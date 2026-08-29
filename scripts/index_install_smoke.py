#!/usr/bin/env python3
"""Install an exact wheel from an index into a clean venv and smoke-test it."""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile
import time
import venv

SMOKE = r'''
import json
import os
import tempfile
import threading
import sys

import clibmdbx

expected = sys.argv[1]
assert clibmdbx.__version__ == expected, (clibmdbx.__version__, expected)
with tempfile.TemporaryDirectory() as directory:
    env = clibmdbx.Environment(directory, max_dbs=4)
    pairs = [(f"key-{index:04d}".encode(), f"value-{index:04d}".encode()) for index in range(256)]
    with env.write() as txn:
        assert txn.put_many(pairs, detached=True) == len(pairs)
    with env.read() as txn:
        assert txn.get_many([key for key, _value in pairs], detached=True) == [value for _key, value in pairs]

    failures = []
    def reader():
        try:
            for _ in range(20):
                with env.read() as txn:
                    assert txn.get(b"key-0017") == b"value-0017"
        except BaseException as exc:
            failures.append(repr(exc))
    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures, failures

    if hasattr(os, "fork"):
        pid = os.fork()
        if pid == 0:
            try:
                env.info()
            except clibmdbx.ForkError:
                os._exit(0)
            except BaseException:
                os._exit(2)
            os._exit(3)
        _pid, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    env.close()

print(json.dumps(clibmdbx.diagnostics(), default=str, sort_keys=True))
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--index", help="PEP 503 simple index URL")
    source.add_argument("--find-links", type=pathlib.Path, help="local directory used for an offline wheel smoke test")
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--delay", type=float, default=10.0)
    parser.add_argument("project")
    parser.add_argument("version")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="clibmdbx-index-smoke-") as directory:
        venv_dir = pathlib.Path(directory) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        command = [
            str(python), "-m", "pip", "install",
            "--disable-pip-version-check", "--no-input", "--no-cache-dir",
            "--only-binary=:all:", "--no-deps",
        ]
        if args.index:
            command.extend(["--index-url", args.index])
        else:
            command.extend(["--no-index", "--find-links", str(args.find_links.resolve())])
        command.append(f"{args.project}=={args.version}")
        result: subprocess.CompletedProcess[str] | None = None
        for attempt in range(1, args.attempts + 1):
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            if result.returncode == 0:
                break
            if attempt < args.attempts:
                print(f"install attempt {attempt}/{args.attempts} failed; retrying", file=sys.stderr)
                time.sleep(args.delay)
        assert result is not None
        if result.returncode:
            print(result.stdout, file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            return result.returncode
        smoke = subprocess.run([str(python), "-c", SMOKE, args.version], check=False)
        if smoke.returncode:
            return smoke.returncode
        uninstall = subprocess.run(
            [str(python), "-m", "pip", "uninstall", "-y", args.project],
            check=False,
        )
        if uninstall.returncode:
            return uninstall.returncode
    print(f"clean-index install, import, CRUD, concurrency, fork safety and uninstall passed for {args.project} {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
