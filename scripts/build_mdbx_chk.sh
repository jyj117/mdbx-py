#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
output=${1:-"$root/build/tools/mdbx_chk"}
mkdir -p "$(dirname "$output")"
${CC:-cc} -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers \
  -DMDBX_BUILD_FLAGS='"clibmdbx-standalone-checker O2"' \
  -I"$root/vendor/libmdbx" \
  "$root/vendor/libmdbx/mdbx_chk.c" "$root/vendor/libmdbx/mdbx.c" \
  -o "$output" -pthread -lrt
"$output" -V
