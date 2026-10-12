"""Exercise clibmdbx against an intentionally capacity-limited filesystem.

Run this only in an isolated container or disposable filesystem.  The script
is used by the release audit to verify the exception raised after real ENOSPC.
"""

from __future__ import annotations

import json
import os
import sys

import clibmdbx


def main() -> int:
    root = sys.argv[1]
    os.mkdir(root)
    env = clibmdbx.Environment(
        root,
        geometry=(0, 1 << 20, 1 << 30, 1 << 20, 2 << 20, -1),
    )
    records = 0
    committed_records = 0
    caught: BaseException | None = None
    close_error: BaseException | None = None
    try:
        while True:
            with env.write() as txn:
                for _ in range(4):
                    txn.put(f"{records:08d}".encode(), bytes(262_144))
                    records += 1
            committed_records = records
    except BaseException as exc:
        caught = exc
    finally:
        try:
            env.close()
        except BaseException as exc:
            close_error = exc

    stat = os.statvfs(os.path.dirname(root))
    atomicity_verified = False
    with clibmdbx.Environment(root, readonly=True) as reopened:
        with reopened.read() as txn:
            for index in range(committed_records):
                assert txn.get(f"{index:08d}".encode()) == bytes(262_144)
            for index in range(committed_records, records + 1):
                assert txn.get(f"{index:08d}".encode()) is None
        atomicity_verified = True
    print(
        json.dumps(
            {
                "records": records,
                "committed_records": committed_records,
                "atomicity_verified": atomicity_verified,
                "exception": type(caught).__name__ if caught else None,
                "code": getattr(caught, "code", None),
                "what": getattr(caught, "what", None),
                "reason": getattr(caught, "reason", None),
                "close_exception": type(close_error).__name__ if close_error else None,
                "close_code": getattr(close_error, "code", None),
                "close_reason": getattr(close_error, "reason", None),
                "free_bytes": stat.f_bavail * stat.f_frsize,
            },
            sort_keys=True,
        )
    )
    close_is_expected = close_error is None or isinstance(close_error, clibmdbx.DiskError)
    return 0 if isinstance(caught, clibmdbx.DiskError) and close_is_expected else 2


if __name__ == "__main__":
    raise SystemExit(main())
