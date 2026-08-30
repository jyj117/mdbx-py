# clibmdbx

`clibmdbx` is an independent, high-performance CPython binding for
[libmdbx](https://github.com/Mithril-mine/libmdbx). Both the distribution and
import name are `clibmdbx`. It is a community project and is not an official
Mithril-mine/libmdbx package.

The hot path is hand-written CPython C API code calling the libmdbx C API
directly. The extension embeds the official libmdbx 0.14.3 amalgamation, so a
wheel does not load a system `libmdbx` and has no runtime Python dependencies.
It does not use ctypes, CFFI, Cython, Rust, or a helper service.

> **Stable release:** 1.0.2 deliberately uses a CPython-version-specific ABI
> for maximum hot-path performance. Public API compatibility follows Semantic
> Versioning within the 1.x line.

## Quick start

```python
import clibmdbx

with clibmdbx.Environment("cache.mdbx", max_dbs=8) as env:
    with env.write() as txn:
        txn.put(b"answer", b"42")

    with env.read() as txn:
        assert txn.get(b"answer") == b"42"
        assert txn[b"answer"] == b"42"
```

Named databases, DUPSORT, cursors and batches use the same transaction model:

```python
with env.write() as txn:
    events = txn.open_db(b"events", flags=clibmdbx.MDBX_DUPSORT, create=True)
    txn.put_many([(b"job", b"started"), (b"job", b"finished")], db=events)

with env.read() as txn, txn.cursor(events) as cursor:
    print(cursor.set(b"job"))
    print(cursor.count())
```

All keys and values accept `bytes` or a contiguous bytes-like object. Returned
data is copied to safe `bytes`. `MDBX_RESERVE` and borrowed zero-copy views are
not exposed because their native pointers can outlive neither the write call nor
the mapped transaction safely.

For a single warm lookup, `env.get(key, db=None, default=None)` performs the
complete short read transaction in one C call and returns a safe copy. It does
not retain a transaction between calls. Use an explicit read transaction and
`get_many()` when several values must share one snapshot.

## Concurrency and lifetime rules

- An `Environment` may be shared by threads. libmdbx still permits only one
  write transaction at a time per environment.
- `Environment.get()` keeps the GIL on the uncontended warm-read path. While a
  writer is pending or active it yields during transaction begin, preventing
  tight reader loops from starving write progress.
- A `Transaction` and every cursor belonging to it are bound to the thread that
  created the transaction. Cross-thread use raises `ThreadError` before calling
  libmdbx.
- Transactions keep their environment alive; cursors and DB handles keep their
  owners alive. Closing an environment with active transactions is rejected.
  A `Database.close()` closes only that Python view because repeated opens may
  share one native DBI; ordinary native DBIs remain environment-owned, avoiding
  upstream's double-close/concurrent-close corruption hazard. Repeated
  `close()`/`abort()` is safe.
- A process created with `fork()` must open a fresh environment. Inherited
  objects raise `ForkError`; their destructors intentionally do not call into
  the parent's native handles.
- The current single-phase high-performance extension supports CPython's main
  interpreter only. Import in a subinterpreter raises `ImportError` explicitly
  rather than sharing interpreter-owned exception/type objects unsafely. Use a
  separate process when interpreter isolation is required.
- `commit`, `sync`, `copy`, defragmentation and warm-up release the GIL once
  around the native operation. A commit with an open cursor retains the GIL
  because libmdbx updates the transaction's cursor list while committing; close
  cursors first when concurrent Python progress during commit matters. Warm
  point reads keep the GIL to avoid a costly
  release/reacquire on every key. Batch methods cross Python/C once and loop in
  C. Their default fast path retains the GIL; `detached=True` first copies every
  input, then releases the GIL exactly once around the native loop. Use detached
  mode for cold pages, large batches or latency-sensitive RPC thread pools.
  Detached mode requires all cursors in the transaction and parent chain to be
  closed, preventing cross-thread cursor finalization from racing native work.
- The wrapper always enables native `MDBX_NOSTICKYTHREADS`, then applies the
  stricter Python owner-thread checks itself. Read transactions can therefore
  be finalized safely by a different Python worker. libmdbx still requires a
  write transaction to finish on its creator OS thread: an accidental
  cross-thread final reference is marked broken and queued, never unlocked on
  the wrong thread. Its owner reaps it automatically on the next environment
  operation or explicitly with `env.reap_orphaned_transactions()`.
- `drop(delete=True)` is rejected while an unrelated transaction may still
  reference the shared DBI. It invalidates all duplicate Python aliases after
  the native delete-and-close succeeds. Creation rolled back at any nesting
  level likewise invalidates its provisional aliases.

Durability defaults are upstream libmdbx defaults. Unsafe modes such as
`SAFE_NOSYNC`, `UTTERLY_NOSYNC`, `WRITEMAP`, and `NOMEMINIT` are opt-in constants;
the binding never enables them silently.

## Major APIs

- `Environment`: one-shot point get, geometry, options, flags, sync, consistent
  copy, online defragmentation, warm-up, reader cleanup, statistics and
  information.
- `Transaction`: read/write/nested transactions, commit timing, abort,
  reset/renew, refresh, park/unpark, canary, GC information, DB enumeration,
  CRUD, replace and batch operations.
- `Database`: flags/statistics, sequence, clear/drop, close and rename.
- `Cursor`: exact/range positioning, forward/reverse and DUPSORT traversal,
  count, write/delete/renew, iterator protocol and bounded range collection.
- Stable exception classes for common libmdbx failures, plus `ThreadError`,
  `ForkError`, and `ClosedError` for wrapper safety checks. Native exceptions
  include `code`, `what`, and `reason`, matching the diagnostic ergonomics
  expected from mature LMDB bindings.

See [the complete C API coverage matrix](docs/API_COVERAGE.md),
[the API/lifecycle guide](docs/API.md),
[the wtdcode/mdbx-py ctypes compatibility audit](docs/WTD_MDBX_PY_COMPATIBILITY.md),
[the python-lmdb comparison](docs/LMDB_COMPATIBILITY.md), and
[the benchmark method](benchmarks/README.md).

## Build and verify from source

A C11 compiler and CPython development headers are required. Builds use the
vendored source and perform no network access.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip build pytest twine
python -m pip install -e .
python -m pytest -q
python -m build
python -m twine check dist/*
```

Strict warnings are enabled by default. Sanitizer builds are supported on
Clang/GCC:

```bash
CLIBMDBX_SANITIZE=address,undefined python -m pip install -e . --no-build-isolation
ASAN_OPTIONS=detect_leaks=0:abort_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1 PYTHONMALLOC=malloc python -m pytest -q
```

For source provenance, wheel policy, release commands, and supported-platform
evidence, see [VENDORING.md](VENDORING.md), [docs/BUILDING.md](docs/BUILDING.md),
and [RELEASING.md](RELEASING.md).

## Embedded source and license

This source tree pins official libmdbx **0.14.3**, upstream release tag
`v0.14.3`. The release tag resolves to commit
`f7a3a9323cacacfa9dc6137ae7a7252a67744ff0`; the official amalgamation records
source commit `251562b2dc55266d8e6d0e6627ec88ecb410702f`.
The official amalgamation archive SHA-256 is
`dbc4a791c44d3e51a8159eedfee0dedada7b21d46c22588f0fa99294983f33cd`.

The wrapper and libmdbx are Apache-2.0 licensed. The sdist and wheels retain the
wrapper and upstream `LICENSE`, `NOTICE`, and `COPYRIGHT`; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [SBOM.cdx.json](SBOM.cdx.json).
