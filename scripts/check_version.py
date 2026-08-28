#!/usr/bin/env python3
"""Check that the immutable tag and all version declarations agree."""

from __future__ import annotations

import pathlib
import re
import sys

from packaging.version import InvalidVersion, Version

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_version.py VERSION", file=sys.stderr)
        return 2
    expected = sys.argv[1]
    try:
        parsed = Version(expected)
    except InvalidVersion:
        print(f"not a valid PEP 440 version: {expected!r}", file=sys.stderr)
        return 1
    if parsed.local is not None:
        print("PyPI releases must not use a PEP 440 local version identifier", file=sys.stderr)
        return 1
    files = {
        "pyproject.toml": r'^version = "([^"]+)"$',
        "src/clibmdbx/_version.py": r'^__version__ = "([^"]+)"$',
        "setup.py": r'MDBX_BUILD_METADATA", \'"clibmdbx-([^"\']+)',
        "src/clibmdbx/_core.c": r'#define CLIBMDBX_VERSION "([^"]+)"',
    }
    errors: list[str] = []
    for relative, pattern in files.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        match = re.search(pattern, text, re.MULTILINE)
        actual = match.group(1) if match else None
        if actual != expected:
            errors.append(f"{relative}: expected {expected!r}, found {actual!r}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"all version declarations match {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
