# Nanvix SDK

The **Nanvix SDK** is the single, reviewed coordinate that downstream ports pin
to cross-compile software for [Nanvix](https://github.com/nanvix/nanvix). It
resolves — **once, at build time** — the coupling between the two repositories
that every Nanvix build otherwise has to match by hand:

- **`nanvix`** — the user libc: headers, `crt0.o`, `libc.a`, `user.ld`, and the
  syscall ABI.
- **`nanvix/llvm`** — the Clang/LLD/compiler-rt/libc++ toolchain plus
  the Nanvix driver.

Instead of leaving that coupling as a mutable shell default, the SDK freezes it
into a **versioned OCI image** and exposes it as one coordinate. This is the
wasi-sdk / Android-NDK model. See [`doc/overview.md`](doc/overview.md) for the
design.

## Canonical artifact

```
ghcr.io/nanvix/nanvix-sdk-c-clang:<version>     # the C SDK image (clang provider)
ghcr.io/nanvix/nanvix-sdk:<version>             # umbrella tag for a coherent release
```

The compatibility/provenance manifest is embedded in each image **both** as OCI
labels (`dev.nanvix.sdk.*`) and as the file `/opt/nanvix/nanvix-sdk.json`.
An optional relocatable tarball (`nanvix-sdk-<ver>-<host>.tar.zst`) is a
secondary output for native / non-Docker use.

## Layout

The SDK is layered. `C-SDK` is a **role** with a shared **C ABI** contract;
`clang` is the current **provider** (a base image), and more C providers can
attach to the same role. Language toolchains attach as **layers** built `FROM` a
provider image.

```
nanvix-sdk/
├── libc.lock                 # SHARED C-provider pin: nanvix libc + c_abi + min_nanvix_os
├── sdk.manifest.toml         # umbrella: image tags composing a coherent release
├── z.py                      # builder/publisher: build-image · verify · release · show
├── schema/                   # JSON schemas for the manifest and libc.lock
├── providers/
│   └── c-clang/              # clang provider (llvm submodule + Dockerfile + build.py)
├── cmake/nanvix.cmake.in     # baked into the image as the CMAKE_TOOLCHAIN_FILE
├── tests/canary/             # hello.c / hello.cpp built INSIDE the image
├── .github/workflows/        # build.yml (PR) · release.yml (push to ghcr)
└── doc/                      # overview · consuming · versioning · adding-a-provider · adding-a-layer
```

## Quick start

Build the C SDK image and validate it end-to-end:

```bash
git submodule update --init --recursive
./z.py build-image --provider c-clang       # builds ghcr.io/nanvix/nanvix-sdk-c-clang:<version>
./z.py verify                               # builds tests/canary INSIDE the image
```

Consume it from a port (e.g. `zlib`) by pulling the image and building inside it:

```bash
IMG=ghcr.io/nanvix/nanvix-sdk-c-clang:<version>
docker pull "$IMG"                          # download the published SDK image
docker run --rm -v "$PWD:/src" -w /src "$IMG" make   # build the port inside it
```

## How the coupling is pinned

| Half           | Pinned by                                        |
| -------------- | ------------------------------------------------ |
| LLVM toolchain | `providers/c-clang/llvm` submodule commit        |
| Nanvix libc    | `libc.lock` (injected as `Z_NANVIX_RELEASE_TAG`) |

Every image is built from source from these pinned inputs — there is no
prebuilt-toolchain shortcut — so a published image is reproducible from its
version alone. Bumping either half is a **reviewed commit** in this repo, which
produces a new SDK image version. No changes are required to `nanvix` or
`llvm`.

## License

MIT — see [`LICENSE.txt`](LICENSE.txt).
