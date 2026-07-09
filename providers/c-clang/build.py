#!/usr/bin/env python3

# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Assemble the c-clang SDK prefix inside the image build.

Invoked from providers/c-clang/Dockerfile after the toolchain, runtimes, and
sysroot have been staged into ``--prefix`` (default /opt/nanvix). It:

  1. Renders the per-artifact manifest to ``<prefix>/nanvix-sdk.json`` from
     provider.toml + libc.lock (validated later against
     schema/nanvix-sdk.schema.json).
  2. Renders the CMake toolchain file to ``<prefix>/nanvix.cmake`` from
     cmake/nanvix.cmake.in.

Build-time provenance is supplied by z.py and the Dockerfile through the
environment: ``NANVIX_SDK_VERSION`` (the release/image tag), ``NANVIX_LLVM_COMMIT``
(resolved from the llvm submodule checkout), and ``NANVIX_SYSROOT_SHA256`` (the
SHA-256 of the staged Nanvix release tarball). Each falls back to the descriptor
value in provider.toml / libc.lock when its variable is unset.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path


def load_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def build_manifest(provider: dict, libc: dict) -> dict:
    """Compose the nanvix-sdk.json manifest from the descriptors."""
    target = provider.get("target", {})
    toolchain = provider.get("toolchain", {})
    compat = provider.get("compat", {})
    features = provider.get("features", {})

    return {
        "schema_version": 1,
        "role": provider["role"],
        "provider": provider["provider"],
        # z.py passes the release/image tag via NANVIX_SDK_VERSION.
        "sdk_version": os.environ.get("NANVIX_SDK_VERSION", "0.0.0-dev"),
        "target": {
            "triple": target.get("triple", ""),
            "alias": target.get("alias", ""),
        },
        "toolchain": {
            "llvm_version": toolchain.get("llvm_version", ""),
            # Resolved from the submodule checkout during the image build.
            "llvm_commit": os.environ.get("NANVIX_LLVM_COMMIT", toolchain.get("llvm_commit", "")),
            "port_branch": toolchain.get("port_branch", ""),
        },
        "libc": {
            "nanvix_tag": libc.get("nanvix_tag", ""),
            "nanvix_commit": libc.get("nanvix_commit", ""),
            "sysroot_sha256": os.environ.get("NANVIX_SYSROOT_SHA256", libc.get("sysroot_sha256", "")),
        },
        "compat": {
            "c_abi": compat.get("c_abi", libc.get("c_abi", "")),
            "cxx_abi": compat.get("cxx_abi", ""),
            "abi": compat.get("abi", ""),
            "min_nanvix_os": libc.get("min_nanvix_os", ""),
        },
        "features": features,
    }


def render_cmake(template: Path, manifest: dict, prefix: str) -> str:
    """Fill the CMake toolchain template with @VAR@ placeholders."""
    subs = {
        "INSTALL_PREFIX": prefix,
        "TARGET_TRIPLE": manifest["target"]["triple"],
        "TARGET_ALIAS": manifest["target"]["alias"],
        "C_ABI": manifest["compat"]["c_abi"],
        "SDK_VERSION": manifest["sdk_version"],
    }
    text = template.read_text()
    for key, value in subs.items():
        text = text.replace(f"@{key}@", value)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="/opt/nanvix", help="Install prefix to populate.")
    parser.add_argument("--provider", required=True, type=Path, help="Path to provider.toml.")
    parser.add_argument("--libc-lock", required=True, type=Path, help="Path to libc.lock.")
    parser.add_argument("--cmake-template", required=True, type=Path, help="Path to nanvix.cmake.in.")
    args = parser.parse_args()

    provider = load_toml(args.provider)
    libc = load_toml(args.libc_lock)
    prefix = Path(args.prefix)
    prefix.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(provider, libc)
    (prefix / "nanvix-sdk.json").write_text(json.dumps(manifest, indent=2) + "\n")

    cmake = render_cmake(args.cmake_template, manifest, str(prefix))
    (prefix / "nanvix.cmake").write_text(cmake)

    print(f"[build.py] wrote {prefix}/nanvix-sdk.json and {prefix}/nanvix.cmake", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
