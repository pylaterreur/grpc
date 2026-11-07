"""Multi-threaded grpcio stress scenarios for free-threaded CPython.

Usage: python ci/stress.py <scenario> [--threads N] [--n N]
Prints one JSON line with the result; exits 1 on unexpected errors.
"""

import argparse
import asyncio
import collections
import json
import sys
import threading
import time
from concurrent import futures

import grpc

SERVICE = "probe.Echo"
UNARY = "/%s/Unary" % SERVICE
STREAM = "/%s/Stream" % SERVICE


def _gil_enabled():
    is_enabled = getattr(sys, "_is_gil_enabled", None)
    return is_enabled() if is_enabled is not None else True


def _unary(request, context):
    return request


def _stream(request_iterator, context):
    for request in request_iterator:
        yield request


def _make_server(workers=32):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=workers))
    handler = grpc.method_handlers_generic_handler(
        SERVICE,
        {
            "Unary": grpc.unary_unary_rpc_method_handler(_unary),
            "Stream": grpc.stream_stream_rpc_method_handler(_stream),
        },
    )
    server.add_generic_rpc_handlers((handler,))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


class _Errors:
    def __init__(self):
        self._lock = threading.Lock()
        self.items = []

    def add(self, item):
        with self._lock:
            self.items.append(item)


def _run_threads(target, count):
    threads = [threading.Thread(target=target, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def scenario_sync(threads, n):
    """Many threads share one channel: unary, future and streaming calls."""
    server, port = _make_server()
    errors = _Errors()
    with grpc.insecure_channel("127.0.0.1:%d" % port) as channel:
        unary = channel.unary_unary(UNARY, _registered_method=True)
        stream = channel.stream_stream(STREAM, _registered_method=True)
        barrier = threading.Barrier(threads)

        def worker(i):
            barrier.wait()
            try:
                for j in range(n):
                    payload = b"%d-%d" % (i, j)
                    if unary(payload, timeout=60) != payload:
                        errors.add("unary mismatch")
                    if j % 10 == 0:
                        if unary.future(payload, timeout=60).result() != payload:
                            errors.add("future mismatch")
                    if j % 25 == 0:
                        if list(stream(iter([payload] * 5), timeout=60)) != [payload] * 5:
                            errors.add("stream mismatch")
            except Exception as e:  # pylint: disable=broad-except
                errors.add("%s: %s" % (type(e).__name__, str(e)[:200]))

        start = time.perf_counter()
        _run_threads(worker, threads)
        elapsed = time.perf_counter() - start
    server.stop(None)
    calls = threads * n
    return {
        "calls": calls,
        "seconds": round(elapsed, 3),
        "unary_calls_per_s": round(calls / elapsed, 1),
        "errors": errors.items[:20],
        "n_errors": len(errors.items),
    }


def scenario_churn(threads, n):
    """Concurrent channel and server creation/destruction."""
    server, port = _make_server()
    errors = _Errors()

    def channel_worker(i):
        try:
            for _ in range(n):
                channel = grpc.insecure_channel("127.0.0.1:%d" % port)
                try:
                    if channel.unary_unary(UNARY)(b"x", timeout=60) != b"x":
                        errors.add("mismatch")
                finally:
                    channel.close()
        except Exception as e:  # pylint: disable=broad-except
            errors.add("channel %s: %s" % (type(e).__name__, str(e)[:200]))

    def server_worker(i):
        try:
            for _ in range(max(1, n // 5)):
                inner, inner_port = _make_server(workers=4)
                with grpc.insecure_channel("127.0.0.1:%d" % inner_port) as channel:
                    channel.unary_unary(UNARY)(b"y", timeout=60)
                inner.stop(None).wait()
        except Exception as e:  # pylint: disable=broad-except
            errors.add("server %s: %s" % (type(e).__name__, str(e)[:200]))

    def worker(i):
        if i % 3 == 2:
            server_worker(i)
        else:
            channel_worker(i)

    start = time.perf_counter()
    _run_threads(worker, threads)
    elapsed = time.perf_counter() - start
    server.stop(None)
    return {"seconds": round(elapsed, 3), "errors": errors.items[:20], "n_errors": len(errors.items)}


def scenario_close_race(threads, n):
    """Close a channel while other threads are calling on it."""
    server, port = _make_server()
    errors = _Errors()
    seen = collections.Counter()
    seen_lock = threading.Lock()
    expected_codes = (grpc.StatusCode.CANCELLED, grpc.StatusCode.UNAVAILABLE)
    start = time.perf_counter()
    for _ in range(n):
        channel = grpc.insecure_channel("127.0.0.1:%d" % port)
        unary = channel.unary_unary(UNARY)
        stop = threading.Event()

        def worker(i):
            while not stop.is_set():
                try:
                    unary(b"z", timeout=60)
                except ValueError:
                    key = "ValueError(closed)"
                except grpc.RpcError as e:
                    code = e.code()
                    key = str(code)
                    if code not in expected_codes:
                        errors.add("unexpected status %s" % code)
                except Exception as e:  # pylint: disable=broad-except
                    key = type(e).__name__
                    errors.add("%s: %s" % (type(e).__name__, str(e)[:200]))
                else:
                    continue
                with seen_lock:
                    seen[key] += 1
                return

        workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
        for thread in workers:
            thread.start()
        time.sleep(0.05)
        channel.close()
        stop.set()
        for thread in workers:
            thread.join()
    elapsed = time.perf_counter() - start
    server.stop(None)
    return {"seconds": round(elapsed, 3), "outcomes": dict(seen), "errors": errors.items[:20], "n_errors": len(errors.items)}


def scenario_registered_close(threads, n):
    """Reproducer from grpc/grpc#43278 (review by espressolee).

    Registering a call handle races with channel.close(); with the GIL
    disabled, this crashed in grpc_core::Channel::RegisterCall.
    """
    sys.setswitchinterval(1000.0)
    channel = grpc.insecure_channel("localhost:1")
    barrier = threading.Barrier(2)
    registration_started = threading.Event()
    method = "/service/" + ("m" * 100_000)
    outcome = {}

    def register(_):
        barrier.wait()
        registration_started.set()
        try:
            channel.unary_unary(method, _registered_method=True)
            outcome["register"] = "ok"
        except Exception as e:  # pylint: disable=broad-except
            outcome["register"] = type(e).__name__

    def close(_):
        barrier.wait()
        registration_started.wait()
        channel.close()

    workers = [threading.Thread(target=register, args=(0,)), threading.Thread(target=close, args=(1,))]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    return {"outcome": outcome, "errors": [], "n_errors": 0}


async def _aunary(request, context):
    return request


def scenario_aio(threads, n):
    """One asyncio event loop per thread, each with its own server/channel."""
    from grpc import aio

    errors = _Errors()

    async def run_one(i):
        server = aio.server()
        server.add_generic_rpc_handlers(
            (grpc.method_handlers_generic_handler(SERVICE, {"Unary": grpc.unary_unary_rpc_method_handler(_aunary)}),)
        )
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        try:
            async with aio.insecure_channel("127.0.0.1:%d" % port) as channel:
                unary = channel.unary_unary(UNARY)
                payloads = [b"%d-%d" % (i, j) for j in range(n)]
                results = await asyncio.gather(*(unary(p, timeout=60) for p in payloads))
                return sum(1 for p, r in zip(payloads, results) if p != r)
        finally:
            await server.stop(None)

    def worker(i):
        try:
            bad = asyncio.run(run_one(i))
            if bad:
                errors.add("%d mismatches" % bad)
        except Exception as e:  # pylint: disable=broad-except
            errors.add("%s: %s" % (type(e).__name__, str(e)[:200]))

    start = time.perf_counter()
    _run_threads(worker, threads)
    elapsed = time.perf_counter() - start
    return {"seconds": round(elapsed, 3), "errors": errors.items[:20], "n_errors": len(errors.items)}


SCENARIOS = {
    "sync": scenario_sync,
    "churn": scenario_churn,
    "close_race": scenario_close_race,
    "registered_close": scenario_registered_close,
    "aio": scenario_aio,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--n", type=int, default=100)
    args = parser.parse_args()
    result = SCENARIOS[args.scenario](args.threads, args.n)
    result.update(
        scenario=args.scenario,
        threads=args.threads,
        n=args.n,
        gil_enabled_at_end=_gil_enabled(),
        grpc_version=grpc.__version__,
    )
    print("RESULT " + json.dumps(result, sort_keys=True), flush=True)
    sys.exit(1 if result["n_errors"] else 0)


if __name__ == "__main__":
    main()
