"""Regression child for writer-owner thread lifetime reuse."""

from __future__ import annotations

import gc
import json
import os
import queue
import sys
import threading
import warnings
from pathlib import Path

import clibmdbx


def main(path: Path) -> int:
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

    failures: queue.Queue[str] = queue.Queue()

    def replacement_thread() -> None:
        try:
            env.write()
        except clibmdbx.BusyError:
            return
        except BaseException as exc:  # pragma: no cover - reported to parent
            failures.put(type(exc).__name__)
        else:  # pragma: no cover - reported to parent
            failures.put("write unexpectedly succeeded")

    # CPython and pthread implementations commonly reuse the just-finished
    # owner's numeric thread ID here.  Lifetime tokens must still reject every
    # replacement without ever calling mdbx_txn_abort() on the wrong owner.
    for _ in range(64):
        replacement = threading.Thread(target=replacement_thread)
        replacement.start()
        replacement.join()

    checks = {
        "warnings": [str(item.message) for item in captured],
        "orphaned": env.orphaned_write_transactions,
        "replacement_failures": list(failures.queue),
        "info_succeeded": env.info()["recent_txnid"] >= 0,
        "reader_check": env.reader_check(),
    }
    for name, call in {
        "read": env.read,
        "write": env.write,
        "get": lambda: env.get(b"uncommitted"),
        "sync": env.sync,
    }.items():
        try:
            call()
        except clibmdbx.BusyError:
            checks[f"{name}_busy"] = True
        else:  # pragma: no cover - reported to parent
            checks[f"{name}_busy"] = False
    try:
        env.close()
    except clibmdbx.BusyError:
        checks["close_busy"] = True
    else:  # pragma: no cover - reported to parent
        checks["close_busy"] = False
    print(json.dumps(checks, sort_keys=True), flush=True)
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
