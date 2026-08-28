# Third-party notices

## libmdbx 0.14.3

- Project: libmdbx
- Upstream: https://github.com/Mithril-mine/libmdbx
- Release: `v0.14.3` (2026-08-09)
- Amalgamation source commit: `251562b2dc55266d8e6d0e6627ec88ecb410702f`
- License: Apache License 2.0
- Copyright: 2015-2026 Leonid Yuriev and libmdbx authors
- Included files: `vendor/libmdbx/mdbx.c`, `mdbx.h`, `mdbx-internals.h`,
  `mdbx_chk.c`, `mdbx-wingetopt.h`, `LICENSE`, `NOTICE`, `COPYRIGHT`, and
  `VERSION.json`.

The authoritative upstream notices are shipped unchanged in
`vendor/libmdbx/LICENSE`, `vendor/libmdbx/NOTICE`, and
`vendor/libmdbx/COPYRIGHT`. The engine is compiled into the extension; it is not
loaded as a shared library at runtime.

## typing_extensions

- Project: typing_extensions
- Upstream: https://github.com/python/typing_extensions
- Requirement: `>=4.15` on Python `<3.15`
- License: Python Software Foundation License 2.0
- Distribution: installed from PyPI; not bundled into clibmdbx wheels or sdists

This dependency provides the standardized `disjoint_base` type-stub marker for
supported CPython versions before it enters the standard-library `typing`
module. It is used only by static-analysis tooling and is not imported by the
clibmdbx runtime hot path.
