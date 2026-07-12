# Overview

The Nanvix SDK packages a matched pair — the Nanvix **libc** and the Nanvix
**LLVM toolchain** — into one versioned, reviewed coordinate that downstream
ports pin. It resolves the coupling **once, at build time**, freezes it into an
OCI image, and exposes that image as the public artifact. This is the
wasi-sdk / Android-NDK model.

## Why

Building anything for Nanvix requires the matching versions of two repos:
`nanvix` (the user libc) and `nanvix/llvm` (clang/lld/compiler-rt/libc++
+ the Nanvix driver). Today that coupling is a **mutable shell default** in the
toolchain build and prose notes. There is no single, reviewed coordinate that
pins both. The SDK is that coordinate.

## Shape

A **thin packaging repository**. It holds no compiler or libc source; it pins
inputs, records the coupling, and builds + publishes the SDK image that
consumers pull and run directly.

- **Input coupling.** The LLVM half is the
  `providers/c-clang/llvm` submodule commit; the libc half is
  [`../libc.lock`](../libc.lock) (injected as `Z_NANVIX_RELEASE_TAG`). No
  changes to `nanvix` or `llvm` are required.
- **Output.** An OCI image per provider,
  `ghcr.io/nanvix/nanvix-sdk-c-clang:<version>`, plus an umbrella
  `ghcr.io/nanvix/nanvix-sdk:<version>`. An optional relocatable tarball is a
  secondary output.

## Roles, providers, layers

The SDK is **layered**, expressed as Docker image layering:

- **Role** — a capability contract. `C-SDK` (`role = c`) guarantees a shared
  **C ABI** (`i686-nanvix-sysv-1`).
- **Provider** — a concrete implementation of a role, each its own base image.
  `clang` today; additional providers can attach to the same role. Providers of
  the same role are drop-in interchangeable because they present the same C ABI
  and standardize C++ on **libc++**.
- **Layer** — a language toolchain built `FROM` a provider image. `requires:
  c_abi` maps directly to the Docker `FROM` base. Dependencies point up only.

## The image

The install prefix doubles as the sysroot, so the Clang driver resolves headers
and the static archives from one place:

```
/opt/nanvix/                # toolchain prefix == sysroot
├── bin/                    # clang clang++ lld llvm-*
├── lib/                    # crt0.o user.ld libc.a libm.a
│                           # libc++.a libc++abi.a libunwind.a
│                           # clang/<v>/lib/<triple>/libclang_rt.builtins*.a
├── usr/include/            # Nanvix C headers
├── include/c++/v1/         # libc++ headers
├── nanvix-sdk.json         # provenance + compat (mirrored as dev.nanvix.sdk.* OCI labels)
└── nanvix.cmake            # CMAKE_TOOLCHAIN_FILE
```

See [`consuming.md`](consuming.md) to use it, [`versioning.md`](versioning.md)
for how versions are cut, [`release-contract.md`](release-contract.md) for the
post-verification downstream signal, and the `adding-a-*.md` guides to extend it.
