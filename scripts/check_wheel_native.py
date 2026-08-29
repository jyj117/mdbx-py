#!/usr/bin/env python3
"""Reject wheel-native dependencies outside each platform's system runtime."""

from __future__ import annotations

import argparse
import glob
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

WINDOWS_SYSTEM_DLLS = {
    "advapi32.dll",
    "bcrypt.dll",
    "crypt32.dll",
    "kernel32.dll",
    "ntdll.dll",
    "ole32.dll",
    "oleaut32.dll",
    "rpcrt4.dll",
    "sechost.dll",
    "shell32.dll",
    "user32.dll",
    "ucrtbase.dll",
    "ws2_32.dll",
}
LINUX_SYSTEM_LIBS = {
    "libc.so.6",
    "libdl.so.2",
    "libgcc_s.so.1",
    "libm.so.6",
    "libpthread.so.0",
    "librt.so.1",
}


def expand_archives(patterns: list[str]) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        paths.extend(pathlib.Path(match) for match in (matches or [pattern]))
    return paths


def pe_imports(data: bytes) -> set[str]:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError("not a PE image")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError("invalid PE signature")
    coff = pe_offset + 4
    section_count = struct.unpack_from("<H", data, coff + 2)[0]
    optional_size = struct.unpack_from("<H", data, coff + 16)[0]
    optional = coff + 20
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic == 0x20B:
        directories = optional + 112
    elif magic == 0x10B:
        directories = optional + 96
    else:
        raise ValueError(f"unsupported PE optional-header magic 0x{magic:x}")
    import_rva, import_size = struct.unpack_from("<II", data, directories + 8)
    if import_rva == 0 or import_size == 0:
        return set()
    sections: list[tuple[int, int, int]] = []
    section_table = optional + optional_size
    for index in range(section_count):
        offset = section_table + index * 40
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", data, offset + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset))

    def rva_offset(rva: int) -> int:
        for virtual_address, size, raw_offset in sections:
            if virtual_address <= rva < virtual_address + size:
                return raw_offset + rva - virtual_address
        raise ValueError(f"PE RVA 0x{rva:x} is outside all sections")

    imports: set[str] = set()
    descriptor = rva_offset(import_rva)
    while descriptor + 20 <= len(data):
        fields = struct.unpack_from("<IIIII", data, descriptor)
        if fields == (0, 0, 0, 0, 0):
            break
        name_offset = rva_offset(fields[3])
        end = data.find(b"\0", name_offset)
        if end < 0:
            raise ValueError("unterminated PE import name")
        imports.add(data[name_offset:end].decode("ascii"))
        descriptor += 20
    return imports


def inspect_linux(path: pathlib.Path) -> set[str]:
    result = subprocess.run(["readelf", "-d", str(path)], check=True, capture_output=True, text=True)
    return set(re.findall(r"\(NEEDED\).*?\[([^]]+)\]", result.stdout))


def inspect_macos(path: pathlib.Path) -> set[str]:
    result = subprocess.run(["otool", "-L", str(path)], check=True, capture_output=True, text=True)
    return {line.strip().split(" (", 1)[0] for line in result.stdout.splitlines()[1:] if line.strip()}


def validate_dependencies(member: str, extracted: pathlib.Path, data: bytes) -> set[str]:
    if member.endswith(".pyd"):
        dependencies = pe_imports(data)
        unexpected = {
            item
            for item in dependencies
            if item.lower() not in WINDOWS_SYSTEM_DLLS
            and not item.lower().startswith(("api-ms-win-", "ext-ms-win-", "vcruntime", "msvcp"))
            and re.fullmatch(r"python3\d{1,2}(?:_d)?\.dll", item.lower()) is None
        }
    elif "macosx_" in extracted.parent.name:
        dependencies = inspect_macos(extracted)
        unexpected = {item for item in dependencies if not item.startswith(("/usr/lib/", "/System/Library/"))}
    else:
        dependencies = inspect_linux(extracted)
        unexpected = {
            item
            for item in dependencies
            if item not in LINUX_SYSTEM_LIBS and not item.startswith(("ld-linux", "ld64.so"))
        }
    if unexpected:
        raise ValueError(f"{member} has unexpected native dependencies: {sorted(unexpected)}")
    if any("mdbx" in item.lower() for item in dependencies):
        raise ValueError(f"{member} dynamically depends on libmdbx: {sorted(dependencies)}")
    return dependencies


def inspect_wheel(wheel: pathlib.Path) -> None:
    if not wheel.is_file():
        raise ValueError(f"not a wheel file: {wheel}")
    with zipfile.ZipFile(wheel) as archive, tempfile.TemporaryDirectory(prefix="clibmdbx-wheel-native-") as directory:
        native = [name for name in archive.namelist() if name.lower().endswith((".so", ".pyd", ".dylib", ".dll"))]
        extension = [name for name in native if pathlib.PurePosixPath(name).name.startswith("_core.")]
        if len(extension) != 1:
            raise ValueError(f"expected one _core native extension, found {native}")
        if native != extension:
            raise ValueError(f"wheel bundles unexpected native libraries: {native}")
        member = extension[0]
        data = archive.read(member)
        extracted = pathlib.Path(directory) / wheel.name / pathlib.PurePosixPath(member).name
        extracted.parent.mkdir(parents=True)
        extracted.write_bytes(data)
        dependencies = validate_dependencies(member, extracted, data)
    print(f"{wheel}: native dependencies {sorted(dependencies)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheels", nargs="+")
    args = parser.parse_args()
    errors: list[str] = []
    for wheel in expand_archives(args.wheels):
        try:
            inspect_wheel(wheel)
        except (OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
            errors.append(f"{wheel}: {exc}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
