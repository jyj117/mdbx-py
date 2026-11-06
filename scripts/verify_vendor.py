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
    "NOTICE": "21f302c17332bb2481b210a0f80e0540e77701d8a9bb40a76a6cb1265f73ad13",
    "VERSION.json": "3492276c3fc5b2731c145047f097f8d875a09e089a60a4cf84353ad63e07771a",
    "mdbx-internals.h": "f293f0e99eb77eebe7f7f4cfa76686e32b4a5fe09445e26b9531714e4d2ec854",
    "mdbx-wingetopt.h": "024d540c80198df81e74d5fa72cd4b0287aba0d7dc2985a82e35b2ccc63b2bbe",
    "mdbx.c": "52d061dc77b1485da1ab7d9df336c750d021e3b1c7267db437bb6100cb3b001c",
    "mdbx.h": "1feb06b7f6f65ab3ad16df51cffbea977331d7426e6d72bdc07f4feac5a9cbb6",
    "mdbx_chk.c": "87ba8659d478701187ac605782bca1637160864c17ade9a413f10db3dce53436",
}
VERSION = {
    "git_describe": "v0.14.3-0-g251562b2",
    "git_timestamp": "2026-08-09T13:18:46+03:00",
    "git_tree": "e4baa5caf1001120895ba9282f645236e2fb160b",
    "git_commit": "251562b2dc55266d8e6d0e6627ec88ecb410702f",
    "semver": "0.14.3",
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
    print("verified libmdbx 0.14.3 vendored sources and notices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
