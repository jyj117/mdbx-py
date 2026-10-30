"""Measure other-process reader capacity or commit synthetic updates."""

import json
import sys

import clibmdbx

with clibmdbx.Environment(sys.argv[1], max_readers=8, max_spare_txns=0) as env:
    if len(sys.argv) > 2:
        for index in range(256):
            with env.write() as txn:
                txn.put(b"key", index.to_bytes(4, "big") + bytes(65532))
        print("written", flush=True)
    else:
        readers = []
        try:
            while True:
                readers.append(env.read())
        except clibmdbx.ReadersFullError:
            print(json.dumps({"capacity": len(readers)}), flush=True)
        finally:
            for txn in readers:
                txn.abort()
