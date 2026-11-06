"""Exercise clibmdbx against an intentionally capacity-limited filesystem.

Run this only in an isolated container or disposable filesystem.  The script
is used by the release audit to verify the exception raised after real ENOSPC.
"""

from __future__ import annotations

import errno
import json
import os
import sys

import clibmdbx


def check_backup_enospc(root: str) -> int:
    """Fill only the caller's disposable, capacity-limited filesystem."""
    source = os.path.join(root, "backup-source")
    filler = os.path.join(root, "backup-filler")
    destination = os.path.join(root, "backup-copy")
    with clibmdbx.Environment(source, geometry=(0, 1 << 20, 4 << 20, 1 << 20, -1, -1)) as env:
        with env.write() as txn:
            txn.put(b"committed", bytes(65536))
        try:
            with open(filler, "wb", buffering=0) as stream:
                try:
                    while True:
                        stream.write(bytes(65536))
                except OSError as exc:
                    if exc.errno != errno.ENOSPC:
                        raise
            try:
                env.copy(destination)
            except clibmdbx.DiskError as exc:
                assert exc.code == errno.ENOSPC, exc
                assert exc.what == "mdbx_env_copy" and exc.reason
                code = exc.code
            else:
                raise AssertionError("backup unexpectedly succeeded on the full filesystem")
            assert env.get(b"committed") == bytes(65536)
        finally:
            os.unlink(filler)
        with env.write() as txn:
            txn.put(b"after", b"still writable")
        assert env.get(b"after") == b"still writable"
    with clibmdbx.Environment(source, readonly=True) as reopened:
        assert reopened.get(b"committed") == bytes(65536)
    return code


def main() -> int:
    root = sys.argv[1]
    capacity = os.statvfs(os.path.dirname(root))
    if capacity.f_blocks * capacity.f_frsize > 64 << 20:
        raise ValueError("requires a disposable filesystem limited to at most 64 MiB")
    os.mkdir(root)
    backup_code = check_backup_enospc(root)
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
                "backup_enospc_verified": True,
                "backup_code": backup_code,
            },
            sort_keys=True,
        )
    )
    close_is_expected = close_error is None or isinstance(close_error, clibmdbx.DiskError)
    return 0 if isinstance(caught, clibmdbx.DiskError) and close_is_expected else 2


if __name__ == "__main__":
    raise SystemExit(main())
