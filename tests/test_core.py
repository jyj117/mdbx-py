from __future__ import annotations

import array
import pathlib

import pytest

import clibmdbx

try:
    import _xxsubinterpreters as subinterpreters
except ImportError:  # pragma: no cover - implementation/version dependent
    subinterpreters = None


def test_diagnostics_and_limits() -> None:
    assert clibmdbx.open is clibmdbx.Environment
    diagnostics = clibmdbx.diagnostics()
    assert diagnostics["binding_version"] == clibmdbx.__version__
    assert diagnostics["libmdbx"]["major"] == 0
    assert diagnostics["libmdbx"]["minor"] == 14
    assert diagnostics["libmdbx"]["patch"] == 3
    assert diagnostics["libmdbx"]["git_commit"] == "251562b2dc55266d8e6d0e6627ec88ecb410702f"
    assert diagnostics["archive_sha256"] == "dbc4a791c44d3e51a8159eedfee0dedada7b21d46c22588f0fa99294983f33cd"
    limits = clibmdbx.limits()
    assert limits["page_size_min"] <= limits["page_size_max"]
    assert limits["key_size_max"] > 0
    assert limits["system_page_size"] > 0
    assert limits["system_ram_total_pages"] > 0
    assert 0 <= limits["system_ram_available_pages"] <= limits["system_ram_total_pages"]


@pytest.mark.skipif(subinterpreters is None, reason="CPython subinterpreter test module unavailable")
def test_subinterpreter_import_is_explicitly_rejected() -> None:
    interpreter = subinterpreters.create()
    try:
        # CPython 3.12 rejects the extension from the
        # Py_mod_multiple_interpreters slot before our module-level guard runs;
        # newer versions may reach the more descriptive binding-owned error.
        with pytest.raises(
            subinterpreters.RunFailedError,
            match="(?:does not support loading in subinterpreters|main CPython interpreter only)",
        ):
            subinterpreters.run_string(interpreter, "import clibmdbx")
    finally:
        subinterpreters.destroy(interpreter)


def test_crud_and_mapping_protocol(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        assert txn.put(b"a\x00b", b"v\x00x")
        assert txn.put(memoryview(b"mem"), bytearray(b"view"))
        assert txn.get(b"a\x00b") == b"v\x00x"
        assert txn.get(b"missing") is None
        assert txn.get(b"missing", None, b"fallback") == b"fallback"
        assert txn.replace(b"a\x00b", b"new") == b"v\x00x"
        assert txn.replace(b"new-key", b"created") is None
        assert txn.delete(b"absent") is False
        assert txn.delete(b"new-key") is True

    with env.read() as txn:
        assert txn[b"a\x00b"] == b"new"
        assert b"a\x00b" in txn
        assert b"missing" not in txn
        with pytest.raises(KeyError):
            _ = txn[b"missing"]
        assert txn.get(array.array("B", b"mem")) == b"view"


def test_hot_crud_accepts_fastcall_keywords(env: clibmdbx.Environment) -> None:
    db = env.open_db(b"keyword-crud", create=True)
    with env.write() as txn:
        assert txn.put(key=b"key", value=b"value", db=db)
        assert txn.get(key=b"key", db=db, default=b"missing") == b"value"
        assert txn.get(key=b"absent", db=db, default=b"fallback") == b"fallback"
        assert txn.put(key=b"key", value=b"other", db=db, flags=clibmdbx.MDBX_NOOVERWRITE) is False
        assert txn.delete(key=b"key", db=db)
        with pytest.raises(TypeError, match="multiple values"):
            txn.get(b"key", key=b"duplicate")
        with pytest.raises(TypeError, match="unexpected keyword"):
            txn.get(key=b"key", unknown=True)
        with pytest.raises(TypeError, match="missing required"):
            txn.put(value=b"value")
    db.close()


def test_environment_one_shot_get(env: clibmdbx.Environment) -> None:
    db = env.open_db(b"one-shot", create=True)
    with env.write() as txn:
        txn.put(b"a\x00b", b"v\x00x", db=db)
        txn.put(b"empty", b"", db=db)

    assert env.get(b"a\x00b", db) == b"v\x00x"
    assert env.get(key=memoryview(b"empty"), db=db) == b""
    assert env.get(b"missing", db, b"fallback") == b"fallback"
    assert env.get(key=b"missing", db=db, default=None) is None
    with pytest.raises(TypeError, match="multiple values"):
        env.get(b"a\x00b", key=b"duplicate")
    with pytest.raises(TypeError, match="unexpected keyword"):
        env.get(key=b"a\x00b", unknown=True)
    with pytest.raises(TypeError, match="missing required"):
        env.get(db=db)
    db.close()


def test_environment_one_shot_get_returns_safe_copy(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        txn.put(b"key", b"before")
    value = env.get(b"key")
    with env.write() as txn:
        txn.put(b"key", b"after")
    assert value == b"before"


def test_environment_one_shot_get_validates_database_scope(env: clibmdbx.Environment, tmp_path: pathlib.Path) -> None:
    other = clibmdbx.Environment(tmp_path / "other")
    foreign = other.open_db(b"foreign", create=True)
    try:
        with pytest.raises(ValueError, match="different environment"):
            env.get(b"key", foreign)
    finally:
        foreign.close()
        other.close()

    writer = env.write()
    provisional = writer.open_db(b"provisional", create=True)
    writer.put(b"key", b"value", db=provisional)
    with pytest.raises(clibmdbx.BadTxnError, match="uncommitted"):
        env.get(b"key", provisional)
    writer.abort()
    with pytest.raises(clibmdbx.ClosedError):
        env.get(b"key", provisional)


def test_environment_one_shot_get_releases_every_reader(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        txn.put(b"key", b"value")
    for _ in range(20_000):
        assert env.get(b"key") == b"value"
    assert env.reader_check() == 0
    env.close()
    env.close()
    with pytest.raises(clibmdbx.ClosedError):
        env.get(b"key")


def test_environment_one_shot_get_additional_bytes_and_default_boundaries(env: clibmdbx.Environment) -> None:
    sentinel = object()
    mutable_key = bytearray(b"mutable")
    with env.write() as txn:
        txn.put(mutable_key, b"value")
        txn.put(b"", b"empty-key")

    assert env.get(memoryview(mutable_key)) == b"value"
    assert env.get(b"") == b"empty-key"
    assert env.get(b"missing", default=sentinel) is sentinel
    with pytest.raises(TypeError, match="db must be"):
        env.get(b"key", object())
    with pytest.raises(TypeError, match="1 to 3 positional"):
        env.get(b"a", None, None, None)


def test_environment_one_shot_get_dupsort_and_dropped_database(env: clibmdbx.Environment) -> None:
    db = env.open_db(b"one-shot-dups", create=True, flags=clibmdbx.MDBX_DUPSORT)
    with env.write() as txn:
        txn.put(b"key", b"b", db=db)
        txn.put(b"key", b"a", db=db)
    assert env.get(b"key", db=db) == b"a"

    with env.write() as txn:
        db.drop(txn, delete=True)
    with pytest.raises(clibmdbx.ClosedError):
        env.get(b"key", db=db)


def test_value_is_safe_copy(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        txn.put(b"key", b"before")
    with env.read() as txn:
        value = txn.get(b"key")
    with env.write() as txn:
        txn.put(b"key", b"after")
    assert value == b"before"


def test_empty_value_and_empty_key_semantics(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        assert txn.put(b"empty-value", b"")
        assert txn.get(b"empty-value") == b""
        assert txn.put(b"", b"value")
        assert txn.get(b"") == b"value"


def test_context_abort_and_idempotent_close(env: clibmdbx.Environment) -> None:
    with pytest.raises(RuntimeError):
        with env.write() as txn:
            txn.put(b"rollback", b"yes")
            raise RuntimeError("abort")
    with env.read() as txn:
        assert txn.get(b"rollback") is None
        txn.abort()
        txn.abort()
    env.close()
    env.close()
    assert env.closed
    with pytest.raises(clibmdbx.ClosedError):
        env.read()


def test_environment_cannot_be_reinitialized_after_close(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    env = clibmdbx.Environment(first)
    env.close()
    with pytest.raises(RuntimeError, match="more than once"):
        env.__init__(second)
    assert env.path == first
    assert env.closed


def test_prepared_read_transaction_and_conflicting_flags(env: clibmdbx.Environment) -> None:
    prepared = env.begin(flags=clibmdbx.MDBX_TXN_RDONLY_PREPARE)
    assert prepared.readonly
    assert not prepared.active
    with pytest.raises(clibmdbx.BadTxnError, match="renew"):
        prepared.get(b"key")
    prepared.renew()
    assert prepared.active
    prepared.abort()

    with pytest.raises(ValueError, match="conflicts"):
        env.begin(write=True, flags=clibmdbx.MDBX_TXN_RDONLY)


def test_broken_transaction_is_inactive_but_can_be_aborted(env: clibmdbx.Environment) -> None:
    txn = env.write()
    txn.put(b"discard", b"value")
    txn.break_()
    assert not txn.active
    with pytest.raises(clibmdbx.BadTxnError, match="broken"):
        txn.get(b"discard")
    txn.abort()
    with env.read() as reader:
        assert reader.get(b"discard") is None


def test_close_order_is_enforced(env: clibmdbx.Environment) -> None:
    txn = env.read()
    with pytest.raises(clibmdbx.BusyError):
        env.close()
    txn.abort()
    env.close()


def test_reset_renew(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        txn.put(b"k", b"v1")
    txn = env.read()
    first_id = txn.id
    assert txn.get(b"k") == b"v1"
    txn.reset()
    assert not txn.active
    with pytest.raises(clibmdbx.BadTxnError):
        txn.get(b"k")
    with env.write() as writer:
        writer.put(b"k", b"v2")
    txn.renew()
    assert txn.id >= first_id
    assert txn.get(b"k") == b"v2"
    txn.abort()


def test_canary_gc_refresh_and_park(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        canary = txn.canary((11, 22, 33))
        assert canary[:3] == (11, 22, 33)
    txn = env.read()
    assert txn.canary()[:3] == (11, 22, 33)
    assert set(txn.gc_info()) >= {"pages_total", "pages_allocated", "pages_reclaimable"}
    txn.park()
    assert txn.unpark() in (False, True)
    assert txn.refresh() in (False, True)
    txn.abort()
    with pytest.raises(clibmdbx.ClosedError):
        txn.unpark()


def test_defrag_result_shape(env: clibmdbx.Environment) -> None:
    assert env.warmup() in (False, True)
    result = env.defrag(time_limit=1)
    assert set(result) >= {"complete", "pages_shrunk", "pages_moved", "stopping_reasons", "cycles"}


def test_nested_write_commit_and_abort(env: clibmdbx.Environment) -> None:
    parent = env.write()
    parent.put(b"parent", b"one")
    child = env.begin(write=True, parent=parent)
    child.put(b"child", b"committed")
    with pytest.raises(clibmdbx.BadTxnError):
        parent.get(b"parent")
    child.commit()
    child2 = env.begin(write=True, parent=parent)
    child2.put(b"aborted", b"no")
    child2.abort()
    parent.commit()
    with env.read() as txn:
        assert txn.get(b"parent") == b"one"
        assert txn.get(b"child") == b"committed"
        assert txn.get(b"aborted") is None


def test_copy_and_readonly(env: clibmdbx.Environment, tmp_path: pathlib.Path) -> None:
    with env.write() as txn:
        txn.put(b"copy", b"value")
    destination = tmp_path / "copy"
    env.copy(destination, flags=clibmdbx.MDBX_CP_COMPACT)
    copied = clibmdbx.Environment(destination, readonly=True)
    try:
        with copied.read() as txn:
            assert txn.get(b"copy") == b"value"
        with pytest.raises(clibmdbx.Error):
            copied.write()
    finally:
        copied.close()


def test_path_nul_rejected(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError):
        clibmdbx.Environment(str(tmp_path) + "\x00suffix")
