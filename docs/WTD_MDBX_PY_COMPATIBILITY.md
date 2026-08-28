# wtdcode/mdbx-py compatibility audit

This is an independent compatibility review, not a source-code port. The
audited reference is [`wtdcode/mdbx-py`](https://github.com/wtdcode/mdbx-py)
commit `b67c434a0e5af691fce1c9b37aecd839a2f9c1bb` (2026-07-27), corresponding to
the currently published `libmdbx==0.3.2` ctypes distribution. The exact commit
is recorded so a future reference update cannot silently change the meaning of
“compatible.”

The reference repository is GPL-2.0 and its tests carry an OpenLDAP license
notice. No implementation or test source was copied into this Apache-2.0
project. `test_wtdcode_parity.py` is an original behavioral test suite built
from the observable scenarios below. `test_wtdcode_interop.py` optionally
installs the published package and exercises both embedded engines against the
same on-disk databases, never concurrently.

## Test parity

The reference currently collects 23 tests. Its `test_cursor_bind` returns
before executing any assertion; its HSR test is fully commented out and is not
collected. clibmdbx executes all 23 named scenarios, upgrades the empty cursor
case to a real reset/renew ownership test, and adds two cross-binding tests.

| Reference test | clibmdbx coverage | Stronger or safer assertion |
| --- | --- | --- |
| `test_open` | `test_wtd_open` | Immediate open, path preservation and deterministic close |
| `test_write` | `test_wtd_write` | Commit, safe byte read and exact entry count |
| `test_db_readitem_writeitem` | `test_wtd_db_readitem_writeitem` | Explicit transaction boundary; `txn[key]` preserves `KeyError` semantics |
| `test_db_iter` | `test_wtd_db_iter` | Same 15 tables × 1024 records, deterministic keys, catalog and batch verification after reopen |
| `test_success_close_written_map` | `test_wtd_success_close_written_map` | Verifies the Python DB view closes without unsafe shared DBI close |
| `test_multi_write` | `test_wtd_multi_write` | Same 16 transactions × 1024 records, 2 GiB upper geometry and full reopen verification |
| `test_replace` | `test_wtd_replace` | Old value and committed replacement |
| `test_delete` | `test_wtd_delete` | DUPSORT exact-value deletion followed by all-duplicates deletion |
| `test_env` | `test_wtd_env` | stat/info, path copy, options, limits, forced sync, clear/drop and copied-data verification; no raw fd |
| `test_userctx` | `test_wtd_userctx_is_replaced_by_owned_object_graph` | Confirms raw-pointer APIs are absent and cursor/DB/transaction native owners remain alive |
| `test_txn` | `test_wtd_txn` | reset/renew, active state, monotonic ID and structured transaction info |
| `test_get_build_info` | `test_wtd_get_build_info` | Checks binding, compiler and complete upstream build options |
| `test_get_version_info` | `test_wtd_get_version_info` | Checks exact embedded version and amalgamation commit |
| `test_get_sysram` | `test_wtd_get_sysram` | Checks official system page/RAM values and invariants |
| `test_txnid` | `test_wtd_txnid` | Positive native transaction ID |
| `test_cursor_bind` | `test_wtd_cursor_bind_now_exercises_renew` | Replaces the reference no-op with reset, new read transaction and native cursor renewal |
| `test_cursor_open` | `test_wtd_cursor_open` | Write, set, first/last, next/prev, EOF-as-`None` and delete |
| `test_set_range` | `test_wtd_set_range` | Binary boundary key, exact lower bound and range scan |
| `test_parent_txn` | `test_wtd_parent_txn` | Actually writes in parent and child, commits both and verifies visibility |
| `test_null_bytes` | `test_wtd_null_bytes` | Embedded NUL in DB name, key and value, including catalog and cursor |
| `test_iters` | `test_wtd_iters` | first/last/full/start-key iteration with binary ordered keys |
| `test_iters_dup` | `test_wtd_iters_dup` | Complete flattened and grouped DUPSORT traversal |
| `test_sequence` | `test_wtd_sequence` | Abort/commit persistence plus explicit unsigned-64 overflow exception |

The optional interoperability tests prove both directions:

1. ctypes 0.3.2 creates default and DUPSORT tables and a sequence; clibmdbx
   reads and extends them; ctypes reopens and verifies the extension.
2. clibmdbx creates a NUL-containing named table and sequence; ctypes reads and
   extends it; clibmdbx reopens and verifies the extension.

Run the parity suite without the reference package:

```bash
python -m pytest -q tests/test_wtdcode_parity.py
```

Run the pinned cross-binding suite:

```bash
python -m pip install "libmdbx==0.3.2"
python -m pytest -q tests/test_wtdcode_interop.py -m ctypes_reference
```

## Public API mapping

Names intentionally follow a Pythonic API instead of retaining ctypes-shaped
pointer operations. “Internal ownership” means the capability exists but its
native pointer is never returned to Python.

| ctypes surface | clibmdbx surface or decision |
| --- | --- |
| `Env(...)`, context manager, `close` | `Environment(...)`, context manager, idempotent `close` |
| `start_transaction`, `ro_transaction`, `rw_transaction` | `begin`, `read`, `write` |
| Environment `get`/`items`/mapping dunders | Explicit `Transaction.get`, `txn[key]`, `Cursor.items`; prevents hidden transactions and swallowed errors |
| `get_path`, `get_stat`, `get_info` | `path`, `stat`, `info` |
| `copy` | `copy` with pathname ownership and CPython path conversion |
| `copy2fd`, `get_fd` | Excluded: raw descriptor ownership, offset and close races are unsuitable for a portable high-level API |
| `set_geometry`, `set/get_option`, `sync` | Same capabilities through `set_geometry`, `set_option`, `get_option`, `sync` |
| `get_maxdbs`, `get_maxkeysize`, `get_maxvalsize` | `info` and `limits` return structured current/official limits |
| `get_db_names` | `Transaction.databases` |
| `delete` | `delete_environment`, requiring an explicit pathname and delete mode |
| `register_thread`, `unregister_thread` | Excluded: wrapper enforces transaction owner-thread affinity and does not expose a way to violate it |
| Env/TXN/Cursor safe and integer user-context methods | Excluded: arbitrary `void *` cannot carry a safe Python lifetime; applications retain their own context |
| `set_hsr`, `get_hsr` | Excluded: arbitrary callbacks inside engine critical paths require unresolved reentrancy, GIL and failure policies |
| TXN lifecycle (`commit`, `commit_ex`, `abort`, `reset`, `renew`, `break_txn`) | `commit`, `commit_ex`, `abort`, `reset`, `renew`, `break_` |
| TXN `id`, `get_info`, canary | `id`, `info`, `canary`; plus `flags`, `readonly`, `active`, `gc_info`, `refresh`, park/unpark |
| TXN `get_env` | Internal strong ownership; no native environment pointer escapes |
| `create_map`, `open_map` | `Transaction.open_db(..., create=True/False)` |
| DBI `get`, `put`, `replace`, `delete` | Transaction CRUD with an optional `Database`; additionally native C-loop batch methods |
| DBI `get_stat`, `drop`, `get_sequence` | `Database.stat`, `clear`/`drop`, `sequence`; plus `flags` and `rename` |
| DBI `close` | Closes one Python view only; the environment owns the shared native DBI, avoiding alias double-close |
| Cursor constructor/open | `Transaction.cursor`, which creates an already-owned valid cursor |
| Unbound `Cursor.create`/`bind` | Excluded: an invalid half-constructed cursor state is unnecessary; `renew` safely changes read transactions |
| Cursor pointer access (`txn`, `dbi`) | Internal strong references; pointers never escape |
| Cursor `copy`/`dup` | Open another owned cursor; avoids subtle shared-position iterator ownership |
| Cursor navigation/get/put/delete/count/renew | `first`, `last`, `next`, `prev`, duplicate/range methods, `put`, `delete`, `count`, `renew` |
| Cursor `eof`, `on_first`, `on_last` | Movement returns `None` at EOF; exact records make position tests explicit |
| `iter`, `iter_dupsort`, `iter_dupsort_rows` | Iterator protocol, `items`, and explicit duplicate navigation, all returning safe `bytes` copies |
| `get_build_info`, `get_version_info` | `diagnostics`, `version_info` with binding/source hashes and compile/runtime ABI evidence |

## ctypes declarations versus implemented features

The reference module assigns `argtypes` to 72 `mdbx_*` symbols. That is not
the same as exposing 72 supported Python operations: many are declarations
only, optional legacy attribute APIs, callbacks, raw pointer helpers or
functions reached indirectly by a smaller high-level surface. Handwritten
ctypes declarations also make the Python source responsible for matching the
loaded shared library ABI.

clibmdbx compiles the pinned official `mdbx.c` against the pinned official
`mdbx.h`; it never copies a structure, enum or function declaration. Its
separate [official C API coverage matrix](API_COVERAGE.md) accounts for every
`LIBMDBX_API` declaration in the newer embedded stable release, including
intentional safety exclusions. A source guard checks the declaration count and
hash on every test build.

## Deliberate semantic corrections

- Native errors inherit from `Exception` and map to stable subclasses. The
  reference `MDBXErrorExc` inherits from `BaseException`, which bypasses normal
  `except Exception` cleanup.
- Missing keys have explicit `get(default=...)`, `False`, or `KeyError`
  semantics. Environment mapping does not catch every exception and turn
  corruption, I/O or lifecycle failures into a false “missing” result.
- Transactions and cursors enforce their native owner thread; inherited
  post-fork objects are rejected using PID checks.
- Returned records are owned `bytes`. No mapped page pointer can outlive its
  transaction or be invalidated by remap/cursor movement.
- Potentially blocking or long batch work releases the GIL once at a documented
  boundary; the warm point-get path keeps the GIL and uses CPython FASTCALL.
- Environment close refuses live transactions instead of guessing a safe
  cross-thread close order.

Compatibility means the same durable database behavior and on-disk format, not
preserving unsafe ctypes pointer access. The package is still an alpha until
the hosted wheel matrix and production soak tests complete; no finite test
suite can prove that defects are impossible.
