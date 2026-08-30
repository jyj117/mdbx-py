"""Small native lifecycle workload suitable for Valgrind's high overhead."""

from __future__ import annotations

import gc
import queue
import tempfile
import threading

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

with tempfile.TemporaryDirectory(dir="/tmp") as directory:
    env = clibmdbx.Environment(directory)
    owner = env.write()
    owner.put(b"orphaned", b"rollback")
    handoff: queue.Queue[object] = queue.Queue()
    handoff.put(owner)
    del owner
    entered = threading.Event()
    result: queue.Queue[str] = queue.Queue()

    def waiting_sync() -> None:
        entered.set()
        try:
            env.sync(force=True)
        except BaseException as exc:
            result.put(type(exc).__name__)

    waiter = threading.Thread(target=waiting_sync)
    waiter.start()
    assert entered.wait(timeout=5)

    def off_owner_finalizer() -> None:
        orphan = handoff.get()
        del orphan
        gc.collect()

    finalizer = threading.Thread(target=off_owner_finalizer)
    finalizer.start()
    finalizer.join(timeout=10)
    waiter.join(timeout=10)
    assert not finalizer.is_alive()
    assert not waiter.is_alive()
    assert result.get_nowait() == "BusyError"
    assert env.reap_orphaned_transactions() == 1
    assert env.get(b"orphaned") is None
    env.close()
gc.collect()
