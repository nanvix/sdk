# Adding a layer

A **layer** is a language toolchain (e.g. Rust) built `FROM` a provider image.
Its `requires` capability maps directly to the Docker `FROM` base: a layer that
`requires = "c_abi"` builds on top of any `c` provider image. **Dependencies
point up only** — layers depend on providers, never the reverse.

## Contract

- **Build FROM a provider.** `ARG BASE_IMAGE` is a concrete C provider image;
  `z.py` resolves it from [`../sdk.manifest.toml`](../sdk.manifest.toml).
- **Declare requirements.** `requires` in `layer.toml` states the capability the
  base must satisfy (e.g. `c_abi`). Re-stamp the image manifest's `requires{}`.
- **Keep the ABI.** A layer never changes the C ABI or C++ runtime it inherits.

## Steps

1. Create `layers/<name>/`.
2. Write `layer.toml` — `kind = "layer"`, `name`, `requires`, `image`,
   `[target]`, `[toolchain]`. Set `enabled = true`.
3. Write the `Dockerfile`: `FROM ${BASE_IMAGE}`, install the language toolchain,
   register the `i686-unknown-nanvix` target, and re-stamp the labels/manifest.
4. Register the image in [`../sdk.manifest.toml`](../sdk.manifest.toml)
   (`enabled = true`).
5. Verify by building a minimal program of the language inside the image.

## Rust specifics

A Rust layer would reuse the
[`nanvix-cargo`](https://github.com/nanvix/nanvix-cargo) binary-cache pattern
(a live `i686-unknown-nanvix` Rust target) for runtime dependency resolution.
