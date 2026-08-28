"""High-performance, self-contained CPython bindings for libmdbx.

This independent community package embeds a pinned upstream libmdbx release.
It does not use ctypes, CFFI, Cython, or a separately loaded libmdbx library.
"""

# Copyright 2026 clibmdbx contributors
# SPDX-License-Identifier: Apache-2.0

from . import _core as _native

_native._check_interpreter()

from ._core import *  # noqa: F403
from ._core import __version__

del _native

# Familiar python-lmdb spelling; this is an alias, not a compatibility shim.
open = Environment  # type: ignore[name-defined]  # noqa: F405,A001


def version_info() -> dict[str, object]:
    """Return binding, embedded-engine, compiler, and provenance details."""

    return diagnostics()  # type: ignore[name-defined]  # noqa: F405
