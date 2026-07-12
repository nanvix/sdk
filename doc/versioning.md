# Versioning

## What a version pins

An SDK version freezes the **whole coupling**:

- the `providers/c-clang/llvm` submodule commit (the LLVM half), and
- [`../libc.lock`](../libc.lock) (the libc half — `nanvix_tag`, `c_abi`,
  `min_nanvix_os`, ...).

Bumping either half is a **reviewed commit** in this repo and produces a **new
image version**. The image tag / `sdk_version` is the public coordinate; the
`c_abi` epoch + `min_nanvix_os` are the interop keys.

## Interop keys

- **`c_abi`** (`i686-nanvix-sysv-1`) — the C ABI epoch. All C providers must
  present the same value, so a port built against one provider runs with any
  provider of the same epoch. Bump the trailing epoch only on a breaking
  libc/syscall ABI change.
- **`min_nanvix_os`** — the oldest Nanvix OS the artifacts run on.

Both are surfaced in every image's `nanvix-sdk.json` and as `dev.nanvix.sdk.*`
OCI labels, so consumers resolve compatibility without reading prose.

## Build path

Every image is built from source — from the pinned
`providers/c-clang/llvm` submodule commit — for both PR verification and
releases. There is no prebuilt-toolchain shortcut, so the published image is
always reproducible from the pinned inputs.

See [`../.github/workflows/build.yml`](../.github/workflows/build.yml) and
[`release.yml`](../.github/workflows/release.yml).

## Umbrella releases

[`../sdk.manifest.toml`](../sdk.manifest.toml) composes a coherent release by
pinning the concrete image tags/digests of every enabled provider and layer
under one `sdk_version`. The umbrella tag `ghcr.io/nanvix/nanvix-sdk:<version>`
aliases that set.

The repository tag is cut before the image build starts. Publication is complete
only when that tag has a GitHub Release with a verified `sdk-release.json`
asset. See [`release-contract.md`](release-contract.md) for the immutable
downstream contract and optional dispatch signal.

## Feature gates

`features{}` in the manifest records machine-readable capability gates
(`compiler_rt = builtins-only`, `dynamic_loader = false`, ...). These track the
current Nanvix OS/libc surface and flip as blocking OS work lands (see the
"Known Limitations" section of `llvm/.nanvix/README.md`).
