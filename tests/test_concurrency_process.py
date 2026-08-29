from __future__ import annotations

import gc
import os
import queue
import subprocess
import sys
import threading

import pytest

import clibmdbx


def test_detached_batch_releases_gil_once(env: clibmdbx.Environment) -> None:
    keys = [f"detached-{index:06d}".encode() for index in range(50_000)]
    with env.write() as txn:
        txn.put_many(((key, b"value") for key in keys), detached=True)

    ready = threading.Event()
    start = threading.Event()
    ran_while_native_loop = threading.Event()

    def observer() -> None:
        ready.set()
        start.wait()
        ran_while_native_loop.set()

    thread = threading.Thread(target=observer)
    thread.start()
    assert ready.wait(timeout=2)
    previous_interval = sys.getswitchinterval()
    try:
        sys.setswitchinterval(1.0)
        start.set()
        with env.read() as txn:
            assert len(txn.get_many(keys, detached=True)) == len(keys)
        assert ran_while_native_loop.is_set()
    finally:
        sys.setswitchinterval(previous_interval)
        thread.join(timeout=2)


def test_environment_shared_readers(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        txn.put_many([(str(i).encode(), str(i * i).encode()) for i in range(200)])
    errors: queue.Queue[BaseException] = queue.Queue()

    def worker() -> None:
        try:
            for _ in range(50):
                with env.read() as txn:
                    assert txn.get(b"17") == b"289"
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.put(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors.empty(), list(errors.queue)


def test_set_option_does_not_deadlock_behind_writer_gil(tmp_path) -> None:
    code = r"""
import sys, threading, time
import clibmdbx

env = clibmdbx.Environment(sys.argv[1])
txn = env.write()
entered = threading.Event()
errors = []

def configure():
    entered.set()
    try:
        env.set_option(clibmdbx.MDBX_opt_sync_bytes, 4096)
    except BaseException as exc:
        errors.append(repr(exc))

thread = threading.Thread(target=configure)
thread.start()
assert entered.wait(1)
# Give the worker enough time to enter the native write-lock wait. If that
# call retains the GIL, this thread can never wake to release the writer.
time.sleep(0.2)
txn.commit()
thread.join(3)
assert not thread.is_alive(), "set_option remained deadlocked"
assert not errors, errors
env.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "set-option-lock")],
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_stat_does_not_deadlock_behind_writer_gil(tmp_path) -> None:
    code = r"""
import sys, threading, time
import clibmdbx

env = clibmdbx.Environment(sys.argv[1], flags=clibmdbx.MDBX_NOSTICKYTHREADS)
txn = env.write()
entered = threading.Event()
errors = []

def inspect():
    entered.set()
    try:
        assert env.stat()["entries"] == 0
    except BaseException as exc:
        errors.append(repr(exc))

thread = threading.Thread(target=inspect)
thread.start()
assert entered.wait(1)
time.sleep(0.2)
txn.commit()
thread.join(3)
assert not thread.is_alive(), "stat remained deadlocked"
assert not errors, errors
env.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "stat-lock")],
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_writer_owner_can_configure_with_open_cursor(env: clibmdbx.Environment) -> None:
    with env.write() as txn:
        cursor = txn.cursor()
        assert cursor.put(b"configured", b"safely")
        env.set_option(clibmdbx.MDBX_opt_sync_bytes, 4096)
        env.set_flags(clibmdbx.MDBX_SAFE_NOSYNC, False)
        assert cursor.current() == (b"configured", b"safely")
        cursor.close()


def test_transaction_and_cursor_thread_affinity(env: clibmdbx.Environment) -> None:
    txn = env.read()
    cur = txn.cursor()
    captured: list[BaseException] = []

    def worker() -> None:
        for call in (lambda: txn.get(b"x"), cur.first, lambda: txn.flags, txn.abort):
            try:
                call()
            except BaseException as exc:
                captured.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert [type(exc) for exc in captured] == [clibmdbx.ThreadError] * 4
    assert all(exc.code == 0 and exc.what == "" for exc in captured)
    cur.close()
    txn.abort()


def test_active_cursor_close_and_renew_are_owner_thread_only(env: clibmdbx.Environment) -> None:
    txn = env.read()
    cur = txn.cursor()
    captured: list[BaseException] = []

    def worker() -> None:
        for call in (cur.close, lambda: cur.renew(txn)):
            try:
                call()
            except BaseException as exc:
                captured.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert [type(exc) for exc in captured] == [clibmdbx.ThreadError] * 2
    cur.close()
    txn.abort()


def test_detached_batch_rejects_open_cursor_chain(env: clibmdbx.Environment) -> None:
    with env.write() as parent:
        parent_cursor = parent.cursor()
        with env.begin(write=True, parent=parent) as child:
            for call in (
                lambda: child.get_many([b"key"], detached=True),
                lambda: child.put_many([(b"key", b"value")], detached=True),
                lambda: child.delete_many([b"key"], detached=True),
            ):
                with pytest.raises(clibmdbx.BusyError, match="cursors.*closed"):
                    call()
        parent_cursor.close()


def test_commit_with_open_cursor_remains_safe(env: clibmdbx.Environment) -> None:
    txn = env.write()
    cur = txn.cursor()
    cur.put(b"cursor", b"commit")
    txn.commit()
    # libmdbx permits closing (or renewing) its reusable cursor after the
    # transaction has finished.
    cur.close()
    with env.read() as reader:
        assert reader.get(b"cursor") == b"commit"


def test_last_reference_can_be_finalized_on_another_thread(env: clibmdbx.Environment) -> None:
    handoff: queue.Queue[object] = queue.Queue()
    txn = env.read()
    cur = txn.cursor()
    handoff.put((txn, cur))
    del txn, cur

    def worker() -> None:
        objects = handoff.get()
        del objects
        gc.collect()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    env.close()


def test_write_finalized_on_another_thread_is_reaped_by_owner(env: clibmdbx.Environment) -> None:
    handoff: queue.Queue[object] = queue.Queue()
    txn = env.write()
    txn.put(b"must-rollback", b"value")
    handoff.put(txn)
    del txn

    def worker() -> None:
        orphan = handoff.get()
        del orphan
        gc.collect()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    # The non-owner destructor never unlocks the native writer.  Its owner can
    # explicitly reap it (ordinary subsequent environment use also reaps it).
    assert env.reap_orphaned_transactions() == 1
    assert env.reap_orphaned_transactions() == 0
    with env.read() as reader:
        assert reader.get(b"must-rollback") is None
    with env.write() as writer:
        writer.put(b"writer-still-works", b"yes")


@pytest.mark.fork
@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_forked_handles_are_rejected(env: clibmdbx.Environment) -> None:
    txn = env.read()
    cur = txn.cursor()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - separate process
        try:
            checks = 0
            for call in (env.info, lambda: txn.get(b"x"), cur.first, txn.abort, cur.close):
                try:
                    call()
                except clibmdbx.ForkError:
                    checks += 1
            os._exit(0 if checks == 5 else 3)
        except BaseException:
            os._exit(4)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    cur.close()
    txn.abort()
