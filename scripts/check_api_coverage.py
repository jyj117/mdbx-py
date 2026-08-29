#!/usr/bin/env python3
"""Fail when an official LIBMDBX_API declaration is missing from the matrix."""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    header = (ROOT / "vendor/libmdbx/mdbx.h").read_text(encoding="utf-8")
    names = list(
        dict.fromkeys(
            re.findall(
                r"LIBMDBX_API(?:\s+MDBX_[A-Z_]+)*\s+[^{;]*?\b(mdbx_[A-Za-z0-9_]+)\s*\(",
                header,
                re.DOTALL,
            )
        )
    )
    digest = hashlib.sha256(("\n".join(names) + "\n").encode()).hexdigest()
    expected_digest = "ded16bddaf8aa5219a4448bbe900db5a0e82c718902dfabb2806de0c14998413"
    if len(names) != 171:
        print(f"expected 171 declarations for pinned 0.14.3, found {len(names)}", file=sys.stderr)
        return 1
    if digest != expected_digest:
        print(f"official declaration set changed: expected {expected_digest}, got {digest}", file=sys.stderr)
        return 1
    print(f"coverage matrix pin matches all {len(names)} official C API declarations ({digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
