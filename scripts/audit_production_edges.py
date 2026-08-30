"""Quantitative production-edge audit for an installed clibmdbx build.

The audit intentionally exercises writer contention, reader-slot exhaustion,
map growth/recovery, MVCC retention, cross-thread finalization and close
latency.  It writes only below a temporary root and prints one JSON result.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path

import clibmdbx

MIB = 1 << 20


def _mkdir(path: Path) -> Path:
    path.mkdir()
    return path


def _exception(exc: BaseException) -> dict[str, object]:
    return {
        "type": type(exc).__name__,
        "code": getattr(exc, "code", None),
        "what": getattr(exc, "what", None),
        "reason": getattr(exc, "reason", None),
    }


def audit_writer_wait(root: Path) -> dict[str, object]:
    env = clibmdbx.Environment(_mkdir(root / "writer-wait"), geometry=(0, MIB, 128 * MIB, MIB, 2 * MIB, -1))
    holder = env.write()
    holder.put(b"seed", b"value")
    waiters = 8
    entered: queue.Queue[int] = queue.Queue()
    waits: queue.Queue[float] = queue.Queue()
    errors: queue.Queue[str] = queue.Queue()

    def waiter(index: int) -> None:
        entered.put(index)
        started = time.perf_counter()
        try:
            with env.write() as txn:
                waits.put(time.perf_counter() - started)
                txn.put(f"waiter:{index}".encode(), b"done")
        except BaseException as exc:
            errors.put(repr(exc))

    threads = [threading.Thread(target=waiter, args=(index,)) for index in range(waiters)]
    for thread in threads:
        thread.start()
    for _ in threads:
        entered.get(timeout=2)
    time.sleep(0.25)
    holder.commit()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads), "a waiting writer did not make progress"
    assert errors.empty(), list(errors.queue)
    measured = sorted(waits.queue)
    assert len(measured) == waiters
    assert measured[0] >= 0.15, measured
    assert measured[-1] < 5.0, measured

    busy_holder = env.write()
    try_result: queue.Queue[tuple[float, str]] = queue.Queue()

    def try_writer() -> None:
        started = time.perf_counter()
        try:
            env.begin(write=True, flags=clibmdbx.MDBX_TXN_TRY)
        except BaseException as exc:
            try_result.put((time.perf_counter() - started, type(exc).__name__))

    trying = threading.Thread(target=try_writer)
    trying.start()
    trying.join(timeout=2)
    assert not trying.is_alive()
    try_latency, try_exception = try_result.get_nowait()
    assert try_exception == "BusyError"
    assert try_latency < 0.5
    busy_holder.abort()

    stop = threading.Event()
    reader_errors: queue.Queue[str] = queue.Queue()
    read_counts = [0] * 8

    def reader(index: int) -> None:
        try:
            while not stop.is_set():
                assert env.get(b"seed") == b"value"
                read_counts[index] += 1
        except BaseException as exc:
            reader_errors.put(repr(exc))

    readers = [threading.Thread(target=reader, args=(index,)) for index in range(len(read_counts))]
    for thread in readers:
        thread.start()
    deadline = time.monotonic() + 2
    while min(read_counts) == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    started = time.perf_counter()
    with env.write() as txn:
        writer_under_reads_latency = time.perf_counter() - started
        txn.put(b"seed", b"value")
    stop.set()
    for thread in readers:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in readers)
    assert reader_errors.empty(), list(reader_errors.queue)
    assert writer_under_reads_latency < 2.0

    active = env.read()
    started = time.perf_counter()
    try:
        env.close()
    except clibmdbx.BusyError:
        busy_close_latency = time.perf_counter() - started
    else:
        raise AssertionError("close accepted an active transaction")
    assert busy_close_latency < 0.5
    active.abort()
    started = time.perf_counter()
    env.close()
    close_latency = time.perf_counter() - started
    env.close()
    return {
        "waiter_count": waiters,
        "wait_min_s": measured[0],
        "wait_max_s": measured[-1],
        "try_writer_latency_s": try_latency,
        "reader_ops": sum(read_counts),
        "writer_under_reader_storm_s": writer_under_reads_latency,
        "busy_close_latency_s": busy_close_latency,
        "clean_close_latency_s": close_latency,
    }


def audit_map_geometry(root: Path) -> dict[str, object]:
    path = _mkdir(root / "map-geometry")
    env = clibmdbx.Environment(path, geometry=(0, MIB, MIB, 0, 0, -1))
    env_open = True
    try:
        txn = env.write()
        records_before_full = 0
        caught: BaseException | None = None
        try:
            while records_before_full < 10_000:
                txn.put(records_before_full.to_bytes(8, "little"), bytes(64 << 10))
                records_before_full += 1
        except BaseException as exc:
            caught = exc
        finally:
            if txn.active:
                txn.abort()
        assert isinstance(caught, clibmdbx.MapFullError), _exception(caught) if caught else None
        fixed = env.info()
        assert fixed["geometry"]["current"] <= fixed["geometry"]["upper"] == MIB

        online_growth = True
        online_growth_error: dict[str, object] | None = None
        try:
            env.set_geometry((0, MIB, 32 * MIB, MIB, 2 * MIB, -1))
        except clibmdbx.UnableExtendMapError as exc:
            # Windows may be unable to extend an existing virtual mapping.  The
            # supported recovery is to close all handles and reopen with the
            # larger upper bound; production deployments should pre-provision it.
            online_growth = False
            online_growth_error = _exception(exc)
            env.close()
            env_open = False
            env = clibmdbx.Environment(
                path,
                geometry=(0, MIB, 32 * MIB, MIB, 2 * MIB, -1),
            )
            env_open = True

        with env.write() as writer:
            for index in range(96):
                writer.put(f"grown:{index:08d}".encode(), bytes(64 << 10))
        grown = env.info()
        assert fixed["geometry"]["current"] < grown["geometry"]["current"] <= grown["geometry"]["upper"]
        assert grown["file_size"] <= grown["map_size"] <= grown["geometry"]["upper"]
        usage = shutil.disk_usage(path)
        started = time.perf_counter()
        env.close()
        env_open = False
        close_latency = time.perf_counter() - started
    finally:
        if env_open:
            env.close()

    reopened = clibmdbx.Environment(path, geometry=(0, -1, 32 * MIB, MIB, 2 * MIB, -1))
    try:
        assert reopened.get(b"grown:00000095") == bytes(64 << 10)
    finally:
        reopened.close()
    return {
        "records_before_map_full": records_before_full,
        "map_full": _exception(caught),
        "online_growth": online_growth,
        "online_growth_error": online_growth_error,
        "fixed": fixed,
        "grown": grown,
        "disk_free_bytes": usage.free,
        "geometry_upper_to_free_ratio": grown["geometry"]["upper"] / usage.free,
        "close_latency_s": close_latency,
    }


def _crash_reader(path: Path) -> int:
    env = clibmdbx.Environment(path, max_readers=4)
    txn = env.read()
    assert txn.active
    print("reader-ready", flush=True)
    os._exit(0)


def audit_reader_slots(root: Path, script: Path) -> dict[str, object]:
    path = _mkdir(root / "reader-slots")
    env = clibmdbx.Environment(path, max_readers=4)
    live = []
    caught: BaseException | None = None
    try:
        while len(live) < 1024:
            live.append(env.read())
    except BaseException as exc:
        caught = exc
    assert isinstance(caught, clibmdbx.ReadersFullError), _exception(caught) if caught else None
    capacity = len(live)
    assert capacity >= 1
    assert env.reader_check() == 0, "reader_check must not clear live transactions"
    for txn in live:
        txn.abort()
    with env.read():
        pass

    reset = [env.read() for _ in range(capacity)]
    for txn in reset:
        txn.reset()
    reset_caught: BaseException | None = None
    try:
        env.read()
    except BaseException as exc:
        reset_caught = exc
    assert isinstance(reset_caught, clibmdbx.ReadersFullError)
    assert env.reader_check() == 0
    for txn in reset:
        txn.abort()

    result = subprocess.run(
        [sys.executable, str(script), "--crash-reader", str(path)],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "reader-ready" in result.stdout
    stale = 0
    deadline = time.monotonic() + 3
    while stale == 0 and time.monotonic() < deadline:
        stale += env.reader_check()
        if stale == 0:
            time.sleep(0.01)
    assert stale >= 1, "reader_check did not clear the crashed process slot"
    with env.read():
        pass
    env.close()
    return {
        "reader_capacity": capacity,
        "readers_full": _exception(caught),
        "reset_handles_still_reserve_slots": isinstance(reset_caught, clibmdbx.ReadersFullError),
        "stale_slots_cleared": stale,
    }


def audit_long_reader(root: Path) -> dict[str, object]:
    path = _mkdir(root / "long-reader")
    env = clibmdbx.Environment(path, geometry=(0, 4 * MIB, 256 * MIB, 4 * MIB, 8 * MIB, -1))
    old = None
    try:
        keys = [f"page:{index:04d}".encode() for index in range(64)]
        with env.write() as txn:
            txn.put_many([(key, bytes(32 << 10)) for key in keys])
        old = env.read()
        assert old.get(keys[0]) == bytes(32 << 10)
        before = env.info()
        commits = 24
        for generation in range(1, commits + 1):
            payload = bytes([generation]) * (32 << 10)
            with env.write() as txn:
                txn.put_many([(key, payload) for key in keys])
        old_info = old.info(scan_readers=True)
        with env.write() as txn:
            retained = txn.gc_info()
        during = env.info()
        assert old_info["reader_lag"] >= commits
        assert old_info["space_retired"] > 0
        assert during["file_size"] >= before["file_size"]
        old.abort()
        old = None

        for generation in range(commits + 1, commits * 2 + 1):
            payload = bytes([generation]) * (32 << 10)
            with env.write() as txn:
                txn.put_many([(key, payload) for key in keys])
        with env.write() as txn:
            after_gc = txn.gc_info()
        after = env.info()
    finally:
        if old is not None and old.active:
            old.abort()
        env.close()
    return {
        "commits_while_old_reader_live": commits,
        "old_reader_info": old_info,
        "gc_during": retained,
        "gc_after": after_gc,
        "file_size_before": before["file_size"],
        "file_size_during": during["file_size"],
        "file_size_after_reuse": after["file_size"],
        "map_size_during": during["map_size"],
        "map_size_after_reuse": after["map_size"],
    }


def _orphan_owner_exit(path: Path) -> int:
    env = clibmdbx.Environment(path)
    handoff: queue.Queue[object] = queue.Queue()

    def owner() -> None:
        txn = env.write()
        txn.put(b"uncommitted", b"must-disappear")
        handoff.put(txn)

    thread = threading.Thread(target=owner)
    thread.start()
    thread.join()
    txn = handoff.get()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", ResourceWarning)
        del txn
        gc.collect()
    warning_text = [str(item.message) for item in captured]
    orphaned_before_reap = env.orphaned_write_transactions
    reaped = env.reap_orphaned_transactions()

    try_started = time.perf_counter()
    try_exception: BaseException | None = None
    try:
        env.begin(write=True, flags=clibmdbx.MDBX_TXN_TRY)
    except BaseException as exc:
        try_exception = exc
    try_latency = time.perf_counter() - try_started

    default_result: queue.Queue[BaseException | None] = queue.Queue()

    def blocking_writer() -> None:
        try:
            env.write()
        except BaseException as exc:
            default_result.put(exc)
        else:
            default_result.put(None)

    waiting = threading.Thread(target=blocking_writer, daemon=True)
    waiting.start()
    waiting.join(timeout=0.25)
    default_write_blocked = waiting.is_alive()
    default_write_exception: BaseException | None = None
    if not default_write_blocked:
        default_write_exception = default_result.get_nowait()

    read_exception: BaseException | None = None
    try:
        env.get(b"uncommitted")
    except BaseException as exc:
        read_exception = exc
    info_succeeded = env.info()["recent_txnid"] >= 0
    reader_check_result = env.reader_check()

    close_exception: BaseException | None = None
    try:
        env.close()
    except BaseException as exc:
        close_exception = exc
    print(
        json.dumps(
            {
                "warnings": warning_text,
                "orphaned_before_reap": orphaned_before_reap,
                "orphaned_after_reap": env.orphaned_write_transactions,
                "reaped": reaped,
                "try_exception": _exception(try_exception) if try_exception else None,
                "try_latency_s": try_latency,
                "default_write_blocked": default_write_blocked,
                "default_write_exception": _exception(default_write_exception) if default_write_exception else None,
                "new_read_exception": _exception(read_exception) if read_exception else None,
                "info_succeeded": info_succeeded,
                "reader_check_result": reader_check_result,
                "close_exception": _exception(close_exception) if close_exception else None,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    os._exit(0)


def _orphan_waiter_owner_exit(path: Path) -> int:
    env = clibmdbx.Environment(path)
    handoff: queue.Queue[object] = queue.Queue()
    owner_exit = threading.Event()

    def owner() -> None:
        txn = env.write()
        txn.put(b"uncommitted", b"must-disappear")
        handoff.put(txn)
        del txn
        owner_exit.wait()

    owner_thread = threading.Thread(target=owner)
    owner_thread.start()
    txn = handoff.get(timeout=5)
    waiter_entered = threading.Event()
    waiter_result: queue.Queue[tuple[str, float]] = queue.Queue()

    def waiter() -> None:
        waiter_entered.set()
        started = time.perf_counter()
        try:
            env.write()
        except BaseException as exc:
            waiter_result.put((type(exc).__name__, time.perf_counter() - started))
        else:
            waiter_result.put(("acquired", time.perf_counter() - started))

    waiting_thread = threading.Thread(target=waiter, daemon=True)
    waiting_thread.start()
    assert waiter_entered.wait(timeout=2)
    time.sleep(0.25)
    waiter_was_waiting = waiting_thread.is_alive() and waiter_result.empty()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", ResourceWarning)
        del txn
        gc.collect()
    owner_exit.set()
    owner_thread.join(timeout=5)
    waiting_thread.join(timeout=5)
    waiter_completed = not waiting_thread.is_alive()
    waiter_exception = None
    waiter_latency = None
    if not waiter_result.empty():
        waiter_exception, waiter_latency = waiter_result.get_nowait()
    print(
        json.dumps(
            {
                "warnings": [str(item.message) for item in captured],
                "orphaned": env.orphaned_write_transactions,
                "waiter_was_waiting": waiter_was_waiting,
                "waiter_completed": waiter_completed,
                "waiter_exception": waiter_exception,
                "waiter_latency_s": waiter_latency,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    os._exit(0)


def audit_resource_warning(root: Path, script: Path) -> dict[str, object]:
    path = _mkdir(root / "resource-warning-recoverable")
    env = clibmdbx.Environment(path)
    handoff: queue.Queue[object] = queue.Queue()
    txn = env.write()
    txn.put(b"must-rollback", b"value")
    handoff.put(txn)
    del txn
    warning_messages: queue.Queue[list[str]] = queue.Queue()

    def finalize() -> None:
        orphan = handoff.get()
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", ResourceWarning)
            del orphan
            gc.collect()
        warning_messages.put([str(item.message) for item in captured])

    worker = threading.Thread(target=finalize)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    messages = warning_messages.get_nowait()
    assert any("write transaction was finalized" in message for message in messages), messages
    assert env.reap_orphaned_transactions() == 1
    assert env.get(b"must-rollback") is None
    with env.write() as writer:
        writer.put(b"recovered", b"yes")
    env.close()

    fatal_path = _mkdir(root / "resource-warning-owner-exit")
    result = subprocess.run(
        [sys.executable, str(script), "--orphan-owner-exit", str(fatal_path)],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    fatal = json.loads(result.stdout.strip().splitlines()[-1])
    assert fatal["warnings"]
    assert fatal["orphaned_before_reap"] == 1
    assert fatal["orphaned_after_reap"] == 1
    assert fatal["reaped"] == 0
    assert fatal["try_exception"]["type"] == "BusyError"
    assert fatal["default_write_blocked"] is False
    assert fatal["default_write_exception"]["type"] == "BusyError"
    assert fatal["new_read_exception"]["type"] == "BusyError"
    assert fatal["info_succeeded"] is True
    assert fatal["reader_check_result"] == 0
    assert fatal["close_exception"]["type"] == "BusyError"
    reopened = clibmdbx.Environment(fatal_path)
    assert reopened.get(b"uncommitted") is None
    reopened.close()

    waiting_path = _mkdir(root / "resource-warning-owner-exit-with-waiter")
    waiting_result = subprocess.run(
        [sys.executable, str(script), "--orphan-waiter-owner-exit", str(waiting_path)],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert waiting_result.returncode == 0, waiting_result.stdout + waiting_result.stderr
    waiting = json.loads(waiting_result.stdout.strip().splitlines()[-1])
    assert waiting["warnings"]
    assert waiting["orphaned"] == 1
    assert waiting["waiter_was_waiting"] is True
    assert waiting["waiter_completed"] is True
    assert waiting["waiter_exception"] in {"BusyError", "PanicError"}
    assert waiting["waiter_latency_s"] < 10.0
    reopened = clibmdbx.Environment(waiting_path)
    assert reopened.get(b"uncommitted") is None
    reopened.close()
    return {
        "recoverable_warning": messages,
        "owner_thread_exit": fatal,
        "owner_thread_exit_with_waiting_writer": waiting,
        "reopen_after_process_exit": True,
    }


def run(root: Path, script: Path) -> dict[str, object]:
    started = time.perf_counter()
    result = {
        "binding_version": clibmdbx.__version__,
        "diagnostics": clibmdbx.diagnostics(),
        "writer_wait": audit_writer_wait(root),
        "map_geometry": audit_map_geometry(root),
        "reader_slots": audit_reader_slots(root, script),
        "long_reader": audit_long_reader(root),
        "resource_warning": audit_resource_warning(root, script),
    }
    result["elapsed_s"] = time.perf_counter() - started
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--crash-reader", type=Path)
    parser.add_argument("--orphan-owner-exit", type=Path)
    parser.add_argument("--orphan-waiter-owner-exit", type=Path)
    args = parser.parse_args()
    if args.crash_reader is not None:
        return _crash_reader(args.crash_reader)
    if args.orphan_owner_exit is not None:
        return _orphan_owner_exit(args.orphan_owner_exit)
    if args.orphan_waiter_owner_exit is not None:
        return _orphan_waiter_owner_exit(args.orphan_waiter_owner_exit)
    script = Path(__file__).resolve()
    if args.root is not None:
        args.root.mkdir(parents=True, exist_ok=False)
        print(json.dumps(run(args.root, script), indent=2, sort_keys=True))
        return 0
    with tempfile.TemporaryDirectory(prefix="clibmdbx-production-audit-", ignore_cleanup_errors=True) as directory:
        print(json.dumps(run(Path(directory), script), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
