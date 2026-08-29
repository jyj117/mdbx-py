# python-lmdb comparison and migration notes

The quality target is the mature `python-lmdb` binding: predictable ownership,
Pythonic not-found semantics, context managers, efficient cursor traversal,
specific exceptions, type information, self-contained wheels, and explicit
thread/process rules. `clibmdbx` is not a drop-in API clone: libmdbx has
different transaction, DBI, geometry, maintenance, and durability facilities.

The comparison baseline reviewed for this release is `python-lmdb` 2.3.0.

| Concern | python-lmdb pattern | clibmdbx pattern |
| --- | --- | --- |
| Open environment | `lmdb.open(...)` / `lmdb.Environment(...)` | `clibmdbx.open(...)` / `clibmdbx.Environment(...)` |
| Read/write scope | `env.begin(write=...)` context | `env.begin(write=...)`, `env.read()`, `env.write()` contexts |
| Named database | `env.open_db(...)` | `env.open_db(...)` or transaction-scoped `txn.open_db(...)` |
| Missing key | `txn.get()` returns `None`/default; indexing raises | Same |
| Write conflicts | `put()` returns `False` | Same for `NOOVERWRITE`/`NODUPDATA` |
| Returned storage | bytes, or opt-in transaction-bound buffers | Safe copied `bytes` only; no dangling mapped views |
| Cursor positioning | boolean plus separate `key()`/`value()` APIs | `(key, value)` or `None` in one native call |
| Cursor iteration | separate iterator objects | cursor iterator plus bounded `items()` materialization |
| Error diagnostics | specific subclasses with code/reason | specific subclasses with `code`, `what`, and `reason` |
| DB handle close | environment-managed/cached | Python view close; shared native DBI remains environment-owned |
| Async helper | optional executor-backed `lmdb.aio` | deliberately not bundled; callers own executor/backpressure policy |
| Engine-specific management | LMDB map sizing/readers/copy | libmdbx geometry, options, canary, park/unpark, warmup, defrag, GC info |

Important migration differences:

- Cursor positioning returns a pair instead of a boolean. Use `is not None` if
  only success matters.
- `Database.close()` does not call `mdbx_dbi_close()`. Repeated opens share a
  DBI, and upstream documents individual/concurrent close as unsafe. Use
  `drop(delete=True)` to delete a table; it requires no unrelated live
  transaction and invalidates every alias.
- There is no `buffers=True`. Copies are the default correctness contract and
  eliminate page-lifetime use-after-free classes. A future zero-copy API would
  require a dedicated owner object and will only be additive.
- No background thread or implicit async executor is created. In gRPC services,
  begin and finish each transaction inside the same worker invocation. Share
  the environment, not transactions or cursors.
- After `fork()`, discard every inherited object and open a fresh environment.
- CPython subinterpreters are intentionally rejected in the current release.
  Use a separate process; this avoids unsafe process-global Python
  type/exception state until a future multi-phase module conversion is fully
  benchmarked.

Before production rollout, run the wheel smoke test and application workload on
the exact CPython/platform pair, exercise shutdown while requests drain, use
bounded write batches, monitor long-lived reader lag, and keep unsafe durability
flags opt-in. See `API.md`, `BUILDING.md`, and `../RELEASING.md` for the concrete
commands and release gates.
