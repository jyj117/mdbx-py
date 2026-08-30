from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import clibmdbx

MIB = 1 << 20


def test_waiting_writer_try_mode_and_close_latency_are_bounded(tmp_path: Path) -> None:
    path = tmp_path / "writer-wait"
    path.mkdir()
    env = clibmdbx.Environment(path, geometry=(0, MIB, 64 * MIB, MIB, 2 * MIB, -1))
    try:
        holder = env.write()
        entered = threading.Event()
        result: queue.Queue[tuple[str, float]] = queue.Queue()

        def waiter() -> None:
            entered.set()
            started = time.perf_counter()
            try:
                with env.write() as txn:
                    txn.put(b"waited", b"yes")
            except BaseException as exc:  # pragma: no cover - surfaced below
                result.put((type(exc).__name__, time.perf_counter() - started))
            else:
                result.put(("ok", time.perf_counter() - started))

        waiting = threading.Thread(target=waiter)
        waiting.start()
        assert entered.wait(timeout=2)
        time.sleep(0.05)
        assert waiting.is_alive()

        try_result: queue.Queue[tuple[str, float]] = queue.Queue()

        def try_writer() -> None:
            started = time.perf_counter()
            try:
                env.begin(write=True, flags=clibmdbx.MDBX_TXN_TRY)
            except BaseException as exc:
                try_result.put((type(exc).__name__, time.perf_counter() - started))

        trying = threading.Thread(target=try_writer)
        trying.start()
        trying.join(timeout=2)
        assert not trying.is_alive()
        exception, try_latency = try_result.get_nowait()
        assert exception == "BusyError"
        assert try_latency < 1.0

        close_started = time.perf_counter()
        with pytest.raises(clibmdbx.BusyError):
            env.close()
        assert time.perf_counter() - close_started < 1.0

        holder.abort()
        waiting.join(timeout=5)
        assert not waiting.is_alive()
        outcome, wait_latency = result.get_nowait()
        assert outcome == "ok"
        assert 0.04 <= wait_latency < 5.0
        assert env.get(b"waited") == b"yes"
    finally:
        if not env.closed:
            env.close()


def test_map_full_recovery_preserves_atomicity_and_larger_geometry(tmp_path: Path) -> None:
    path = tmp_path / "map-full"
    path.mkdir()
    env = clibmdbx.Environment(path, geometry=(0, MIB, MIB, 0, 0, -1))
    env_open = True
    try:
        txn = env.write()
        with pytest.raises(clibmdbx.MapFullError):
            for index in range(10_000):
                txn.put(index.to_bytes(8, "little"), bytes(64 << 10))
        txn.abort()
        assert env.get((0).to_bytes(8, "little")) is None
        assert env.info()["geometry"]["upper"] == MIB

        try:
            env.set_geometry((0, MIB, 32 * MIB, MIB, 2 * MIB, -1))
        except clibmdbx.UnableExtendMapError:
            env.close()
            env_open = False
            env = clibmdbx.Environment(path, geometry=(0, MIB, 32 * MIB, MIB, 2 * MIB, -1))
            env_open = True

        with env.write() as writer:
            for index in range(96):
                writer.put(f"grown:{index:08d}".encode(), bytes(64 << 10))
        info = env.info()
        assert info["file_size"] <= info["map_size"] <= info["geometry"]["upper"]
        assert info["geometry"]["upper"] == 32 * MIB
    finally:
        if env_open:
            env.close()

    reopened = clibmdbx.Environment(path, geometry=(0, -1, 32 * MIB, MIB, 2 * MIB, -1))
    try:
        assert reopened.get(b"grown:00000095") == bytes(64 << 10)
    finally:
        reopened.close()


def test_reader_check_clears_reader_left_by_crashed_process(tmp_path: Path) -> None:
    path = tmp_path / "crashed-reader"
    path.mkdir()
    env = clibmdbx.Environment(path, max_readers=8)
    helper = Path(__file__).with_name("helpers") / "crashed_reader.py"
    try:
        result = subprocess.run(
            [sys.executable, str(helper), str(path)],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "reader-ready" in result.stdout
        stale = env.reader_check()
        assert stale >= 1
        assert env.reader_check() == 0
        with env.read():
            pass
    finally:
        env.close()


def test_long_reader_exposes_lag_and_retained_space_then_pages_are_reusable(tmp_path: Path) -> None:
    path = tmp_path / "long-reader"
    path.mkdir()
    env = clibmdbx.Environment(path, geometry=(0, 4 * MIB, 128 * MIB, 4 * MIB, 8 * MIB, -1))
    keys = [f"page:{index:04d}".encode() for index in range(32)]
    old = None
    try:
        with env.write() as txn:
            txn.put_many([(key, bytes(16 << 10)) for key in keys])
        old = env.read()
        assert old.get(keys[0]) == bytes(16 << 10)
        commits = 16
        for generation in range(1, commits + 1):
            with env.write() as txn:
                txn.put_many([(key, bytes([generation]) * (16 << 10)) for key in keys])

        old_info = old.info(scan_readers=True)
        with env.write() as txn:
            gc_during = txn.gc_info()
        file_size_during = env.info()["file_size"]
        assert old_info["reader_lag"] >= commits
        assert old_info["space_retired"] > 0
        assert gc_during["pages_gc"] > gc_during["pages_reclaimable"]

        old.abort()
        old = None
        for generation in range(commits + 1, commits * 2 + 1):
            with env.write() as txn:
                txn.put_many([(key, bytes([generation]) * (16 << 10)) for key in keys])
        with env.write() as txn:
            gc_after = txn.gc_info()
        file_size_after = env.info()["file_size"]
        assert gc_after["pages_gc"] == gc_after["pages_reclaimable"]
        assert file_size_after <= file_size_during + 8 * MIB
    finally:
        if old is not None and old.active:
            old.abort()
        env.close()
