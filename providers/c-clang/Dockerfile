# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.
#
# Builds the c-clang Nanvix SDK image:
#   ghcr.io/nanvix/nanvix-sdk-c-clang:<version>
#
# The image bundles the Nanvix LLVM toolchain (clang/clang++/lld/llvm-*), the
# cross-compiled C++ runtimes (libc++/libc++abi/libunwind + compiler-rt
# builtins), the staged Nanvix sysroot (headers + crt0.o/libc.a/user.ld/...),
# the CMake toolchain file, and the SDK manifest -- all under /opt/nanvix.
#
# Build context is the repository root (so libc.lock, schema/, cmake/ and the
# providers/c-clang tree are all reachable). z.py sets the ARGs below.
#
# The LLVM toolchain is built from source in the builder stage below via the
# fork's `z` orchestrator; the providers/c-clang/llvm submodule must be
# checked out on the host first.

# syntax=docker/dockerfile:1

ARG INSTALL_LOCATION=/opt/nanvix

# ===========================================================================
# Stage: builder -- produce /opt/nanvix (toolchain + runtimes + sysroot).
# ===========================================================================
# The SDK is always built from the pinned providers/c-clang/llvm
# submodule, so the published image is fully reproducible from the pinned inputs
# (see doc/versioning.md). There is no prebuilt-toolchain shortcut.
FROM ubuntu:24.04 AS builder

ARG INSTALL_LOCATION
# Nanvix libc release tag from libc.lock, injected into llvm/z.
ARG NANVIX_RELEASE_TAG
# Provenance resolved by z.py and recorded in the SDK manifest.
ARG NANVIX_SDK_VERSION
ARG NANVIX_LLVM_COMMIT

# The pinned Nanvix LLVM fork. Must be checked out on the host first:
#   git submodule update --init --recursive providers/c-clang/llvm
COPY providers/c-clang/llvm /src/llvm

WORKDIR /src/llvm

# Host build dependencies. The fork's `z` requires `gh` (GitHub CLI) for its tool
# check, but it is not in the base Ubuntu repositories, so register its apt
# source before running `z setup`. Also add `python3` (required by the LLVM CMake
# configure, absent from `z setup`'s list and the base image) and `sccache`
# (`z` auto-uses it as the compiler cache; the build RUN mounts a persistent
# BuildKit cache for it).
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg python3 sccache; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list; \
    ./z setup

# Compiler cache: `z` uses sccache when present (-DCMAKE_*_COMPILER_LAUNCHER).
# Point it at a fixed dir and persist that dir across builds with the BuildKit
# cache mount on the RUN below, so an invalidated build layer (e.g. a libc.lock
# bump) recompiles from cache instead of from scratch. sccache never changes the
# emitted objects, so the image stays reproducible.
#
# Cap the cache a little above its observed working set (~0.3 GB) rather than at
# the old 20G ceiling: the buildkit-cache-dance mirrors this mount onto the CI
# runner's disk (and into the actions/cache artifact), so an unbounded cap would
# let it slowly erode the disk headroom the image export needs.
ENV SCCACHE_DIR=/sccache
ENV SCCACHE_CACHE_SIZE=2G

# Two-stage build driven by the fork's `z` orchestrator (see
# llvm/.nanvix/README.md, "Building the toolchain"):
#   * stage 0 builds Clang + LLD (default triple i686-unknown-nanvix);
#   * stage 1 stages the Nanvix libc (release ${NANVIX_RELEASE_TAG}) into the
#     sysroot and cross-compiles compiler-rt builtins, libunwind, libc++, and
#     libc++abi with the stage 0 compiler.
# Everything installs under ${INSTALL_LOCATION}: the toolchain (bin/), the C/C++
# runtimes, and the staged Nanvix libc + headers share that one prefix -- the
# sysroot IS the install prefix (the fork's driver resolves the archives from
# $SYSROOT/lib), matching cmake/nanvix.cmake.in. The Nanvix libc
# release is fetched over public HTTP and handed to stage 1 via Z_NANVIX_SRC, so
# `gh` auth is never needed; the optional gh_token secret (forwarded by z.py from
# $GH_TOKEN / $GITHUB_TOKEN) only raises the release-metadata API rate limit.
RUN --mount=type=secret,id=gh_token,env=GH_TOKEN,required=false \
    --mount=type=cache,target=/sccache,sharing=locked \
    set -eux; \
    # Fetch the pinned Nanvix libc release up front (fail fast). The token, when
    # present, is written to a curl config file (never traced) and only raises
    # the GitHub API rate limit for the metadata lookup. The restrictive umask
    # that protects that secret file is confined to a subshell so it does NOT
    # leak into the toolchain staging below: otherwise every directory created
    # while installing into ${INSTALL_LOCATION} inherits mode 0700, and a
    # `docker run --user` consumer can no longer traverse /opt/nanvix/bin (or
    # /opt/nanvix/lib) to exec the toolchain.
    set +x; \
    ( umask 077; \
      if [ -n "${GH_TOKEN:-}" ]; then \
          printf 'header = "Authorization: Bearer %s"\n' "${GH_TOKEN}" > /tmp/gh.curlrc; \
      else \
          : > /tmp/gh.curlrc; \
      fi ); \
    set -x; \
    asset_url="$(curl -fsSL -K /tmp/gh.curlrc \
        "https://api.github.com/repos/nanvix/nanvix/releases/tags/${NANVIX_RELEASE_TAG}" \
        | jq -r '.assets[] | select(.name | test("nanvix-x86-microvm-standalone-release-.*[.]tar[.]bz2$")) | .browser_download_url' \
        | head -n1)"; \
    rm -f /tmp/gh.curlrc; \
    test -n "${asset_url}"; \
    mkdir -p /tmp/nanvix-libc; \
    curl -fsSL -o /tmp/nanvix-libc.tar.bz2 "${asset_url}"; \
    # Record the staged-sysroot digest for the SDK manifest / libc.lock pin.
    sha256sum /tmp/nanvix-libc.tar.bz2 | cut -d' ' -f1 > /tmp/nanvix-sysroot.sha256; \
    tar -xjf /tmp/nanvix-libc.tar.bz2 -C /tmp/nanvix-libc; \
    libc_src="$(dirname "$(dirname "$(find /tmp/nanvix-libc -path '*/include/stdlib.h' | head -n1)")")"; \
    test -n "${libc_src}"; \
    # Stage 0 -- the compiler (Clang + LLD).
    ./z configure --stage=0 \
        --install-location="${INSTALL_LOCATION}"; \
    ./z build; \
    ./z install; \
    # Stage 1 -- the runtimes, with the Nanvix libc from the release above.
    Z_NANVIX_SRC="${libc_src}" ./z configure --stage=1 \
        --install-location="${INSTALL_LOCATION}"; \
    ./z build; \
    ./z install; \
    sccache --show-stats || true; \
    ./z verify

# Trim the installed prefix before it is copied into the final image. This ships
# a *use* toolchain (compile + link for i686-unknown-nanvix), not an LLVM
# development kit, so strip debug info from the executables and drop the
# developer/test-only tools the Clang driver never invokes. clang/clang++, ld.lld
# (driver-invoked), llvm-ar/llvm-ranlib (see cmake/nanvix.cmake.in) and everything
# under lib/ (the runtimes + sysroot exercised by tests/canary) are kept, so the
# canary build still passes. Keeps the exported/loaded image within the CI
# runner's disk budget; stripping is deterministic, so the image stays reproducible.
RUN set -eux; \
    strip_bin="${INSTALL_LOCATION}/bin/llvm-strip"; \
    [ -x "${strip_bin}" ] || strip_bin="$(command -v strip || true)"; \
    du -sh "${INSTALL_LOCATION}" || true; \
    if [ -n "${strip_bin}" ]; then \
        find "${INSTALL_LOCATION}/bin" -maxdepth 1 -type f \
            -exec "${strip_bin}" --strip-unneeded {} + 2>/dev/null || true; \
    fi; \
    for tool in c-index-test clang-check clang-scan-deps clang-refactor \
                clang-rename clang-extdef-mapping clang-import-test clang-repl \
                diagtool llvm-c-test bugpoint llvm-bugpoint llvm-reduce \
                llvm-exegesis; do \
        rm -f "${INSTALL_LOCATION}/bin/${tool}"; \
    done; \
    du -sh "${INSTALL_LOCATION}" || true

# Assemble the SDK manifest (/opt/nanvix/nanvix-sdk.json) and the CMake
# toolchain file (/opt/nanvix/nanvix.cmake) from the descriptors.
COPY providers/c-clang/provider.toml /ctx/provider.toml
COPY libc.lock /ctx/libc.lock
COPY cmake/nanvix.cmake.in /ctx/nanvix.cmake.in
COPY providers/c-clang/build.py /ctx/build.py
RUN NANVIX_SDK_VERSION="${NANVIX_SDK_VERSION}" \
    NANVIX_LLVM_COMMIT="${NANVIX_LLVM_COMMIT}" \
    NANVIX_SYSROOT_SHA256="$(cat /tmp/nanvix-sysroot.sha256 2>/dev/null || true)" \
    python3 /ctx/build.py \
        --prefix "${INSTALL_LOCATION}" \
        --provider /ctx/provider.toml \
        --libc-lock /ctx/libc.lock \
        --cmake-template /ctx/nanvix.cmake.in

# ===========================================================================
# Stage: final -- ship /opt/nanvix + the port build dependencies.
# ===========================================================================
FROM ubuntu:24.04

ARG INSTALL_LOCATION

# Port build dependencies (kept in sync with the current toolchain-clang image):
# make/autotools/perl/python and ld.lld's runtime dep on libxml2.
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        make binutils libxml2 pkg-config \
        m4 autoconf automake libtool patch \
        bzip2 xz-utils gzip python3 perl file \
        ca-certificates gawk sed diffutils findutils; \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder ${INSTALL_LOCATION}/ ${INSTALL_LOCATION}/

# The toolchain (clang/clang++/lld/llvm-*) and the CMake toolchain file live
# under ${INSTALL_LOCATION}; consumers use them directly via `docker run`.
ENV PATH="${INSTALL_LOCATION}/bin:${PATH}"
ENV NANVIX_SDK_ROOT="${INSTALL_LOCATION}"

# Static image provenance. The dev.nanvix.sdk.* compatibility labels (a mirror
# of /opt/nanvix/nanvix-sdk.json) are injected by z.py via --label at build
# time, resolved from the composed artifact manifest.
LABEL org.opencontainers.image.source="https://github.com/nanvix/nanvix-sdk" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.title="nanvix-sdk-c-clang"
