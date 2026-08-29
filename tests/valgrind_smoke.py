"""Small native lifecycle workload suitable for Valgrind's high overhead."""

from __future__ import annotations

import gc
import tempfile

import clibmdbx

with tempfile.TemporaryDirectory(dir="/tmp") as directory:
    env = clibmdbx.Environment(directory, max_dbs=4)
    with env.write() as txn:
        db = txn.open_db(b"dups", create=True, flags=clibmdbx.MDBX_DUPSORT)
        for index in range(1000):
            txn.put(index.to_bytes(4, "big"), b"value")
            txn.put(b"same", index.to_bytes(4, "big"), db, clibmdbx.MDBX_NODUPDATA)
    with env.read() as txn:
        assert txn.get_many([index.to_bytes(4, "big") for index in range(1000)]) == [b"value"] * 1000
        with txn.cursor(db) as cursor:
            assert cursor.set(b"same") == (b"same", b"\0\0\0\0")
            assert cursor.count() == 1000
    db.close()
    env.close()
gc.collect()
