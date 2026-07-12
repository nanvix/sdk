# Verified release contract

A Git tag or mutable OCI tag is not evidence that an SDK is consumable. The
authoritative completion signal is the GitHub Release for that tag and its
`sdk-release.json` asset. It is published only after the provider image is
pushed and its non-root C, C++, Autotools, and CMake canaries pass.

## `sdk-release.json`

The contract is validated by
[`schema/sdk-release.schema.json`](../schema/sdk-release.schema.json). Schema
version 1 has these top-level fields:

- `schema_version` — always `1`;
- `sdk_version`;
- `provider_id` — provider directory/image identifier (`c-clang`);
- `provider` — implementation name from the embedded manifest (`clang`);
- `role`;
- `image` — `name`, `digest`, and immutable `ref = name@digest`;
- `target`, `toolchain`, `libc`, `compat`, and `features`.

The nested provenance objects exactly mirror
`/opt/nanvix/nanvix-sdk.json`. `libc.nanvix_version` is
`libc.nanvix_tag` without its leading `v`; the tag, exact Nanvix commit, and
sysroot SHA-256 remain present.

Generate and independently verify a contract from a pushed digest:

```sh
./z.py generate-contract \
  --image-name ghcr.io/nanvix/nanvix-sdk-c-clang \
  --digest sha256:<64-hex> --output sdk-release.json
./z.py verify-contract --contract sdk-release.json
```

Both commands pull, inspect, and run only the immutable `name@digest`
reference. Verification fails closed for a missing digest or metadata, invalid
schema, any manifest/contract difference, or any missing, extra, or different
`dev.nanvix.sdk.*` OCI label. Writes are deterministic and atomic; an identical
rerun leaves the existing file unchanged.

## Completion signals

The release workflow executes in this order:

1. build and push;
2. run canaries against the pushed digest;
3. generate and verify `sdk-release.json`;
4. idempotently create or edit the tag's GitHub Release and replace the asset;
5. optionally dispatch `nanvix-sdk-released` to `nanvix/workflows`.

No completion signal runs after a failed verification. The optional dispatch request has `event_type = nanvix-sdk-released`.
`client_payload.contract` is the complete `sdk-release.json` object,
`client_payload.release_url` is the tag's GitHub Release URL, and
`client_payload.idempotency_key` is `<sdk_version>:<image.digest>`.

`DISPATCH_TOKEN` authenticates the dispatch. Consumers must use
`idempotency_key` to collapse delivery retries. The GitHub Release remains the
authoritative polling surface when dispatch is disabled.
