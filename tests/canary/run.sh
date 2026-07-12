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
# Or directly:                       docker run --rm \
#                                        --user "$(id -u):$(id -g)" \
#                                        -v "$PWD/tests/canary:/work" \
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

echo "[canary] building with Autotools"
AUTOTOOLS_OUT="${OUT}/autotools"
mkdir -p "${AUTOTOOLS_OUT}"
cp autotools/configure.ac autotools/Makefile.am hello.c "${AUTOTOOLS_OUT}/"
(
    cd "${AUTOTOOLS_OUT}"
    autoreconf -fi
    CC=clang CFLAGS="--target=${TARGET} -O2" \
        ./configure --host=i686-nanvix
    make
)

echo "[canary] building with CMake"
cmake -S cmake -B "${OUT}/cmake" \
    -DCMAKE_TOOLCHAIN_FILE=/opt/nanvix/nanvix.cmake
cmake --build "${OUT}/cmake"

echo "[canary] artifacts:"
ls -l "${OUT}"

# Confirm every build path emits static i386 ELF with no PT_INTERP segment.
for f in "${OUT}/hello_c" "${OUT}/hello_cpp" \
         "${AUTOTOOLS_OUT}/hello_autotools" "${OUT}/cmake/hello_cmake"; do
    if command -v file >/dev/null 2>&1; then
        file "${f}"
    fi
    readelf -h "${f}" | grep -Eq 'Class:[[:space:]]+ELF32'
    readelf -h "${f}" | grep -Eq 'Machine:[[:space:]]+Intel 80386'
    if readelf -l "${f}" | grep -q 'INTERP'; then
        echo "[canary] unexpected PT_INTERP in ${f}" >&2
        exit 1
    fi
    if readelf -l "${f}" | grep -q 'DYNAMIC'; then
        echo "[canary] expected a static ELF, found PT_DYNAMIC in ${f}" >&2
        exit 1
    fi
done

echo "[canary] OK"
