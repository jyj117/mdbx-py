#!/usr/bin/env python3
"""Validate a complete immutable release set and optionally write SHA-256 sums."""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import Version

PACKAGE = "clibmdbx"
PYTHONS = {f"cp{minor}" for minor in range(310, 315)}
PLATFORMS = {"linux-x86_64", "linux-aarch64", "macos-x86_64", "macos-arm64", "windows-amd64"}


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def platform_group(platforms: set[str]) -> str:
    groups: set[str] = set()
    for platform in platforms:
        if platform.startswith("manylinux_") and platform.endswith("_x86_64"):
            groups.add("linux-x86_64")
        elif platform.startswith("manylinux_") and platform.endswith("_aarch64"):
            groups.add("linux-aarch64")
        elif platform.startswith("macosx_") and platform.endswith("_x86_64"):
            groups.add("macos-x86_64")
        elif platform.startswith("macosx_") and platform.endswith("_arm64"):
            groups.add("macos-arm64")
        elif platform == "win_amd64":
            groups.add("windows-amd64")
        else:
            raise ValueError(f"unexpected platform tag: {platform}")
    if len(groups) != 1:
        raise ValueError(f"wheel spans unexpected platform groups: {sorted(groups)}")
    return groups.pop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-matrix", action="store_true")
    parser.add_argument("--checksums", type=pathlib.Path)
    parser.add_argument("version")
    parser.add_argument("archives", nargs="+", type=pathlib.Path)
    args = parser.parse_args()

    expected_version = Version(args.version)
    errors: list[str] = []
    seen_names: set[str] = set()
    wheel_targets: dict[tuple[str, str], str] = {}
    sdists: list[str] = []

    for path in args.archives:
        if not path.is_file():
            errors.append(f"not a regular file: {path}")
            continue
        if path.name in seen_names:
            errors.append(f"duplicate archive filename: {path.name}")
            continue
        seen_names.add(path.name)
        try:
            if path.suffix == ".whl":
                name, version, _build, tags = parse_wheel_filename(path.name)
                if canonicalize_name(name) != PACKAGE or version != expected_version:
                    raise ValueError(f"metadata is {name} {version}, expected {PACKAGE} {expected_version}")
                interpreters = {tag.interpreter for tag in tags}
                if len(interpreters) != 1:
                    raise ValueError(f"unexpected interpreter tags: {sorted(interpreters)}")
                interpreter = interpreters.pop()
                abis = {tag.abi for tag in tags}
                if abis != {interpreter}:
                    raise ValueError(f"wheel must use the exact per-CPython ABI {interpreter}, found {sorted(abis)}")
                target = (interpreter, platform_group({tag.platform for tag in tags}))
                if target in wheel_targets:
                    raise ValueError(f"duplicates target {target} from {wheel_targets[target]}")
                wheel_targets[target] = path.name
            elif path.name.endswith(".tar.gz"):
                name, version = parse_sdist_filename(path.name)
                if canonicalize_name(name) != PACKAGE or version != expected_version:
                    raise ValueError(f"metadata is {name} {version}, expected {PACKAGE} {expected_version}")
                sdists.append(path.name)
            else:
                raise ValueError("not a wheel or .tar.gz source distribution")
        except (ValueError, TypeError) as exc:
            errors.append(f"{path.name}: {exc}")

    if len(sdists) != 1:
        errors.append(f"expected exactly one sdist, found {sdists}")
    if args.full_matrix:
        expected_targets = {(python, platform) for python in PYTHONS for platform in PLATFORMS}
        actual_targets = set(wheel_targets)
        if missing := sorted(expected_targets - actual_targets):
            errors.append(f"missing wheel targets: {missing}")
        if unexpected := sorted(actual_targets - expected_targets):
            errors.append(f"unexpected wheel targets: {unexpected}")

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    if args.checksums:
        lines = [f"{sha256(path)}  {path.name}\n" for path in sorted(args.archives, key=lambda item: item.name)]
        args.checksums.write_text("".join(lines), encoding="ascii", newline="\n")
    print(f"validated {len(wheel_targets)} wheels and {len(sdists)} sdist for {PACKAGE} {expected_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
