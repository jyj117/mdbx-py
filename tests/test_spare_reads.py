from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import clibmdbx

HELPER = Path(__file__).with_name("helpers") / "reader_capacity.py"


def capacity(path: Path) -> int:
    result = subprocess.run([sys.executable, str(HELPER), str(path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["capacity"]


@pytest.mark.parametrize("spares", [0, 1])
def test_spare_is_bounded_optional_and_released_for_public_reads(tmp_path: Path, spares: int) -> None:
    path = tmp_path / "db"
    with clibmdbx.Environment(path, max_readers=8, max_spare_txns=spares) as env:
        total = env.info()["max_readers"]
        for _ in range(100):
            assert env.get(b"absent") is None
        assert capacity(path) == total - spares
        readers = []
        try:
            for _ in range(total):
                readers.append(env.read())
            with pytest.raises(clibmdbx.ReadersFullError):
                env.read()
            assert env.reader_check() == 0
        finally:
            for txn in readers:
                txn.abort()
        assert env.get(b"absent") is None
        assert capacity(path) == total - spares
        assert env.reader_check() == 0
        assert capacity(path) == total
    with clibmdbx.Environment(path, max_readers=8, max_spare_txns=0) as reopened:
        assert capacity(path) == reopened.info()["max_readers"]


def test_spare_renews_external_commits_without_retaining_old_pages(tmp_path: Path) -> None:
    path = tmp_path / "db"
    with clibmdbx.Environment(path, max_readers=8, geometry=(0, 1 << 20, 8 << 20, 1 << 20, 0, -1)) as env:
        with env.write() as txn:
            txn.put(b"key", b"before")
        before = env.get(b"key")
        assert capacity(path) == env.info()["max_readers"] - 1
        result = subprocess.run(
            [sys.executable, str(HELPER), str(path), "write"], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr
        assert env.get(b"key") == (255).to_bytes(4, "big") + bytes(65532)
        assert before == b"before"


def test_management_copy_reclaims_idle_slot_under_other_process_pressure(tmp_path: Path) -> None:
    path = tmp_path / "db"
    with clibmdbx.Environment(path, max_readers=8) as env:
        assert env.get(b"missing") is None
        holder = subprocess.Popen(
            [sys.executable, str(HELPER), str(path), "hold"],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            ready = json.loads(holder.stdout.readline())
            assert ready["capacity"] == env.info()["max_readers"] - 1
            backup = tmp_path / "backup"
            env.copy(backup)
            with clibmdbx.Environment(backup, readonly=True) as copied:
                assert copied.get(b"missing") is None
        finally:
            _, error = holder.communicate("release\n", timeout=15)
            assert holder.returncode == 0, error


@pytest.mark.parametrize("value", [-1, 2])
def test_invalid_spare_limit(tmp_path: Path, value: int) -> None:
    with pytest.raises(ValueError, match="0 or 1"):
        clibmdbx.Environment(tmp_path / "db", max_spare_txns=value)


def test_spare_native_error_and_cross_thread_close(env: clibmdbx.Environment) -> None:
    db = env.open_db(b"integer", create=True, flags=clibmdbx.MDBX_INTEGERKEY)
    key = (1).to_bytes(4, sys.byteorder)
    with env.write() as txn:
        txn.put(key, b"value", db)
    assert env.get(key, db) == b"value"
    with pytest.raises(clibmdbx.BadValueSizeError):
        env.get(b"invalid-key-size", db)
    assert env.get(key, db) == b"value"
    errors = []

    def close() -> None:
        try:
            env.close()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=close)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    assert env.closed


def test_spare_deallocation_on_another_thread_releases_native_environment(tmp_path: Path) -> None:
    path = tmp_path / "db"
    env = clibmdbx.Environment(path)
    assert env.get(b"missing") is None
    holder = [env]
    del env
    thread = threading.Thread(target=holder.clear)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    with clibmdbx.Environment(path) as reopened:
        assert reopened.get(b"missing") is None


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_inherited_spare_is_never_reused_or_aborted_in_child(env: clibmdbx.Environment) -> None:
    assert env.get(b"missing") is None
    pid = os.fork()
    if pid == 0:
        try:
            try:
                env.get(b"missing")
            except clibmdbx.ForkError:
                env.close()
                os._exit(0)
            os._exit(2)
        except BaseException:
            os._exit(3)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert env.get(b"missing") is None
