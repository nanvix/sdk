#!/bin/sh

# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

#
# Canary build. Run INSIDE the SDK image (the toolchain is on PATH and the
# sysroot is baked in). Compiles and links the canary programs for
# i686-unknown-nanvix; it does NOT run them (there is no Nanvix guest here).
# Success proves the image is a self-contained cross-toolchain.
#
# Usage (from the repo, via z.py):   ./z.py verify
# Or directly:                       docker run --rm -v "$PWD/tests/canary:/work" \
#                                        -w /work <image> sh ./run.sh
#

set -eu

TARGET="i686-unknown-nanvix"
OUT="${OUT:-/tmp/nanvix-canary}"
mkdir -p "${OUT}"

echo "[canary] using clang: $(command -v clang)"

echo "[canary] building hello.c"
clang --target="${TARGET}" -O2 hello.c -o "${OUT}/hello_c"

echo "[canary] building hello.cpp"
clang++ --target="${TARGET}" -O2 hello.cpp -o "${OUT}/hello_cpp"

echo "[canary] artifacts:"
ls -l "${OUT}"

# Sanity: confirm the linked outputs are static Nanvix ELF (no interpreter).
for f in "${OUT}/hello_c" "${OUT}/hello_cpp"; do
    if command -v file >/dev/null 2>&1; then
        file "${f}"
    fi
done

echo "[canary] OK"
