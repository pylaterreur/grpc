"""Run ci/stress.py scenarios repeatedly in fresh processes and summarize."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

# scenario, threads, n, repetitions
PLAN = (
    ("sync", 16, 300, 3),
    ("churn", 9, 30, 3),
    ("close_race", 8, 30, 3),
    ("registered_close", 2, 1, 20),
    ("aio", 4, 200, 3),
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("gil0", "default"), required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stress.py")
    env = dict(os.environ)
    env.pop("PYTHON_GIL", None)
    prefix = [sys.executable, "-X", "faulthandler"]
    if args.mode == "gil0":
        env["PYTHON_GIL"] = "0"
        prefix += ["-X", "gil=0"]

    summary = []
    for scenario, threads, n, repetitions in PLAN:
        statuses = {}
        results = []
        log_path = os.path.join(args.out, "%s.log" % scenario)
        with open(log_path, "w") as log:
            for rep in range(repetitions):
                cmd = prefix + [script, scenario, "--threads", str(threads), "--n", str(n)]
                start = time.monotonic()
                proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    out, _ = proc.communicate(timeout=args.timeout)
                    status = None
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    out, _ = proc.communicate()
                    status = "TIMEOUT"
                text = out.decode("utf-8", "replace")
                rc = proc.returncode
                if status is None:
                    if rc == 0:
                        status = "OK"
                    elif rc < 0:
                        status = "CRASH(%s)" % signal.Signals(-rc).name
                    else:
                        status = "ERRORS(rc=%d)" % rc
                statuses[status] = statuses.get(status, 0) + 1
                log.write("=== rep %d status %s %.1fs\n%s\n" % (rep, status, time.monotonic() - start, text))
                for line in text.splitlines():
                    if line.startswith("RESULT "):
                        results.append(json.loads(line[len("RESULT "):]))
        line = {"scenario": scenario, "threads": threads, "n": n, "statuses": statuses}
        rates = [r["unary_calls_per_s"] for r in results if "unary_calls_per_s" in r]
        if rates:
            line["unary_calls_per_s"] = rates
        errors = [e for r in results for e in r.get("errors", [])]
        if errors:
            line["sample_errors"] = errors[:5]
        outcomes = [r.get("outcomes") or r.get("outcome") for r in results if r.get("outcomes") or r.get("outcome")]
        if outcomes:
            line["sample_outcomes"] = outcomes[:3]
        summary.append(line)
        print("STRESS mode=%s %s" % (args.mode, json.dumps(line, sort_keys=True)), flush=True)
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump({"mode": args.mode, "python": sys.version, "summary": summary}, f, indent=1)


if __name__ == "__main__":
    main()
