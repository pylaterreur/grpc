"""Run grpcio unit-test modules, one subprocess per module, and summarize.

Modes:
  gil0     -X gil=0 and PYTHON_GIL=0 (also inherited by subprocesses the
           tests spawn): the GIL stays disabled although cygrpc does not
           declare free-threading support.
  default  no override: on a free-threaded build, importing cygrpc
           re-enables the GIL; on a regular build, the GIL is always on.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

_RAN = re.compile(r"^Ran (\d+) tests? in", re.M)
_FAILED = re.compile(r"^FAILED \((.*)\)$", re.M)
_OK = re.compile(r"^OK(?: \((.*)\))?$", re.M)
_TEST_LINE = re.compile(r"^(\S+) \(([\w.]+)\)\s*\.\.\.\s*(ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)$", re.M)
_GIL_WARNING = "has been enabled to load module"


def _counts(text):
    counts = {}
    match = _FAILED.search(text) or _OK.search(text)
    if match and match.group(1):
        for part in match.group(1).split(","):
            key, _, value = part.strip().partition("=")
            if value.isdigit():
                counts[key] = int(value)
    ran = _RAN.search(text)
    counts["ran"] = int(ran.group(1)) if ran else None
    return counts


def _bad_tests(text):
    bad = []
    for _name, qualname, outcome in _TEST_LINE.findall(text):
        if outcome in ("FAIL", "ERROR"):
            bad.append("%s %s" % (qualname, outcome))
    return bad


def run_module(module, args, env, prefix):
    cmd = prefix + ["-m", "unittest", "-v", module]
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=args.tests_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=args.timeout)
        status = None
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        status = "TIMEOUT"
    elapsed = time.monotonic() - start
    text = out.decode("utf-8", "replace")
    rc = proc.returncode
    if status is None:
        if rc == 0:
            status = "PASS"
        elif rc < 0:
            status = "CRASH(%s)" % signal.Signals(-rc).name
        elif rc in (134, 139):
            status = "CRASH(rc=%d)" % rc
        else:
            status = "FAIL"
    counts = _counts(text)
    return {
        "module": module,
        "status": status,
        "rc": rc,
        "seconds": round(elapsed, 1),
        "counts": counts,
        "bad_tests": _bad_tests(text)[:40],
        "gil_warning": _GIL_WARNING in text,
        "fatal": [line for line in text.splitlines() if "Fatal Python error" in line or "Segmentation fault" in line][:5],
    }, text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests-dir", required=True)
    parser.add_argument("--mode", choices=("gil0", "default"), required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", required=True)
    parser.add_argument("--modules-file", required=True)
    args = parser.parse_args()

    with open(args.modules_file) as f:
        modules = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    os.makedirs(args.out, exist_ok=True)
    env = dict(os.environ)
    env.pop("PYTHON_GIL", None)
    prefix = [sys.executable, "-X", "faulthandler"]
    if args.mode == "gil0":
        env["PYTHON_GIL"] = "0"
        prefix += ["-X", "gil=0"]

    results = []
    for module in modules:
        result, text = run_module(module, args, env, prefix)
        results.append(result)
        with open(os.path.join(args.out, module + ".log"), "w") as f:
            f.write(text)
        counts = result["counts"]
        print(
            "%-62s %-16s %6.1fs ran=%s %s%s"
            % (
                module,
                result["status"],
                result["seconds"],
                counts.get("ran"),
                " ".join("%s=%s" % kv for kv in sorted(counts.items()) if kv[0] != "ran"),
                " GIL-WARNING" if result["gil_warning"] else "",
            ),
            flush=True,
        )
        for bad in result["bad_tests"]:
            print("    " + bad, flush=True)
        for fatal in result["fatal"]:
            print("    " + fatal, flush=True)

    with open(os.path.join(args.out, "results.json"), "w") as f:
        json.dump({"mode": args.mode, "python": sys.version, "results": results}, f, indent=1)
    summary = {}
    for result in results:
        key = result["status"].split("(")[0]
        summary[key] = summary.get(key, 0) + 1
    print("SUMMARY mode=%s %s" % (args.mode, json.dumps(summary, sort_keys=True)))
    with open(os.path.join(args.out, "summary.txt"), "w") as f:
        f.write("mode=%s %s\n" % (args.mode, json.dumps(summary, sort_keys=True)))
        for result in results:
            if result["status"] != "PASS":
                f.write("%s %s %s\n" % (result["module"], result["status"], "; ".join(result["bad_tests"][:10])))


if __name__ == "__main__":
    main()
