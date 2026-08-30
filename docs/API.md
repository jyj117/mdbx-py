# Python API and semantics

`Environment(path, *, flags=0, mode=0o664, max_readers=126, max_dbs=64,
geometry=None, options=None, readonly=False, subdir=True)` opens immediately.
`geometry` is a six-item `(lower, now, upper, grow, shrink, page_size)` sequence;
use `-1` for unchanged values. `options` maps exported `MDBX_opt_*` constants to
integer values.

`env.begin(write=False, parent=None, flags=0)` creates a transaction. `read()`
and `write()` are optimized conveniences. A transaction context commits a clean
exit and aborts an exceptional exit. Read transactions support reset/renew,
refresh, and park/unpark. Passing `flags=MDBX_TXN_RDONLY_PREPARE` creates an
inactive preallocated read handle; call `renew()` before use. A contradictory
`write=True` plus `MDBX_TXN_RDONLY` is rejected. `begin(write=True, parent=parent_txn)` creates an
upstream-supported nested write transaction; the parent is blocked until the
child finishes.

`env.get(key, db=None, default=None)` is the one-shot point-read path. It begins
a fresh read transaction, performs `mdbx_get`, copies the value, and aborts the
transaction before returning, all inside one C call. No `Transaction` object or
reader slot survives the call. The DBI must already be committed. Use an
explicit transaction for multi-key or multi-DBI snapshot consistency.
The warm uncontended path retains the GIL; when a writer is pending or active,
the read begin temporarily releases it so sustained reader loops cannot starve
write progress.

`txn.get(key, db=None, default=None)` returns the default on `MDBX_NOTFOUND`;
`txn[key]` raises `KeyError`. `delete` returns whether a record existed. `put`
returns `False` for `NOOVERWRITE`/`NODUPDATA` conflicts and otherwise `True`.
`replace` returns a safe copy of the previous value or `None`. All other libmdbx
errors become a stable `clibmdbx.Error` subclass. Native errors expose `code`,
`what`, and `reason` attributes in addition to their complete message.

`get_many`, `put_many`, and `delete_many` always cross the Python/C boundary
once. The default path minimizes allocations and retains the GIL. Passing
`detached=True` copies every key/value into native-owned memory before releasing
the GIL once for the complete libmdbx loop. The copy prevents another Python
thread from mutating a shared `bytearray` while libmdbx reads it. Detached calls
raise `BusyError` while a cursor remains open in the transaction or its parent
chain: libmdbx cursor close/rebind and transaction use must never overlap.

Named tables are opened by `txn.open_db(name, flags=0, create=False)` or
`env.open_db(...)`. Creation requires a write transaction. A `Database` handle
is environment-scoped. Repeated opens can share one native DBI, so
`Database.close()` closes only that Python view; the native DBI remains owned by
the environment. This follows upstream's warning that a shared DBI may only be
closed once and must not be closed concurrently with a transaction.
`drop(delete=True)` is the explicit native delete-and-close operation. It is
rejected while an unrelated transaction is live and invalidates every Python
alias for that DBI. `clear()` or `drop(delete=False)` keeps the DBI valid.

Newly created DBIs remain provisional until the outermost write transaction
commits. Aborting that transaction, including an outer transaction after a
nested create committed into it, invalidates every provisional Python handle.

Cursor positioning methods return `(key, value)` or `None`. Iteration begins at
the current record when positioned, otherwise at first, and stays exhausted
until an explicit positioning operation. `items(start=None,
stop=None, limit=0, reverse=False)` performs cursor movement in C and returns a
list of safe byte pairs; `stop` is exclusive.

An active write transaction must be committed or aborted by its owner thread.
If its final Python reference is accidentally released on another thread, the
destructor never performs the upstream-forbidden cross-thread unlock. Instead
it marks the native transaction broken and defers abort to the owner. The next
environment operation on that owner thread reaps it automatically;
`env.reap_orphaned_transactions()` performs and reports the same cleanup
explicitly. A `ResourceWarning` identifies this misuse, and
`env.orphaned_write_transactions` exposes the number still awaiting their real
owner. Owner identity is scoped to a thread lifetime rather than a reusable
numeric thread ID. If an owner exits, new transactions and native environment
operations fail immediately with `BusyError`; discard and reopen the
environment in a fresh process after draining traffic. A writer already waiting
inside libmdbx when the owner exits may be awakened with `PanicError`; this is
the same restart-only fault, not a retryable transaction error. Cross-thread
native abort is never safe.

No MDBX pointer is ever exposed to Python. This is deliberate: a page pointer
can be invalidated by transaction end, cursor movement, renewal, remap, write or
environment close. Copies are predictable and safe. Scalar write methods reject
both `MDBX_RESERVE` and `MDBX_MULTIPLE`: the former returns a mapped writable
pointer, while the latter changes the C argument ABI to a two-`MDBX_val` array.
Passing either through a normal bytes-like API would be unsafe.

Call `clibmdbx.diagnostics()` for wrapper version, embedded engine version,
amalgamation commit, source hashes, compiler, build flags, architecture, PID and
ABI details. `clibmdbx.limits()` reports platform-specific libmdbx database,
transaction, key and value limits plus `system_page_size`,
`system_ram_total_pages`, and `system_ram_available_pages` from the official
`mdbx_get_sysraminfo()` API.
