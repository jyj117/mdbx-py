# Production operations

This runbook describes the failure and capacity rules that matter for a
long-running service. They follow libmdbx 0.14.3's single-writer, MVCC reader
table, mapping and transaction-owner contracts. `clibmdbx` does not add a
background transaction manager.

## Writer ownership and bounded waits

An environment has one native writer. A normal `env.write()` waits until the
current writer finishes and releases the GIL while waiting. Do not hold a
resource that the current writer needs while starting another writer; that is
an application dependency cycle, not a lock libmdbx can resolve. Use
`env.begin(write=True, flags=MDBX_TXN_TRY)` when a request must fail immediately
instead of queueing.

Always create, commit or abort, and release a write transaction in the same
thread callback. Do not put a live transaction into a queue, future, async task
or object graph whose final reference may move to another worker.

If an unfinished writer is finalized on another thread, clibmdbx emits
`ResourceWarning`, marks it broken and queues it for its real owner. Numeric
thread ID reuse cannot authorize cleanup: ownership is a unique token for that
thread lifetime. The owner can call `env.reap_orphaned_transactions()` or cause
automatic reaping with its next environment/transaction operation.

Treat any of the following as a production fault:

- a `ResourceWarning` containing `write transaction was finalized`;
- `env.orphaned_write_transactions > 0`;
- `BusyError` saying that the environment is faulted by a write transaction
  finalized on a non-owner thread;
- `PanicError` returned to a writer that was already waiting inside libmdbx
  when the abandoned owner's OS thread exited.

If the owner thread has exited, no remaining thread is allowed to unlock that
writer. New transactions and native environment operations fail fast rather
than waiting forever. A writer already waiting before the fault is detected is
woken by libmdbx's abandoned-owner handling and may receive `PanicError`; it
must not be retried on the same environment. Stop accepting work, let already-active read
transactions finish, retain diagnostics, and restart the process. A fresh
process/environment discards the abandoned uncommitted transaction during
normal MDBX recovery. Never call a private C function or use a thread-ID reuse
trick to abort it.

In production, make this normally ignored warning visible, for example:

```python
import logging
import warnings

logging.captureWarnings(True)
warnings.filterwarnings(
    "always",
    message=".*write transaction was finalized.*",
    category=ResourceWarning,
    module=".*",
)
```

Because finalizer exceptions are unraisable by Python design, converting this
warning to an exception cannot reliably throw it back into the worker's request
handler. Route the captured warning and the environment property to a
fatal-health path; tests should also fail on unraisable exceptions.

## Geometry, map usage and disk space

`MapFullError` means the transaction reached the configured geometry `upper`
bound. It does **not** mean the filesystem is full. Abort that transaction; its
partial changes are not committed. Increase capacity only with a new
transaction.

Plan a generous `upper` value before all processes open the database. It is a
virtual mapping limit, not immediate physical disk allocation. Online
`set_geometry()` may return `UnableExtendMapError` when the existing address
range cannot be extended; this is explicitly allowed by libmdbx and is common
on constrained mappings. Coordinate all users, close every environment and
reopen with the larger upper value. Do not assume an online increase will
always work on Windows or POSIX.

Useful values from `env.info()` are:

- logical live-page bytes: approximately `(last_pgno + 1) * page_size`;
- current database file bytes: `file_size`;
- filesystem-allocated bytes: `file_allocated`;
- virtual map limit: `geometry["upper"]` (and `map_size` for the current
  mapping);
- unsynchronized volume: `unsynced_bytes`.

Alert separately on logical/map headroom and filesystem free space. A practical
policy reserves enough filesystem space for peak copy-on-write growth, a long
reader window, MDBX metadata/GC work, backups and unrelated files. A real write
or sync failure caused by storage exhaustion is raised as `DiskError`; retain
its `code`, `what` and `reason`. Free/reserve storage, confirm filesystem and
device health, reopen if required, and validate with `mdbx_chk` before restoring
traffic. Never reinterpret `MapFullError` as a disk error or automatically
enable unsafe durability flags.

## Reader slots and stale readers

`max_readers` requests the shared reader-table size before open and has an
effect only for the first process opening that database/lock file; later
processes use the existing shared table. Treat `env.info()["max_readers"]` as
the authoritative effective capacity rather than assuming every opener can
resize it.
`ReadersFullError` means no slot is available. Every read transaction must be
ended deterministically with a context manager or `abort()`. `reset()` releases
the MVCC snapshot but intentionally retains the transaction handle and its
reader slot for later `renew()`; abort the handle to release the slot.

`env.reader_check()` removes stale slots belonging to processes that no longer
exist according to libmdbx's reader-table rules and returns the number cleared.
It does not cancel a valid slow reader, and an abandoned reader whose process is
still alive is not something to assume it can repair. It must not be used as a
substitute for closing application transactions. Run it at startup and
periodically when multiple processes may crash, record non-zero results, and
alert on sustained reader-table occupancy or repeated `ReadersFullError`.

## Long readers and retained pages

An active reader keeps its original MVCC snapshot. Writers still commit, but
pages retired after that snapshot cannot be reused and the database file can
grow toward the geometry upper bound.

For a known transaction, `txn.info(scan_readers=True)` provides the actionable
live metrics:

- `reader_lag`: commits since its snapshot;
- `space_retired`: bytes retired since that snapshot and therefore retained by
  the reader;
- `space_leftover`: writer headroom before slow-reader handling becomes
  necessary.

At environment scope, `recent_txnid - latter_reader_txnid` is the oldest active
reader lag across processes; the corresponding `self_` field limits this view
to the current process. Alert on both age and retained bytes, because a reader
with a small commit lag can still pin many rewritten pages.

`Transaction.gc_info()` reports current GC page counts. In release wheels,
`diagnostics()["build"]["options"]` currently contains `PROFGC=0`; therefore
the profiling-only `max_reader_lag` and `max_retained_pages` fields are expected
to be zero and must not be used as live alarms. Use the transaction information
above and compare `pages_gc` with `pages_reclaimable` instead.

Use short read contexts in RPC handlers. For intentional long scans, bound
their duration/record count, consider `park()` where its semantics fit, and
cancel/restart scans that exceed the service retention budget.

## Shutdown and close latency

Shutdown order is: stop admission, cancel or finish RPC work, commit/abort every
owner-thread transaction, close cursors, verify
`orphaned_write_transactions == 0`, optionally `sync(force=True)`, then close
the environment. `close()` is idempotent and fails quickly with `BusyError`
while transactions or native operations are active; it does not silently tear
their pointers down.

A clean `close(dont_sync=False)` may perform durable I/O and its latency depends
on `unsynced_bytes`, filesystem and device behavior. Measure it under the real
durability mode and storage workload. `dont_sync=True` is not a generic latency
optimization; use it only when the caller has deliberately accepted the
upstream durability consequences.

## Release-gate probes

The repository keeps two standalone checks for disposable environments:

```bash
python scripts/audit_production_edges.py
python scripts/probe_disk_full.py /path/on/a/strictly-limited-filesystem
```

The first records writer wait/TRY and close latency, map growth/reopen behavior,
reader exhaustion and stale cleanup, long-reader retention and orphaned-writer
fail-fast recovery as JSON. The second must be run inside a disposable quota,
loopback or tmpfs/container mount and writes until the real filesystem rejects
I/O. Never point either command at production data.
