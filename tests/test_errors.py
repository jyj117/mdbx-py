from __future__ import annotations

import pathlib

import pytest

import clibmdbx


def test_no_overwrite_is_pythonic(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        assert txn.put(b"key", b"one")
        assert txn.put(b"key", b"two", None, clibmdbx.MDBX_NOOVERWRITE) is False
        assert txn.get(b"key") == b"one"


def test_map_full_maps_to_specific_exception(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "small"
    path.mkdir()
    env = clibmdbx.Environment(path, geometry=(0, 1 << 20, 1 << 20, 0, 0, -1))
    try:
        txn = env.write()
        with pytest.raises(clibmdbx.MapFullError) as caught:
            for i in range(100):
                txn.put(i.to_bytes(4, "little"), bytes(65536))
        assert caught.value.code != 0
        assert caught.value.what == "mdbx_put"
        assert "MAP_FULL" in caught.value.reason
        txn.abort()
    finally:
        env.close()


def test_invalid_contiguity_and_readonly_write(env: clibmdbx.Environment, env_path: pathlib.Path) -> None:
    with env.write() as txn:
        with pytest.raises((TypeError, BufferError)):
            txn.put(memoryview(b"abcdef")[::2], b"x")
    env.close()
    readonly = clibmdbx.Environment(env_path, readonly=True)
    try:
        with pytest.raises(clibmdbx.ReadonlyError) as caught:
            readonly.write()
        assert isinstance(caught.value.code, int)
        assert caught.value.what == "mdbx_txn_begin"
        assert caught.value.reason
    finally:
        readonly.close()


def test_corrupted_file_has_actionable_exception(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "corrupt"
    path.mkdir()
    (path / "mdbx.dat").write_bytes(b"not an mdbx database" * 100)
    with pytest.raises(clibmdbx.Error) as caught:
        clibmdbx.Environment(path, readonly=True)
    assert "mdbx_env_open" in str(caught.value)


def test_readers_full_maps_to_specific_exception(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "readers"
    path.mkdir()
    env = clibmdbx.Environment(path, max_readers=1)
    transactions = []
    try:
        with pytest.raises(clibmdbx.ReadersFullError):
            for _ in range(1024):
                transactions.append(env.read())
    finally:
        for txn in transactions:
            txn.abort()
        env.close()


def test_dbs_full_maps_to_specific_exception(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "dbs"
    path.mkdir()
    env = clibmdbx.Environment(path, max_dbs=2)
    try:
        txn = env.write()
        handles = []
        with pytest.raises(clibmdbx.DbsFullError):
            for i in range(64):
                handles.append(txn.open_db(f"db-{i}".encode(), create=True))
        txn.abort()
        del handles
    finally:
        env.close()


def test_native_layout_changing_put_flags_are_rejected_everywhere(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        cursor = txn.cursor()
        for flag, match in (
            (clibmdbx.MDBX_RESERVE, "RESERVE"),
            (clibmdbx.MDBX_MULTIPLE, "MULTIPLE"),
        ):
            for call in (
                lambda flag=flag: txn.put(b"key", b"value", flags=flag),
                lambda flag=flag: txn.replace(b"key", b"value", flags=flag),
                lambda flag=flag: txn.put_many([(b"key", b"value")], flags=flag),
                lambda flag=flag: cursor.put(b"key", b"value", flags=flag),
            ):
                with pytest.raises(ValueError, match=match):
                    call()
        cursor.close()


def test_fastcall_put_flags_do_not_truncate_on_64_bit_hosts(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        with pytest.raises(OverflowError, match="uint32"):
            txn.put(b"key", b"value", flags=1 << 40)


@pytest.mark.parametrize("value", [-1, 1 << 40])
def test_all_uint32_arguments_reject_out_of_range_values(
    env: clibmdbx.Environment, tmp_path: pathlib.Path, value: int
) -> None:
    constructor_calls = (
        lambda: clibmdbx.Environment(tmp_path / "bad-flags", flags=value),
        lambda: clibmdbx.Environment(tmp_path / "bad-mode", mode=value),
        lambda: clibmdbx.Environment(tmp_path / "bad-readers", max_readers=value),
        lambda: clibmdbx.Environment(tmp_path / "bad-dbs", max_dbs=value),
    )
    for call in constructor_calls:
        with pytest.raises(OverflowError, match="uint32"):
            call()

    environment_calls = (
        lambda: env.copy(tmp_path / "bad-copy", flags=value),
        lambda: env.set_flags(value),
        lambda: env.warmup(flags=value),
        lambda: env.warmup(timeout=value),
        lambda: env.begin(flags=value),
        lambda: env.open_db(flags=value),
    )
    for call in environment_calls:
        with pytest.raises(OverflowError, match="uint32"):
            call()

    with env.write() as txn:
        db = txn.open_db(b"range-checks", create=True)
        cursor = txn.cursor(db)
        transaction_calls = (
            lambda: txn.open_db(flags=value),
            lambda: txn.put(b"key", b"value", flags=value),
            lambda: txn.replace(b"key", b"value", flags=value),
            lambda: txn.put_many([(b"key", b"value")], flags=value),
            lambda: cursor.put(b"key", b"value", flags=value),
            lambda: cursor.delete(flags=value),
        )
        for call in transaction_calls:
            with pytest.raises(OverflowError, match="uint32"):
                call()
        cursor.close()


@pytest.mark.parametrize("value", [-1, 1 << 80])
def test_all_uint64_arguments_reject_out_of_range_values(env: clibmdbx.Environment, value: int) -> None:
    with pytest.raises(OverflowError, match="uint64"):
        env.set_option(clibmdbx.MDBX_opt_sync_bytes, value)
    with env.write() as txn:
        db = txn.open_db(b"sequence-range", create=True)
        with pytest.raises(OverflowError, match="uint64"):
            db.sequence(txn, increment=value)
