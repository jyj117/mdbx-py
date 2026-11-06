from __future__ import annotations

import gc

import pytest

import clibmdbx


def test_named_database_lifecycle(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        db = txn.open_db(b"named\x00db", create=True)
        txn.put(b"a", b"1", db)
        txn.put(b"b", b"2", db)
        assert db.sequence(txn) == 0
        assert db.sequence(txn, 4) == 0
        assert db.sequence(txn) == 4
        assert db.stat(txn)["entries"] == 2
        assert db.flags(txn)["flags"] == 0
    with env.read() as txn:
        assert txn.get(b"a", db) == b"1"
        assert any(item["name"] == b"named\x00db" for item in txn.databases())
        alias = txn.open_db(b"named\x00db")
    with env.write() as txn:
        db.rename(txn, b"renamed")
    assert db.name == b"renamed"
    assert alias.name == b"renamed"
    with env.read() as txn:
        with pytest.raises(clibmdbx.NotFoundError):
            txn.open_db(b"named\x00db")
        assert txn.open_db(b"renamed").name == b"renamed"
    with env.write() as txn:
        db.clear(txn)
        assert db.stat(txn)["entries"] == 0
    db.close()
    assert db.closed


def test_database_rename_rolls_back_python_name(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        db = txn.open_db(b"rename-original", create=True)

    txn = env.write()
    db.rename(txn, b"rename-aborted")
    assert db.name == b"rename-aborted"
    txn.abort()
    assert db.name == b"rename-original"
    with env.read() as txn:
        assert txn.open_db(b"rename-original").name == b"rename-original"
        with pytest.raises(clibmdbx.NotFoundError):
            txn.open_db(b"rename-aborted")


def test_nested_database_rename_rolls_back_with_outer(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        db = txn.open_db(b"nested-rename-original", create=True)

    outer = env.write()
    child = env.begin(write=True, parent=outer)
    db.rename(child, b"nested-rename-new")
    child.commit()
    assert db.name == b"nested-rename-new"
    outer.abort()
    assert db.name == b"nested-rename-original"


def test_dupsort_cursor_complete_navigation(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        db = txn.open_db(b"dups", flags=clibmdbx.MDBX_DUPSORT, create=True)
        for key, value in [(b"a", b"1"), (b"a", b"2"), (b"a", b"3"), (b"b", b"1"), (b"c", b"1")]:
            assert txn.put(key, value, db)
        with txn.cursor(db) as cur:
            assert cur.first() == (b"a", b"1")
            assert cur.count() == 3
            assert cur.next_dup() == (b"a", b"2")
            assert cur.last_dup() == (b"a", b"3")
            assert cur.next_nodup() == (b"b", b"1")
            assert cur.prev_nodup() == (b"a", b"3")
            assert cur.get_both(b"a", b"2") == (b"a", b"2")
            assert cur.get_both(b"a", b"2x", range=True) == (b"a", b"3")
            assert cur.set(b"missing") is None
            assert cur.set_range(b"bb") == (b"c", b"1")
            assert cur.items(start=b"a", stop=b"c") == [
                (b"a", b"1"),
                (b"a", b"2"),
                (b"a", b"3"),
                (b"b", b"1"),
            ]
            assert cur.items(start=b"c", reverse=True) == [
                (b"c", b"1"),
                (b"b", b"1"),
                (b"a", b"3"),
                (b"a", b"2"),
                (b"a", b"1"),
            ]
    db.close()


def test_cursor_put_delete_and_iteration(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        db = txn.open_db(b"cursor-write", create=True)
        with txn.cursor(db) as cur:
            assert cur.put(b"a", b"1")
            assert cur.put(b"b", b"2")
            assert list(cur) == [(b"a", b"1"), (b"b", b"2")]
            assert cur.set(b"b") == (b"b", b"2")
            assert list(cur) == [(b"b", b"2")]
            with pytest.raises(StopIteration):
                next(cur)
            assert cur.set(b"a") == (b"a", b"1")
            cur.delete()
            assert cur.set(b"a") is None
    db.close()


def test_cursor_survives_transaction_end_only_for_close(env: clibmdbx.Environment) -> None:
    txn = env.read()
    cur = txn.cursor()
    txn.abort()
    try:
        with __import__("pytest").raises(clibmdbx.ClosedError):
            cur.first()
    finally:
        cur.close()


def test_duplicate_database_wrappers_do_not_close_shared_dbi(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        first = txn.open_db(b"shared-dbi", create=True)
        second = txn.open_db(b"shared-dbi")
        txn.put(b"key", b"value", second)

    first.close()
    assert first.closed
    with env.read() as txn:
        assert txn.get(b"key", second) == b"value"

    with env.read() as txn:
        discarded = txn.open_db(b"shared-dbi")
    del discarded
    gc.collect()
    with env.read() as txn:
        assert txn.get(b"key", second) == b"value"

    # Closing a Python view while a transaction exists is safe because the
    # native DBI remains owned by the environment.
    txn = env.read()
    try:
        alias = txn.open_db(b"shared-dbi")
        alias.close()
        assert txn.get(b"key", second) == b"value"
    finally:
        txn.abort()
    second.close()


def test_aborted_database_create_invalidates_python_handle(env: clibmdbx.Environment) -> None:
    txn = env.write()
    db = txn.open_db(b"rolled-back-create", create=True)
    txn.put(b"key", b"value", db)
    txn.abort()

    assert db.closed
    with env.read() as read_txn:
        with pytest.raises(clibmdbx.ClosedError):
            read_txn.get(b"key", db)
        with pytest.raises(clibmdbx.NotFoundError):
            read_txn.open_db(b"rolled-back-create")


def test_nested_create_stays_provisional_until_outer_commit(env: clibmdbx.Environment) -> None:
    outer = env.write()
    child = env.begin(write=True, parent=outer)
    db = child.open_db(b"nested-rolled-back-create", create=True)
    child.put(b"key", b"value", db)
    child.commit()
    assert not db.closed
    assert outer.get(b"key", db) == b"value"
    outer.abort()

    assert db.closed
    with env.read() as txn:
        with pytest.raises(clibmdbx.NotFoundError):
            txn.open_db(b"nested-rolled-back-create")


def test_deleting_drop_invalidates_every_database_alias(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        first = txn.open_db(b"drop-alias", create=True)
        second = txn.open_db(b"drop-alias")
        txn.put(b"key", b"value", first)

    with env.write() as txn:
        first.drop(txn, delete=True)
        assert first.closed
        assert second.closed
        with pytest.raises(clibmdbx.ClosedError):
            txn.get(b"key", second)

    with env.read() as txn:
        with pytest.raises(clibmdbx.NotFoundError):
            txn.open_db(b"drop-alias")


def test_deleting_drop_rejects_other_live_transactions(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        db = txn.open_db(b"drop-with-reader", create=True)
        txn.put(b"key", b"value", db)

    reader = env.read()
    try:
        with env.write() as writer:
            with pytest.raises(clibmdbx.BusyError):
                db.drop(writer, delete=True)
        assert reader.get(b"key", db) == b"value"
    finally:
        reader.abort()

    with env.write() as writer:
        db.drop(writer, delete=True)
    assert db.closed


def test_reverse_items_start_above_largest_key(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        for key in (b"a", b"b", b"c"):
            txn.put(key, key.upper())
    with env.read() as txn, txn.cursor() as cursor:
        assert cursor.items(start=b"z", reverse=True) == [(b"c", b"C"), (b"b", b"B"), (b"a", b"A")]


def test_cursor_can_be_closed_after_transaction_and_environment(env: clibmdbx.Environment) -> None:
    txn = env.read()
    cursor = txn.cursor()
    txn.abort()
    env.close()
    cursor.close()


def test_database_view_can_be_closed_after_environment(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        db = txn.open_db(b"close-after-environment", create=True)
    env.close()
    assert db.closed
    db.close()
    db.close()


@pytest.mark.parametrize("abort_nested", [False, True])
def test_rename_refreshes_table_after_aborted_write(tmp_path, abort_nested: bool) -> None:
    path = tmp_path / "rename-after-abort"
    expected = [(i.to_bytes(8, "big"), b"committed") for i in range(10)]
    with clibmdbx.Environment(path, max_dbs=4) as env:
        with env.write() as txn:
            db = txn.open_db(b"original", create=True)
            txn.put_many(expected, db)
        parent = env.write() if abort_nested else None
        txn = env.begin(write=True, parent=parent) if parent else env.write()
        for i in range(10, 5010):
            txn.put(i.to_bytes(8, "big"), b"x" * 200, db)
        txn.abort()
        if parent:
            parent.abort()
        # First native use of this DBI in the new write must be rename.
        # Reading/stat'ing it in the test first would hide the upstream bug.
        with env.write() as txn:
            db.rename(txn, b"renamed")
        with env.read() as txn, txn.cursor(db) as cursor:
            assert txn.stat(db)["entries"] == len(expected)
            assert list(cursor) == expected
    with clibmdbx.Environment(path) as reopened, reopened.read() as txn:
        db = txn.open_db(b"renamed")
        with txn.cursor(db) as cursor:
            assert list(cursor) == expected


def test_copy_overwrite_rejected_before_destination_is_touched(env, tmp_path) -> None:
    destination = tmp_path / "existing-backup"
    original = b"existing-backup-must-survive" * 1024
    destination.write_bytes(original)
    for flags in (clibmdbx.MDBX_CP_OVERWRITE, clibmdbx.MDBX_CP_OVERWRITE | clibmdbx.MDBX_CP_COMPACT):
        with pytest.raises(NotImplementedError, match="copy to a new path"):
            env.copy(destination, flags=flags)
        assert destination.read_bytes() == original
    missing = tmp_path / "missing-backup"
    with pytest.raises(NotImplementedError):
        env.copy(missing, flags=clibmdbx.MDBX_CP_OVERWRITE)
    assert not missing.exists()
