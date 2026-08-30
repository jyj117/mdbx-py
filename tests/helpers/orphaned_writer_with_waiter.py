"""Regression child for an already-waiting writer whose owner exits."""

from __future__ import annotations

import gc
import json
import os
import queue
import sys
import threading
import time
import warnings
from pathlib import Path

import clibmdbx


def main(path: Path) -> int:
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
        else:  # pragma: no cover - reported to parent
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

    # The off-owner finalizer releases the binding-level root-writer gate. The
    # waiter must observe BusyError before entering libmdbx and must never stay
    # blocked when the owner exits without reaping.
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


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
