"""Open a reader slot and exit without normal cleanup."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import clibmdbx

path = Path(sys.argv[1])
env = clibmdbx.Environment(path)
txn = env.read()
assert txn.active
print("reader-ready", flush=True)
os._exit(0)
