# Vendoring libmdbx

The build is offline and deterministic with respect to its C sources. It never
downloads libmdbx. The current pin is the official stable release 0.14.4:

| Item | Value |
| --- | --- |
| Release tag | `v0.14.4` |
| Annotated tag object | `6dae84f65ce208f8299c024186a13dfd1f258f78` |
| Tag release commit | `a032e40c1c06eaf695a163cfc32d4a01ffb8b26c` |
| Amalgamation source commit | `716ce9d5ae5458fa1de44097dc6e8f80ed1e4809` |
| Source tree | `4b437ab251d578093abf92b5f65c568bf01004ac` |
| Official archive SHA-256 | `0dd6bf6048d5d208bc20d325cf93c8f88beda95802e9e3aa7958346611c36922` |
| `mdbx.c` SHA-256 | `58b770f90b75c533fd66d6d040764f75589565a2bc46034cc2ec2d8d9b257176` |
| `mdbx.h` SHA-256 | `0290c40b3a40346eb2e892d17c50b9a488464c116ba317c2ad977e78ae87451b` |

`scripts/verify_vendor.py` verifies provenance metadata and the SHA-256 of every
vendored source, tool header, manifest and notice, plus the version/commit
encoded in the source. To update:

1. Confirm on the official releases page that the candidate is a stable release,
   not master, an RC, or a development snapshot.
2. Download the official amalgamation archive independently and record its
   SHA-256 before extraction.
3. Replace the complete source set and notices; never hand-copy declarations.
4. Update the pin in `_core.c`, `setup.py`, docs, SBOM and verification script.
5. Re-run strict builds, the complete tests, ASan/UBSan, Valgrind where available,
   `mdbx_chk`, clean-wheel tests, all CI platforms, and the benchmark suite.
6. Review upstream release notes for data-format, durability, option and API
   changes. Update the C API matrix explicitly.

The import-time check compares the expected major/minor/patch and exact
amalgamation commit to `mdbx_version`. A source/link mismatch fails import.
