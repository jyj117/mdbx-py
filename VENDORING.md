# Vendoring libmdbx

The build is offline and deterministic with respect to its C sources. It never
downloads libmdbx. The current pin is the official stable release 0.14.3:

| Item | Value |
| --- | --- |
| Release tag | `v0.14.3` |
| Annotated tag object | `cf3e13938bf974260872e629713f6ecf30e62030` |
| Tag release commit | `f7a3a9323cacacfa9dc6137ae7a7252a67744ff0` |
| Amalgamation source commit | `251562b2dc55266d8e6d0e6627ec88ecb410702f` |
| Source tree | `e4baa5caf1001120895ba9282f645236e2fb160b` |
| Official archive SHA-256 | `dbc4a791c44d3e51a8159eedfee0dedada7b21d46c22588f0fa99294983f33cd` |
| `mdbx.c` SHA-256 | `52d061dc77b1485da1ab7d9df336c750d021e3b1c7267db437bb6100cb3b001c` |
| `mdbx.h` SHA-256 | `1feb06b7f6f65ab3ad16df51cffbea977331d7426e6d72bdc07f4feac5a9cbb6` |

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
