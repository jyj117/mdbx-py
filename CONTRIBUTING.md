# Contributing

Changes must preserve the central property of this package: the hot path is
hand-written CPython C API code calling the vendored official libmdbx C API.
ctypes, CFFI, Cython, Rust/PyO3 and runtime loading of another libmdbx are not
accepted.

Before submitting a change:

1. Add focused success, boundary, failure and lifetime tests.
2. Run `python -m pytest -q` with strict compiler warnings.
3. Run the ASan/UBSan command in `docs/BUILDING.md` for C changes.
4. Run `mdbx_chk` against any database written by a new storage operation.
5. Update `docs/API_COVERAGE.md` and the public type stub for API changes.
6. Run the benchmark three times for hot-path changes and commit raw JSON only
   when the workload and machine metadata are complete.

Do not commit databases, credentials, organization data, generated wheels, or
private benchmark inputs. All commits must preserve the Apache-2.0 headers and
third-party notices.
