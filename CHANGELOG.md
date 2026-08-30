# Changelog

This project follows Semantic Versioning and PEP 440. Released versions are
immutable.

## 1.0.3 - 2026-08-30

- Replace reusable numeric OS/Python thread identifiers with CPython's unique
  thread-state IDs for every transaction, cursor, writer owner and deferred
  writer cleanup record. This prevents a replacement thread from impersonating
  an exited write owner and attempting an invalid native unlock.
- Treat a write transaction finalized away from an owner that cannot reap it as
  a faulted environment: new transactions and native environment operations now
  fail immediately with `BusyError` instead of waiting forever on libmdbx's
  single-writer lock. Introspection, `reader_check()`, explicit reaping and
  cleanup of already-active transactions remain available.
- Expose `Environment.orphaned_write_transactions` for health monitoring and
  make the `ResourceWarning` state and process-restart recovery explicit.
- Add a binding-level writer gate so queued writers, sync, online
  geometry/flag/option changes and defragmentation remain outside libmdbx and
  are released with metadata-bearing `BusyError` on every supported platform
  when an off-owner finalizer faults the environment. Add cross-platform regression
  coverage that deliberately exits a write-owner thread, churns replacement
  threads, and exercises root and nested orphan recovery, plus quantitative gates for
  writer wait/TRY behavior, busy and clean close latency, `MapFullError` recovery,
  geometry reopen, crashed-reader cleanup, long-reader lag and retained pages.
- Make the deferred-record allocation-failure path permanently fail closed and
  release only the binding gate, so extreme OOM cannot strand queued operations
  or trigger an upstream-forbidden native unlock.
- Add real filesystem-exhaustion and combined production-edge audit scripts for
  release validation. The disk-full probe verifies libmdbx's native error is
  exposed as `DiskError`, separately from the geometry-only `MapFullError`.

## 1.0.2 - 2026-08-30

- Add `Environment.get(key, db=None, default=None)` for warm point lookups. It
  begins, reads, copies, and aborts one short read transaction entirely in C,
  without allocating a Python `Transaction` or retaining thread-affine state.
- Preserve fresh-snapshot and safe-copy semantics, committed-DBI validation,
  fork rejection, reader-limit error mapping, and deterministic close cleanup.
- Prevent tight one-shot reader loops from starving a pending or active writer;
  the uncontended read-only hot path continues to retain the GIL for speed.
- Reject deleting a DBI while an environment-level native operation may still
  reference it, closing a lifecycle race introduced by writer-aware yielding.
- Add single/multi-thread stress, close-race, reader exhaustion, large-value,
  NUL/empty-value, cross-environment DBI, fork, ASan and UBSan coverage.

## 1.0.1 - 2026-08-29

- Initial stable public release of the hand-written CPython C-API binding.
- Start the public version sequence at 1.0.1 by maintainer choice; 1.0.0 and
  all earlier development versions were never published to a package index.
- Embed the official libmdbx 0.14.3 amalgamation.
- Add environment, transaction, DBI and cursor APIs, Python exception mapping,
  C-loop batch primitives, thread affinity and post-fork rejection.
- Add randomized model testing, concurrency, crash recovery, sanitizers,
  `mdbx_chk`, reproducible benchmarks and multi-platform wheel CI.
- Keep repeated database wrappers on one environment-owned native DBI; prevent
  double-close, invalidate aliases after deleting drop, and invalidate handles
  created by an aborted outer or nested transaction.
- Guard GIL-detached environment operations against concurrent close and defer
  accidental cross-thread write finalization to the native owner thread.
- Expand native error subclasses and expose `code`, `what`, and `reason`.
- Add an independently written parity suite for all 23 collected
  wtdcode/mdbx-py test scenarios and pinned two-way on-disk interoperability
  tests against `libmdbx==0.3.2`.
- Expose official system RAM/page diagnostics and raise `OverflowError` for a
  DB sequence increment that would exceed unsigned 64-bit storage.
- Reject unsafe scalar use of native `MDBX_RESERVE` and `MDBX_MULTIPLE`, whose
  pointer/array contracts cannot be represented by the scalar Python methods.
- Harden cursor/transaction lifetime tracking around GIL-detached native calls,
  including writer-owner configuration, nested transactions, cursor renewal,
  cross-thread finalization and copied detached-batch results.
- Reject free-threaded CPython builds until the lifecycle model has been
  redesigned and validated without the GIL.
- Add release-configuration gates for repository/security contacts and test the
  exact sdist from the release workflow before any upload.
