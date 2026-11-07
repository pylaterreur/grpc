"""Report whether importing an extension module keeps the GIL disabled.

Usage: python ci/import_check.py [--module grpc._cython.cygrpc]
"""

import argparse
import importlib
import os
import sys
import sysconfig
import warnings


def _gil_enabled():
    is_enabled = getattr(sys, "_is_gil_enabled", None)
    return is_enabled() if is_enabled is not None else True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", default="grpc._cython.cygrpc")
    args = parser.parse_args()

    print("python:", sys.version.replace("\n", " "))
    print("Py_GIL_DISABLED:", sysconfig.get_config_var("Py_GIL_DISABLED"))
    print("PYTHON_GIL env:", os.environ.get("PYTHON_GIL"))
    print("-X gil:", sys._xoptions.get("gil"))
    print("GIL enabled before import:", _gil_enabled())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module(args.module)
    for warning in caught:
        print("WARNING:", warning.category.__name__, warning.message)
    print("imported:", module.__name__, getattr(module, "__file__", None))
    if args.module.startswith("grpc"):
        try:
            import grpc

            print("grpc version:", grpc.__version__)
        except ImportError:
            pass
    print("GIL enabled after import:", _gil_enabled())


if __name__ == "__main__":
    main()
