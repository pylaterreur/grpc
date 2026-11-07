"""Annotate cygrpc.cpp:LINE references with the Cython source line they came from.

Usage: python ci/cpp_to_pxi.py <cygrpc.cpp> <text file with cygrpc.cpp:N refs>
Cython precedes generated code with comments like
  /* "src/python/grpcio/grpc/_cython/_cygrpc/aio/completion_queue.pyx.pxi":104
"""

import re
import sys

_REF = re.compile(r"cygrpc\.cpp:(\d+)")
_ORIGIN = re.compile(r'^\s*/\* "([^"]+\.(?:pyx|pxi|pxd))":(\d+)')


def main():
    cpp_path, text_path = sys.argv[1], sys.argv[2]
    with open(cpp_path, errors="replace") as f:
        cpp = f.read().splitlines()
    with open(text_path, errors="replace") as f:
        text = f.read()

    cache = {}

    def origin(line_no):
        if line_no in cache:
            return cache[line_no]
        result = "?"
        for index in range(min(line_no, len(cpp)) - 1, max(0, line_no - 400), -1):
            match = _ORIGIN.match(cpp[index])
            if match:
                path = match.group(1).split("grpc/_cython/")[-1]
                result = "%s:%s" % (path, match.group(2))
                break
        cache[line_no] = result
        return result

    print(_REF.sub(lambda m: "cygrpc.cpp:%s [%s]" % (m.group(1), origin(int(m.group(1)))), text))


if __name__ == "__main__":
    main()
