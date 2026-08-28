#!/usr/bin/env python3
"""Wait for an index release and match every published SHA-256 to local files."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"User-Agent": "clibmdbx-release-verifier/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", required=True, help="index base, for example https://test.pypi.org")
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--delay", type=float, default=10.0)
    parser.add_argument("project")
    parser.add_argument("version")
    parser.add_argument("archives", nargs="+", type=pathlib.Path)
    args = parser.parse_args()

    expected = {path.name: sha256(path) for path in args.archives}
    project = urllib.parse.quote(args.project, safe="")
    version = urllib.parse.quote(args.version, safe="")
    url = f"{args.index.rstrip('/')}/pypi/{project}/{version}/json"
    last_error = "index did not return the release"

    for attempt in range(1, args.attempts + 1):
        try:
            payload = fetch(url)
            urls = payload.get("urls")
            if not isinstance(urls, list):
                raise ValueError("JSON response has no urls list")
            actual: dict[str, str] = {}
            for item in urls:
                if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                    raise ValueError("malformed file entry in JSON response")
                digests = item.get("digests")
                if not isinstance(digests, dict) or not isinstance(digests.get("sha256"), str):
                    raise ValueError(f"missing SHA-256 for {item['filename']}")
                actual[item["filename"]] = digests["sha256"]
            if actual == expected:
                print(f"{args.index}: all {len(expected)} filenames and SHA-256 digests match")
                return 0
            missing = sorted(expected.keys() - actual.keys())
            extra = sorted(actual.keys() - expected.keys())
            changed = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])
            last_error = f"release set mismatch: missing={missing}, extra={extra}, changed={changed}"
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        if attempt < args.attempts:
            print(f"attempt {attempt}/{args.attempts}: {last_error}; retrying", file=sys.stderr)
            time.sleep(args.delay)

    print(f"verification failed for {url}: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
