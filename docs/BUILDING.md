# Building and platform verification

## Local Linux build

```bash
python -m venv /tmp/clibmdbx-venv
/tmp/clibmdbx-venv/bin/python -m pip install -U pip
/tmp/clibmdbx-venv/bin/python -m pip install \
  "setuptools==84.0.0" "wheel==0.48.0" "pytest==9.1.1" \
  "build==1.5.0" "twine==7.0.0" "auditwheel==6.8.1"
CLIBMDBX_STRICT=1 /tmp/clibmdbx-venv/bin/python -m pip install -e . --no-build-isolation
TMPDIR=/tmp /tmp/clibmdbx-venv/bin/python -m pytest -q
```

Keep MDBX databases on a native Linux filesystem. WSL `/mnt/c` is useful for
source but not representative for mmap/database correctness or performance.
Release build and validation tools are exact pins. Upgrade them deliberately
and rerun the complete wheel matrix; do not let a tag build resolve an
unreviewed toolchain update.

## Sanitizers

```bash
rm -rf build src/clibmdbx/_core*.so
CLIBMDBX_SANITIZE=address,undefined CLIBMDBX_STRICT=1 \
  python -m pip install -e . --no-build-isolation
LD_PRELOAD="$(gcc -print-file-name=libasan.so)" \
ASAN_OPTIONS=detect_leaks=0:abort_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
PYTHONMALLOC=malloc TMPDIR=/tmp python -m pytest -vv
```

Leak detection is disabled only because a stock, unsanitized CPython process has
interpreter-global allocations. CI additionally runs Valgrind with
`PYTHONMALLOC=malloc` and definite leaks/errors treated as failures.

The build disables UBSan's alignment check for libmdbx because libmdbx
deliberately selects unaligned x86 load/store implementations when
`MDBX_UNALIGNED_OK` permits them. Bounds, object-size, pointer, integer and all
other enabled ASan/UBSan checks remain active.

## Official checker

```bash
bash scripts/build_mdbx_chk.sh /tmp/clibmdbx-tools/mdbx_chk
/tmp/clibmdbx-tools/mdbx_chk -q /path/to/environment
```

The helper compiles the checker from the exact vendored source.

## Wheel policy

Wheels intentionally use per-minor CPython tags (`cp310` ... `cp314`), not
`abi3`. `cibuildwheel` performs a clean install and test for each platform. A
platform is supported only after its CI job has passed; a classifier or workflow
entry alone is not validation.

Free-threaded (`cp*t`) CPython builds are intentionally rejected at compile
time in the current release: the wrapper's native-object lifecycle relies on
the GIL and has not yet been redesigned and validated for free-threaded
execution.

Linux wheels target manylinux x86_64 and aarch64. macOS targets x86_64 and arm64;
Windows targets AMD64 with Windows 10 or later. The C extension contains static
libmdbx objects, but retains normal platform C runtime/system-library imports.
