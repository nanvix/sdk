# Adding a provider

A **provider** is a concrete toolchain that fills a **role**. The `c` role is
the shared C SDK; `c-clang` is the reference provider. Any provider of the `c`
role MUST present the same **C ABI** so ports are drop-in interchangeable across
providers.

## Contract

- **Same C ABI.** Stage the libc pinned by the shared [`../libc.lock`](../libc.lock)
  into the same sysroot; set `compat.c_abi` to match (`i686-nanvix-sysv-1`).
- **Same C++ runtime.** Standardize on **libc++** (never libstdc++) to avoid an
  ABI split.
- **Same prefix + target.** `/opt/nanvix`, triple `i686-unknown-nanvix`,
  alias `i686-nanvix`.
- **Same manifest.** Emit `/opt/nanvix/nanvix-sdk.json` and the
  `dev.nanvix.sdk.*` labels (reuse `providers/c-clang/build.py`).

## Steps

1. Create `providers/<role>-<provider>/`.
2. Pin the compiler source fork as a submodule under that directory (in the
   repo-root `.gitmodules`), mirroring `providers/c-clang/llvm`.
3. Write `provider.toml` — `role`, `provider`, `image`, `[target]`,
   `[toolchain]`, `[compat]`, `[features]`. Set `enabled = true`.
4. Write the `Dockerfile`: a builder stage that builds the toolchain + runtimes
   and stages the shared libc, then a final stage that ships `/opt/nanvix` +
   port build deps and stamps the labels. Reuse `build.py` for the manifest.
5. Register the image in [`../sdk.manifest.toml`](../sdk.manifest.toml)
   (`enabled = true`).
6. Verify: `./z.py verify --provider <role>-<provider>` builds
   `tests/canary` inside the new image.

See [`../providers/c-clang`](../providers/c-clang) as the worked example.
