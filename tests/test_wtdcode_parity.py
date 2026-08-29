"""Independent behavioral ports of every wtdcode/mdbx-py test scenario.

The reference suite is not copied: these tests exercise the same database
capabilities through clibmdbx's safe, Pythonic API.  The audited reference is
recorded in docs/WTD_MDBX_PY_COMPATIBILITY.md.
"""

from __future__ import annotations

import struct

import clibmdbx
import pytest


KEY = b"MDBX_TEST_KEY"
BINARY_VALUE = b"\xaa\xbb\xcc\x00"
TEXT_VALUE = b"MDBX_TEST_VAL_UTF8"


def open_env(tmp_path, name: str, *, max_dbs: int = 64, geometry=None) -> clibmdbx.Environment:
    path = tmp_path / name
    path.mkdir()
    return clibmdbx.Environment(
        path,
        max_dbs=max_dbs,
        max_readers=32,
        geometry=geometry or (0, 1 << 20, 1 << 28, 1 << 20, 2 << 20, -1),
    )


def test_wtd_open(tmp_path) -> None:
    with open_env(tmp_path, "open", max_dbs=1) as env:
        assert not env.closed
        assert env.path == tmp_path / "open"


def test_wtd_write(tmp_path) -> None:
    with open_env(tmp_path, "write", max_dbs=1) as env:
        with env.write() as txn:
            assert txn.put(KEY, BINARY_VALUE)
        with env.read() as txn:
            assert txn.get(KEY) == BINARY_VALUE
            assert txn.stat()["entries"] == 1


def test_wtd_db_readitem_writeitem(tmp_path) -> None:
    with open_env(tmp_path, "mapping", max_dbs=1) as env:
        with env.write() as txn:
            assert txn.put(KEY, TEXT_VALUE)
        with env.read() as txn:
            assert txn[KEY] == TEXT_VALUE


def test_wtd_db_iter(tmp_path) -> None:
    path = tmp_path / "db-iter"
    path.mkdir()
    expected: dict[bytes, list[tuple[bytes, bytes]]] = {}
    with clibmdbx.Environment(path, max_dbs=32) as env:
        with env.write() as txn:
            for db_index in range(15):
                name = f"table-{db_index:02d}".encode()
                db = txn.open_db(name, create=True)
                records = [
                    (f"key-{item:04d}".encode(), f"value-{db_index:02d}-{item:04d}".encode())
                    for item in range(1024)
                ]
                assert txn.put_many(records, db=db) == len(records)
                expected[name] = records
    with clibmdbx.Environment(path, max_dbs=32) as env:
        with env.read() as txn:
            assert {entry["name"] for entry in txn.databases()} >= set(expected)
            for name, records in expected.items():
                db = txn.open_db(name)
                assert txn.get_many((key for key, _ in records), db=db) == [value for _, value in records]


def test_wtd_success_close_written_map(tmp_path) -> None:
    with open_env(tmp_path, "close-map") as env:
        with env.write() as txn:
            db = txn.open_db(b"map", create=True)
            txn.put(KEY, TEXT_VALUE, db)
        db.close()
        assert db.closed


def test_wtd_multi_write(tmp_path) -> None:
    path = tmp_path / "multi-write"
    path.mkdir()
    expected: dict[bytes, list[tuple[bytes, bytes]]] = {}
    geometry = (0, 1 << 20, 2 << 30, 1 << 20, 2 << 20, -1)
    with clibmdbx.Environment(path, max_dbs=32, geometry=geometry) as env:
        for db_index in range(16):
            name = f"batch-{db_index:02d}".encode()
            records = [
                (f"key-{item:04d}".encode(), f"value-{db_index:02d}-{item:04d}".encode())
                for item in range(1024)
            ]
            with env.write() as txn:
                db = txn.open_db(name, create=True)
                assert txn.put_many(records, db=db) == len(records)
            expected[name] = records
    with clibmdbx.Environment(path, max_dbs=32) as env:
        with env.read() as txn:
            for name, records in expected.items():
                db = txn.open_db(name)
                assert txn.get_many((key for key, _ in records), db=db) == [value for _, value in records]


def test_wtd_replace(tmp_path) -> None:
    with open_env(tmp_path, "replace") as env:
        with env.write() as txn:
            txn.put(KEY, BINARY_VALUE)
        with env.write() as txn:
            assert txn.replace(KEY, TEXT_VALUE) == BINARY_VALUE
        with env.read() as txn:
            assert txn.get(KEY) == TEXT_VALUE


def test_wtd_delete(tmp_path) -> None:
    with open_env(tmp_path, "delete") as env:
        with env.write() as txn:
            db = txn.open_db(b"multi", flags=clibmdbx.MDBX_DUPSORT, create=True)
            txn.put(KEY, BINARY_VALUE, db)
            txn.put(KEY, TEXT_VALUE, db)
        with env.write() as txn:
            assert txn.delete(KEY, BINARY_VALUE, db)
            assert txn.get(KEY, db) == TEXT_VALUE
            assert txn.delete(KEY, db=db)
            assert txn.get(KEY, db) is None


def test_wtd_env(tmp_path) -> None:
    source = tmp_path / "env"
    source.mkdir()
    destination = tmp_path / "env-copy"
    with clibmdbx.Environment(source, max_dbs=2) as env:
        assert env.stat()["page_size"] > 0
        assert env.info()["geometry"]["upper"] > 0
        with env.write() as txn:
            db = txn.open_db(b"managed", create=True)
            txn.put(KEY, TEXT_VALUE, db)
        env.copy(destination, flags=clibmdbx.MDBX_CP_FORCE_DYNAMIC_SIZE)
        env.set_option(clibmdbx.MDBX_opt_txn_dp_initial, 2048)
        assert env.get_option(clibmdbx.MDBX_opt_txn_dp_initial) == 2048
        assert env.sync(force=True) in (False, True)
        limits = clibmdbx.limits()
        assert limits["key_size_max"] > 0
        assert limits["value_size_max"] >= limits["key_size_max"]
        with env.write() as txn:
            db.clear(txn)
            assert db.stat(txn)["entries"] == 0
            db.drop(txn, delete=True)
    with clibmdbx.Environment(destination, readonly=True) as copied:
        with copied.read() as txn:
            copied_db = txn.open_db(b"managed")
            assert txn.get(KEY, copied_db) == TEXT_VALUE


def test_wtd_userctx_is_replaced_by_owned_object_graph(tmp_path) -> None:
    """Raw ``void *`` contexts are intentionally absent; owners stay alive safely."""

    env = open_env(tmp_path, "user-context")
    txn = env.read()
    cursor = txn.cursor()
    db = txn.open_db()
    assert not hasattr(env, "set_user_ctx")
    assert not hasattr(env, "set_user_ctx_int")
    assert not hasattr(txn, "set_user_ctx")
    del env
    assert txn.active
    assert cursor.first() is None
    assert db.name is None
    cursor.close()
    txn.abort()


def test_wtd_txn(tmp_path) -> None:
    with open_env(tmp_path, "txn", max_dbs=1) as env:
        txn = env.read()
        before = txn.id
        txn.reset()
        assert not txn.active
        txn.renew()
        assert txn.active
        assert txn.id >= before
        info = txn.info()
        assert info["id"] == txn.id
        txn.abort()


def test_wtd_get_build_info() -> None:
    info = clibmdbx.diagnostics()
    assert info["binding_version"] == clibmdbx.__version__
    assert info["build"]["options"]
    assert info["build"]["compiler"]


def test_wtd_get_version_info() -> None:
    info = clibmdbx.version_info()
    version = info["libmdbx"]
    assert (version["major"], version["minor"], version["patch"]) == (0, 14, 3)
    assert version["git_commit"] == "251562b2dc55266d8e6d0e6627ec88ecb410702f"


def test_wtd_get_sysram() -> None:
    limits = clibmdbx.limits()
    assert limits["system_page_size"] > 0
    assert limits["system_ram_total_pages"] > 0
    assert 0 <= limits["system_ram_available_pages"] <= limits["system_ram_total_pages"]


def test_wtd_txnid(tmp_path) -> None:
    with open_env(tmp_path, "txnid", max_dbs=1) as env:
        with env.write() as txn:
            assert txn.id > 0


def test_wtd_cursor_bind_now_exercises_renew(tmp_path) -> None:
    """The reference test was a no-op; this validates the safe equivalent."""

    with open_env(tmp_path, "cursor-renew", max_dbs=1) as env:
        with env.write() as writer:
            writer.put(b"a", b"1")
        old_txn = env.read()
        cursor = old_txn.cursor()
        assert cursor.first() == (b"a", b"1")
        old_txn.reset()
        new_txn = env.read()
        cursor.renew(new_txn)
        assert cursor.first() == (b"a", b"1")
        cursor.close()
        new_txn.abort()
        old_txn.abort()


def test_wtd_cursor_open(tmp_path) -> None:
    with open_env(tmp_path, "cursor-open", max_dbs=2) as env:
        with env.write() as txn:
            db = txn.open_db(b"cursor", create=True)
            with txn.cursor(db) as cursor:
                cursor.put(KEY, TEXT_VALUE)
                cursor.put(b"abc", b"def")
                assert cursor.set(KEY) == (KEY, TEXT_VALUE)
        with env.write() as txn:
            with txn.cursor(db) as cursor:
                assert cursor.first() == (KEY, TEXT_VALUE)
                assert cursor.last() == (b"abc", b"def")
                assert cursor.prev() == (KEY, TEXT_VALUE)
                assert cursor.next() == (b"abc", b"def")
                assert cursor.next() is None
                assert cursor.first() == (KEY, TEXT_VALUE)
                cursor.delete()
                assert cursor.set(KEY) is None


def test_wtd_set_range(tmp_path) -> None:
    with open_env(tmp_path, "set-range", max_dbs=2) as env:
        records = [
            (b"OPEN", b"1"),
            (b"QBUZZ*BRANDING*QBUZZ-5-\x94\x00", b"2"),
            (b"TEST", b"3"),
        ]
        with env.write() as txn:
            db = txn.open_db(b"range", create=True)
            txn.put_many(records, db=db)
        with env.read() as txn, txn.cursor(db) as cursor:
            assert cursor.set(b"TEST") == (b"TEST", b"3")
            assert cursor.set_range(b"QBUZZ*BRANDING*QBUZZ-") == records[1]
            assert cursor.items(start=b"QBUZZ*BRANDING*QBUZZ-")[:1] == [records[1]]


def test_wtd_parent_txn(tmp_path) -> None:
    with open_env(tmp_path, "parent", max_dbs=2) as env:
        parent = env.write()
        parent.put(b"parent", b"1")
        child = env.begin(write=True, parent=parent)
        child.put(b"child", b"2")
        child.commit()
        parent.commit()
        with env.read() as txn:
            assert txn.get_many((b"parent", b"child")) == [b"1", b"2"]


def test_wtd_null_bytes(tmp_path) -> None:
    name, key, value = b"0m\x00\x00", b"1k\x00\x00", b"2v\x00\x00"
    # `nul` is a reserved Win32 device name, so keep the embedded-NUL test
    # entirely in MDBX keys/values while using a portable directory name.
    with open_env(tmp_path, "embedded-null-data", max_dbs=2) as env:
        with env.write() as txn:
            db = txn.open_db(name, create=True)
            txn.put(key, value, db)
        with env.read() as txn:
            assert any(entry["name"] == name for entry in txn.databases())
            reopened = txn.open_db(name)
            assert txn.get(key, reopened) == value
            with txn.cursor(reopened) as cursor:
                assert cursor.first() == (key, value)


def test_wtd_iters(tmp_path) -> None:
    expected = [
        (struct.pack(">I", i), struct.pack(">I", 10 - i))
        for i in range(10)
        if i != 1
    ]
    with open_env(tmp_path, "iters") as env:
        with env.write() as txn:
            txn.put_many(expected)
        with env.read() as txn, txn.cursor() as cursor:
            assert cursor.first() == expected[0]
            assert cursor.last() == expected[-1]
            assert cursor.items() == expected
            assert cursor.items(start=struct.pack(">I", 4)) == expected[3:]


def test_wtd_iters_dup(tmp_path) -> None:
    keys = [struct.pack(">I", i) for i in range(10) if i != 1]
    values = [struct.pack(">I", i) for i in range(5)]
    expected = [(key, value) for key in keys for value in values]
    with open_env(tmp_path, "iters-dup", max_dbs=2) as env:
        with env.write() as txn:
            db = txn.open_db(b"dups", flags=clibmdbx.MDBX_DUPSORT, create=True)
            txn.put_many(expected, db=db)
        with env.read() as txn, txn.cursor(db) as cursor:
            assert cursor.first() == expected[0]
            assert cursor.first_dup() == expected[0]
            assert cursor.last_dup() == (keys[0], values[-1])
            assert cursor.last() == expected[-1]
            assert cursor.first_dup() == (keys[-1], values[0])
            assert cursor.last_dup() == expected[-1]
            assert cursor.items() == expected
            rows: list[tuple[bytes, tuple[bytes, ...]]] = []
            record = cursor.first()
            while record is not None:
                key = record[0]
                duplicates = [record[1]]
                record = cursor.next_dup()
                while record is not None:
                    duplicates.append(record[1])
                    record = cursor.next_dup()
                rows.append((key, tuple(duplicates)))
                record = cursor.next_nodup()
            assert rows == [(key, tuple(values)) for key in keys]


def test_wtd_sequence(tmp_path) -> None:
    with open_env(tmp_path, "sequence") as env:
        with env.read() as txn:
            db = txn.open_db()
            assert db.sequence(txn) == 0
        txn = env.write()
        assert db.sequence(txn, increment=1) == 0
        assert db.sequence(txn, increment=1) == 1
        txn.abort()
        with env.read() as txn:
            assert db.sequence(txn) == 0
        with env.write() as txn:
            assert db.sequence(txn, increment=1) == 0
            assert db.sequence(txn, increment=1) == 1
        with env.read() as txn:
            assert db.sequence(txn) == 2

        overflow_txn = env.write()
        try:
            assert db.sequence(overflow_txn, increment=(1 << 64) - 3) == 2
            with pytest.raises(OverflowError, match="overflowed uint64"):
                db.sequence(overflow_txn, increment=1)
        finally:
            overflow_txn.abort()
