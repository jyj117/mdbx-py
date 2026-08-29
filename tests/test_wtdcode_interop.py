"""On-disk compatibility tests against the optional wtdcode ctypes package."""

from __future__ import annotations

import pytest

import clibmdbx

mdbx = pytest.importorskip(
    "mdbx",
    reason="install the pinned libmdbx==0.3.2 reference package for interop tests",
)

pytestmark = pytest.mark.ctypes_reference

REFERENCE_GEOMETRY = mdbx.Geometry(
    size_now=16 << 20,
    size_upper=256 << 20,
    growth_step=1 << 20,
    shrink_threshold=2 << 20,
)
NATIVE_GEOMETRY = (-1, 16 << 20, 256 << 20, 1 << 20, 2 << 20, -1)


def test_ctypes_creates_clibmdbx_reads_and_extends(tmp_path) -> None:
    path = tmp_path / "ctypes-first"
    path.mkdir()

    reference = mdbx.Env(path.as_posix(), maxdbs=8, geometry=REFERENCE_GEOMETRY)
    txn = reference.rw_transaction()
    default = txn.open_map()
    default.put(txn, b"default\x00key", b"ctypes\x00value")
    duplicates = txn.create_map(b"dups", mdbx.MDBXDBFlags.MDBX_DUPSORT)
    duplicates.put(txn, b"key", b"a")
    duplicates.put(txn, b"key", b"b")
    assert duplicates.get_sequence(txn, 3) == 0
    txn.commit()
    reference.close()

    with clibmdbx.Environment(path, max_dbs=8, geometry=NATIVE_GEOMETRY) as native:
        with native.read() as txn:
            db = txn.open_db(b"dups", flags=clibmdbx.MDBX_DUPSORT)
            assert txn.get(b"default\x00key") == b"ctypes\x00value"
            assert db.sequence(txn) == 3
            with txn.cursor(db) as cursor:
                assert cursor.items() == [(b"key", b"a"), (b"key", b"b")]
        with native.write() as txn:
            db = txn.open_db(b"dups", flags=clibmdbx.MDBX_DUPSORT)
            txn.put(b"key", b"c", db)
            assert db.sequence(txn, increment=4) == 3

    reference = mdbx.Env(path.as_posix(), maxdbs=8)
    txn = reference.ro_transaction()
    default = txn.open_map()
    duplicates = txn.open_map(b"dups", mdbx.MDBXDBFlags.MDBX_DUPSORT)
    assert default.get(txn, b"default\x00key") == b"ctypes\x00value"
    assert duplicates.get_sequence(txn, 0) == 7
    with txn.cursor(duplicates) as cursor:
        assert list(cursor.iter_dupsort()) == [(b"key", b"a"), (b"key", b"b"), (b"key", b"c")]
    txn.abort()
    reference.close()


def test_clibmdbx_creates_ctypes_reads_and_extends(tmp_path) -> None:
    path = tmp_path / "native-first"
    path.mkdir()

    with clibmdbx.Environment(path, max_dbs=8, geometry=NATIVE_GEOMETRY) as native:
        with native.write() as txn:
            db = txn.open_db(b"named\x00map", create=True)
            txn.put(b"key\x00", b"native\x00value", db)
            db.sequence(txn, increment=11)

    reference = mdbx.Env(path.as_posix(), maxdbs=8)
    txn = reference.ro_transaction()
    db = txn.open_map(b"named\x00map")
    assert db.get(txn, b"key\x00") == b"native\x00value"
    assert db.get_sequence(txn, 0) == 11
    txn.abort()
    txn = reference.rw_transaction()
    db = txn.open_map(b"named\x00map")
    db.put(txn, b"from-ctypes", b"roundtrip")
    txn.commit()
    reference.close()

    with clibmdbx.Environment(path, max_dbs=8) as native:
        with native.read() as txn:
            db = txn.open_db(b"named\x00map")
            assert txn.get(b"key\x00", db) == b"native\x00value"
            assert txn.get(b"from-ctypes", db) == b"roundtrip"
            assert db.sequence(txn) == 11
