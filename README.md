# grpcio free-threading probe (throwaway branch)

This orphan branch only holds a GitHub Actions workflow and helper scripts.
It builds grpcio 1.85.1 from the PyPI sdist on free-threaded CPython
(3.13t, 3.14t, 3.15t), then checks:

- whether importing `grpc._cython.cygrpc` re-enables the GIL;
- the grpcio unit tests (v1.85.1 tree) with the GIL forced off
  (`-X gil=0`, `PYTHON_GIL=0`) and in the default mode;
- multi-threaded stress scenarios (`ci/stress.py`).

It is an investigation aid on a personal fork, not a proposed change.
