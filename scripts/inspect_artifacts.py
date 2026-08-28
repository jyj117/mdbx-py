#!/usr/bin/env python3
"""Reject release archives with missing notices or private/data artifacts."""

from __future__ import annotations

import pathlib
import sys
import tarfile
import zipfile

REQUIRED_SUFFIXES = {
    "LICENSE",
    "NOTICE",
    "vendor/libmdbx/LICENSE",
    "vendor/libmdbx/NOTICE",
    "vendor/libmdbx/COPYRIGHT",
}
FORBIDDEN_SUFFIXES = {".mdb", ".mdbx", ".lck", ".pem", ".key", ".p12", ".env"}
FORBIDDEN_PARTS = {"native_mdbx", "native_mdbx_rust", "company", "secrets"}
FORBIDDEN_NAMES = {".pypirc"}


def forbidden_member(name: str) -> bool:
    path = pathlib.PurePosixPath(name)
    return (
        path.suffix in FORBIDDEN_SUFFIXES
        or any(part in FORBIDDEN_PARTS for part in path.parts)
        or any(part in FORBIDDEN_NAMES or part.startswith(".env.") for part in path.parts)
    )


def members(path: pathlib.Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, "r:*") as archive:
        return archive.getnames()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: inspect_artifacts.py ARCHIVE...", file=sys.stderr)
        return 2
    failed = False
    for argument in sys.argv[1:]:
        path = pathlib.Path(argument)
        names = members(path)
        lowered = [name.lower() for name in names]
        missing = [suffix for suffix in REQUIRED_SUFFIXES if not any(name.endswith(suffix.lower()) for name in lowered)]
        forbidden = [name for name in lowered if forbidden_member(name)]
        if missing or forbidden:
            failed = True
            print(f"{path}: missing={missing}, forbidden={forbidden}", file=sys.stderr)
        else:
            print(f"{path}: {len(names)} entries, notices present, no forbidden data")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
