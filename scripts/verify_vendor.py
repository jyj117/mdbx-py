#!/usr/bin/env python3
"""Offline provenance and integrity checks for the pinned libmdbx source."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "libmdbx"
EXPECTED = {
    "COPYRIGHT": "e53a73bbd1f4862f53fb6f37c4d987b1d12a1886d22022a8bafc1a7a0471dbb6",
    "LICENSE": "0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594",
    "mdbx-internals.h": "592c5537e226e7b8b504801d1de00a88d62f227895467c99db44ce0ada975108",
    "mdbx-wingetopt.h": "024d540c80198df81e74d5fa72cd4b0287aba0d7dc2985a82e35b2ccc63b2bbe",
    "mdbx.c": "58b770f90b75c533fd66d6d040764f75589565a2bc46034cc2ec2d8d9b257176",
    "mdbx.h": "0290c40b3a40346eb2e892d17c50b9a488464c116ba317c2ad977e78ae87451b",
    "mdbx_chk.c": "b6427670be9850d09a74bf87bed7cfdec23d804b26cfeda167a169e436d913f9",
    "NOTICE": "21f302c17332bb2481b210a0f80e0540e77701d8a9bb40a76a6cb1265f73ad13",
    "VERSION.json": "89554c225dc451791e748c4d14a7ccbc5fd7ca850204a175c21e4b02f673d624",
}
VERSION = {
    "git_describe": "v0.14.4-0-g716ce9d5",
    "git_timestamp": "2026-09-18T22:53:42+03:00",
    "git_tree": "4b437ab251d578093abf92b5f65c568bf01004ac",
    "git_commit": "716ce9d5ae5458fa1de44097dc6e8f80ed1e4809",
    "semver": "0.14.4",
}


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    errors: list[str] = []
    for name, expected in EXPECTED.items():
        actual = sha256(VENDOR / name)
        if actual != expected:
            errors.append(f"{name}: expected {expected}, got {actual}")
    actual_version = json.loads((VENDOR / "VERSION.json").read_text(encoding="utf-8"))
    if actual_version != VERSION:
        errors.append(f"VERSION.json mismatch: {actual_version!r}")
    c_source = (VENDOR / "mdbx.c").read_text(encoding="utf-8", errors="strict")
    if VERSION["git_commit"] not in c_source or VERSION["semver"] not in c_source:
        errors.append("mdbx.c does not encode the pinned version/commit")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("verified libmdbx 0.14.4 vendored sources and notices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
