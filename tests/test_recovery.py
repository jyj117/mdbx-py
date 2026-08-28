from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

import clibmdbx


def run_crashing_writer(path: pathlib.Path, *, commit: bool) -> None:
    code = """
import os, sys, clibmdbx
env = clibmdbx.Environment(sys.argv[1])
txn = env.write()
txn.put(b'crash-key', sys.argv[2].encode())
if sys.argv[3] == 'commit':
    txn.commit()
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(path), "committed" if commit else "uncommitted", "commit" if commit else "exit"],
        check=False,
    )
    assert result.returncode == 0


def test_crash_recovery_commit_and_rollback(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "crash"
    path.mkdir()
    run_crashing_writer(path, commit=True)
    env = clibmdbx.Environment(path)
    try:
        with env.read() as txn:
            assert txn.get(b"crash-key") == b"committed"
    finally:
        env.close()

    run_crashing_writer(path, commit=False)
    env = clibmdbx.Environment(path)
    try:
        with env.read() as txn:
            assert txn.get(b"crash-key") == b"committed"
    finally:
        env.close()


def test_large_value_roundtrip(env: clibmdbx.Environment) -> None:
    value = os.urandom(4 << 20)
    with env.write() as txn:
        txn.put(b"large", value)
    with env.read() as txn:
        assert txn.get(b"large") == value


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_readonly_permissions(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "permissions"
    path.mkdir()
    env = clibmdbx.Environment(path)
    with env.write() as txn:
        txn.put(b"key", b"value")
    env.close()
    data = path / "mdbx.dat"
    lock = path / "mdbx.lck"
    old_data = data.stat().st_mode
    old_lock = lock.stat().st_mode
    try:
        data.chmod(0o400)
        lock.chmod(0o400)
        path.chmod(0o500)
        # EXCLUSIVE is the upstream mode that does not require a writable
        # shared lock file on genuinely read-only media.
        readonly = clibmdbx.Environment(path, readonly=True, flags=clibmdbx.MDBX_EXCLUSIVE)
        try:
            with readonly.read() as txn:
                assert txn.get(b"key") == b"value"
        finally:
            readonly.close()
    finally:
        path.chmod(0o700)
        data.chmod(old_data)
        lock.chmod(old_lock)
