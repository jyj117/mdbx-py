# Official libmdbx 0.14.3 C API coverage

This matrix accounts for all 171 `LIBMDBX_API` function declarations in the
vendored stable header. “Wrapped” means the Python API calls that function or a
strictly more general sibling directly. “Represented” means the capability is
implemented safely through another public libmdbx primitive. “Excluded” is an
intentional boundary, not an accidental omission.

| C API family (all functions in vendored `mdbx.h`) | Status | Python surface or reason |
| --- | --- | --- |
| `mdbx_env_create`, `open`, `close_ex`, `delete`, `set/get_option`, `set_geometry`, `set/get_flags`, `get_path` | Wrapped | `Environment`, `delete_environment`, properties/options/geometry |
| Wide-path `*_openW`, `*_deleteW`, `*_copyW`, `*_pathnameW`, `*_recoveryW`, `*_snapinfoW` | Represented | CPython path conversion selects the native path encoding; no duplicate `W` API |
| `mdbx_env_copy`, `mdbx_txn_copy2pathname`, `mdbx_env_copy2fd`, `mdbx_txn_copy2fd` | Partly wrapped | `Environment.copy`; transaction-scoped/fd variants excluded until a Python fd ownership contract is finalized |
| `mdbx_env_stat_ex`, `mdbx_env_info_ex`, `mdbx_env_sync_ex`, `mdbx_env_warmup`, `mdbx_env_defrag` | Wrapped | `stat`, `info`, `sync`, `warmup`, `defrag` |
| `mdbx_is_readahead_reasonable`, all `mdbx_limits_*`, `mdbx_default_pagesize`, `mdbx_get_sysraminfo`, `mdbx_env_get_max*` | Wrapped/represented | `readahead_reasonable`, `limits` (including system RAM/page data); legacy or redundant env-specific variants are collapsed |
| `mdbx_txn_begin_ex`, `info`, `env`, `flags`, `id`, `commit_ex`, `abort_ex`, `break`, `reset`, `park`, `unpark`, `renew`, `refresh` | Wrapped | `Environment.begin`; `Transaction` lifecycle/properties |
| `mdbx_txn_clone` | Excluded | Native clean-snapshot cloning needs explicit parent/DBI/cursor lifetime semantics; open another read transaction today |
| `mdbx_txn_checkpoint`, `commit_embark_read`, `amend`, `rollback` | Excluded | Upstream marks these lifecycle APIs as subject to incompatible change; the binding keeps commit/abort semantics unambiguous |
| `mdbx_canary_put/get`, `mdbx_gc_info` | Wrapped | `Transaction.canary`, `gc_info` |
| `mdbx_dbi_open`, `open2`, `open_ex`, `open_ex2` | Wrapped/represented | `Transaction.open_db`; native comparators/callback contexts intentionally not accepted |
| `mdbx_dbi_rename`, `rename2`, `enumerate_tables`, `stat`, `flags_ex`, `close`, `drop`, `sequence` | Wrapped/represented | `Database` methods and `Transaction.databases`. Individual wrappers deliberately do not call unsafe `mdbx_dbi_close`; normal DBIs live until environment close, while `drop(delete=True)` performs the native delete-and-close after an exclusivity check and invalidates all aliases |
| Key encoding conversions (`mdbx_key_from_*`, `*_from_key`) | Excluded | Pure encoding helpers are reproducible in Python; exposing native-endian scalar formats by default is error-prone |
| `mdbx_dbi_dupsort_depthmask` | Excluded | Low-level page-layout diagnostic, not a table operation |
| `mdbx_get`, `get_ex`, `get_equal_or_great`, `put`, `replace`, `replace_ex`, `del` | Wrapped/represented | `Environment.get` one-shot reads and `Transaction` CRUD/replace; range lookup via cursor. Replace is atomic get+put in the same write transaction. Scalar writes reject `MDBX_RESERVE` and ABI-changing `MDBX_MULTIPLE`; neither can be passed safely through a one-value bytes API |
| `mdbx_cache_get*` | Excluded | Experimental thread-local cache API would hide mutable native pointer lifetimes |
| Cursor create/bind/open/close/renew/reset/unbind/txn/dbi/copy | Wrapped/represented | `Transaction.cursor`, `Cursor.close/renew`; Python object ownership replaces raw bind/user context |
| `mdbx_cursor_get`, `put`, `del`, `count`, state tests | Wrapped | Positioning, DUPSORT traversal, CRUD, count and iterator methods |
| `mdbx_cursor_scan*`, `get_batch`, `delete_range`, `distance`, `scroll`, `distribute`, `bunch_delete`, `count_ex` | Partly represented | `Cursor.items`, batch transaction calls, iteration; specialized unstable/zero-copy or layout-sensitive bulk interfaces excluded pending safe result ownership |
| `mdbx_estimate_distance/move/range` | Excluded | Query-planning estimates are not needed for key-value correctness; candidates for a later additive API |
| `mdbx_is_dirty`, `cmp/dcmp`, `get_keycmp/get_datacmp` | Represented | Copied bytes and native cursor ordering avoid exposing mapped pointers or function pointers |
| `mdbx_reader_check` | Wrapped | `Environment.reader_check` |
| `mdbx_reader_list`, `mdbx_txn_straggler`, HSR set/get | Excluded | Callback-based monitoring/reader eviction can execute arbitrary code inside engine critical paths and requires a reentrancy policy |
| Thread register/unregister, txn lock/unlock | Excluded | Python wrapper enforces transaction thread affinity; exporting raw locks would allow violating it |
| Environment/transaction/cursor user-context setters/getters | Excluded | Python strong references provide ownership; raw `void *` cannot be made type-safe |
| Debug/log/panic/assert/error helpers (`module_handler`, `setup_debug*`, `assert_fail`, `set_panic`, `dump_val`, `strerror*`, `liberr2str`) | Internal/represented | Import configures warning logging; exceptions use libmdbx error text. Process-global callbacks are not public |
| Recovery/open-for-recovery, turn-for-recovery, preopen snapshot info | Excluded | Administrative repair can overwrite/roll back files and belongs in an explicit offline recovery tool, not a normal process handle |
| `mdbx_env_chk`, `mdbx_env_chk_encount_problem` | Tooling | Official vendored `mdbx_chk` is built by `scripts/build_mdbx_chk.sh`; the in-process callback API risks reentrancy |
| `mdbx_env_resurrect_after_fork` | Intentionally excluded | Reusing inherited mmap/locks is easy to misuse; wrapper rejects inherited objects and requires a clean reopen |
| `mdbx_ratio2digits/percents` | Excluded | Formatting helpers only; no database capability |

The source-level guard `scripts/check_api_coverage.py` extracts the ordered set
of every official declaration and checks its count and SHA-256 against the set
used to prepare this matrix. This keeps future vendor upgrades from silently
adding, removing or renaming an API without a matrix review.
