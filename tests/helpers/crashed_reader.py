"""Open a reader slot and exit without normal cleanup."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import clibmdbx

path = Path(sys.argv[1])
env = clibmdbx.Environment(path)
txn = env.read()
assert txn.active
if len(sys.argv) > 2:
    # A surviving process can retain the same open-file-description lock after
    # the reader process exits. It never calls MDBX through the inherited env.
    if os.fork() == 0:
        for descriptor in (0, 1, 2):
            os.close(descriptor)
        time.sleep(float(sys.argv[2]))
        os._exit(0)
print("reader-ready", flush=True)
os._exit(0)
