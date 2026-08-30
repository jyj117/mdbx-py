# Copyright 2026 clibmdbx contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import gzip
import io
import os
import sys
import tarfile
import tempfile

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext
from setuptools.command.sdist import sdist


class BuildExt(build_ext):
    """Use conservative release optimizations without changing MDBX durability."""

    def build_extensions(self) -> None:
        compiler = self.compiler.compiler_type
        sanitize = os.environ.get("CLIBMDBX_SANITIZE", "").strip()
        strict = os.environ.get("CLIBMDBX_STRICT", "1") != "0"
        for ext in self.extensions:
            if compiler == "msvc":
                ext.extra_compile_args += ["/O2", "/GL", "/utf-8"]
                ext.extra_link_args += ["/LTCG", "/OPT:REF", "/OPT:ICF"]
                if strict:
                    # CPython 3.10's own pytime.h emits C4115 with current
                    # MSVC, and CPython callback signatures intentionally
                    # carry unused parameters.  Keep every other W4 warning
                    # fatal while suppressing only those two known classes.
                    ext.extra_compile_args += ["/W4", "/WX", "/wd4100", "/wd4115"]
            else:
                ext.extra_compile_args += [
                    "-O3",
                    "-fvisibility=hidden",
                ]
                if sys.platform.startswith("linux"):
                    # This is a GCC/ELF optimization.  Apple Clang accepts the
                    # spelling but reports it as unused, which is correctly
                    # fatal in strict builds.
                    ext.extra_compile_args += ["-fno-semantic-interposition"]
                if not sanitize:
                    lto = "-flto=auto" if sys.platform.startswith("linux") else "-flto"
                    ext.extra_compile_args += [lto]
                    ext.extra_link_args += [lto]
                if strict:
                    ext.extra_compile_args += [
                        "-Wall",
                        "-Wextra",
                        "-Wstrict-prototypes",
                        "-Werror",
                        "-Wno-cast-function-type",
                        "-Wno-missing-field-initializers",
                    ]
                    if sys.platform == "darwin":
                        # Apple's CPython build injects -Wunreachable-code.
                        # The official libmdbx amalgamation intentionally uses
                        # compile-time-disabled diagnostic branches, so keep
                        # all other warnings fatal and suppress this one class.
                        ext.extra_compile_args += ["-Wno-unreachable-code"]
                if sanitize:
                    kinds = sanitize.replace(" ", "")
                    ext.extra_compile_args += [f"-fsanitize={kinds}", "-fno-omit-frame-pointer", "-O1"]
                    ext.extra_link_args += [f"-fsanitize={kinds}"]
                    if "undefined" in kinds:
                        # libmdbx deliberately uses unaligned loads/stores on
                        # architectures reported as MDBX_UNALIGNED_OK.
                        ext.extra_compile_args += ["-fno-sanitize=alignment"]
                if sys.platform.startswith("linux"):
                    # Resolve all relocations at load time and make the GOT
                    # read-only.  This does not affect the database hot path.
                    ext.extra_link_args += ["-Wl,-z,relro", "-Wl,-z,now"]
        super().build_extensions()


class ReproducibleSdist(sdist):
    """Normalize archive metadata that setuptools does not make reproducible."""

    def run(self) -> None:
        super().run()
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "1787875200"))
        for archive_name in self.archive_files:
            if archive_name.endswith(".tar.gz"):
                self._normalize_tar_gz(archive_name, epoch)

    @staticmethod
    def _normalize_tar_gz(archive_name: str, epoch: int) -> None:
        entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
        with tarfile.open(archive_name, "r:gz") as source:
            for member in source.getmembers():
                extracted = source.extractfile(member) if member.isfile() else None
                entries.append((member, extracted.read() if extracted is not None else None))

        directory = os.path.dirname(os.path.abspath(archive_name))
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as raw:
            temporary = raw.name
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as target:
                    for member, payload in sorted(entries, key=lambda item: item[0].name):
                        member.uid = member.gid = 0
                        member.uname = member.gname = ""
                        member.mtime = epoch
                        member.pax_headers = {}
                        target.addfile(member, io.BytesIO(payload) if payload is not None else None)
        os.replace(temporary, archive_name)


define_macros = [
    # libmdbx normally forces its version/build data to default visibility even
    # for static builds.  In a CPython extension these are implementation
    # details; overriding the header's supported macro keeps the dynamic symbol
    # table limited to PyInit__core and avoids collisions with other bindings.
    ("__dll_export", ""),
    ("MDBX_BUILD_METADATA", '"clibmdbx-1.0.3"'),
    ("MDBX_BUILD_FLAGS", '"setuptools O3 LTO hidden-symbols"'),
    ("MDBX_ENV_CHECKPID", "1"),
    ("MDBX_TXN_CHECKOWNER", "1"),
    ("MDBX_ENABLE_PGET_STAT", "1"),
]

if sys.platform == "win32":
    define_macros.extend(
        [
            ("_WIN32_WINNT", "0x0A00"),
            ("WIN32_LEAN_AND_MEAN", "1"),
            ("NOMINMAX", "1"),
        ]
    )

system_libraries = ["advapi32", "ntdll", "user32"] if sys.platform == "win32" else []

extension = Extension(
    "clibmdbx._core",
    sources=[
        "src/clibmdbx/_core.c",
        "vendor/libmdbx/mdbx.c",
    ],
    include_dirs=["vendor/libmdbx"],
    define_macros=define_macros,
    libraries=system_libraries,
    extra_compile_args=[],
    extra_link_args=[],
)

setup(ext_modules=[extension], cmdclass={"build_ext": BuildExt, "sdist": ReproducibleSdist})
