"""Group ThreadSanitizer reports (log_path files) by their first two frames.

Usage: python ci/tsan_summary.py <dir-with-tsan.*-files> [--out summary.txt]
"""

import argparse
import collections
import os
import re

_HEADER = re.compile(r"^WARNING: ThreadSanitizer: (.+?)(?: \(pid=\d+\))?$")
_FRAME = re.compile(r"^\s+#(\d+) (\S+) (.*)$")


_LOCATION = re.compile(r"(\S+\.(?:c|cc|cpp|h|hpp|pxi|pyx|inc)):(\d+)")


def _short(frame_text):
    # "__pyx_f_..._foo(args) /path/to/file.cpp:123:45 (cygrpc.so+0x...)"
    func = frame_text.split("(")[0].split(" ")[0]
    match = _LOCATION.search(frame_text)
    location = "%s:%s" % (match.group(1), match.group(2)) if match else frame_text.split(" (")[-1]
    location = re.sub(r"^.*/(src/python/|src/core/|third_party/|Python/|Objects/|Modules/|Include/)", r"\1", location)
    return "%s %s" % (func, location)


def parse_reports(path):
    reports = []
    with open(path, errors="replace") as f:
        lines = f.read().splitlines()
    i = 0
    while i < len(lines):
        match = _HEADER.match(lines[i])
        if not match:
            i += 1
            continue
        kind = match.group(1)
        sections = []
        current = None
        i += 1
        while i < len(lines) and not lines[i].startswith("SUMMARY: ThreadSanitizer"):
            line = lines[i]
            frame = _FRAME.match(line)
            if frame:
                if current is not None and len(current[1]) < 6:
                    current[1].append(_short(frame.group(2) + " " + frame.group(3)))
            elif re.match(r"^  \S", line):
                current = (line.strip(), [])
                sections.append(current)
            i += 1
        reports.append((kind, sections))
        i += 1
    return reports


def _key(kind, sections):
    stacks = [frames for title, frames in sections if frames][:2]
    tops = []
    for frames in stacks:
        interesting = [f for f in frames if "cygrpc" in f or "__pyx" in f or "src/python" in f or "src/core" in f]
        tops.append((interesting or frames)[0])
    return (kind,) + tuple(tops)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--out")
    args = parser.parse_args()
    groups = collections.Counter()
    examples = {}
    files = 0
    for name in sorted(os.listdir(args.directory)):
        if not name.startswith("tsan"):
            continue
        files += 1
        for kind, sections in parse_reports(os.path.join(args.directory, name)):
            key = _key(kind, sections)
            groups[key] += 1
            examples.setdefault(key, (name, sections))
    out = ["files with reports: %d, distinct report groups: %d" % (files, len(groups))]
    for key, count in groups.most_common():
        out.append("")
        out.append("[%d] %s" % (count, key[0]))
        for top in key[1:]:
            out.append("    top: %s" % top)
        name, sections = examples[key]
        out.append("    example: %s" % name)
        for title, frames in sections[:4]:
            out.append("      %s" % title)
            for frame in frames[:6]:
                out.append("        %s" % frame)
    text = "\n".join(out)
    print(text)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
