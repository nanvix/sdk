#!/usr/bin/env python3

# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Pure unit tests for the verified SDK release contract."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("sdk_z", ROOT / "z.py")
assert SPEC and SPEC.loader
z = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = z
SPEC.loader.exec_module(z)
BUILD_SPEC = importlib.util.spec_from_file_location(
    "c_clang_build", ROOT / "providers" / "c-clang" / "build.py"
)
assert BUILD_SPEC and BUILD_SPEC.loader
provider_build = importlib.util.module_from_spec(BUILD_SPEC)
sys.modules[BUILD_SPEC.name] = provider_build
BUILD_SPEC.loader.exec_module(provider_build)

DIGEST = "sha256:" + "a" * 64
IMAGE_NAME = "ghcr.io/nanvix/nanvix-sdk-c-clang"
IMAGE_REF = f"{IMAGE_NAME}@{DIGEST}"


def manifest() -> dict:
    return {
        "schema_version": 1,
        "role": "c",
        "provider": "clang",
        "sdk_version": "v0.20.0-sdk.1",
        "target": {
            "triple": "i686-unknown-nanvix",
            "alias": "i686-nanvix",
        },
        "toolchain": {
            "llvm_version": "22.1.8",
            "llvm_commit": "b" * 40,
            "port_branch": "nanvix/v22.1.8",
        },
        "libc": {
            "nanvix_tag": "v0.20.0",
            "nanvix_version": "0.20.0",
            "nanvix_commit": "c" * 40,
            "sysroot_sha256": "d" * 64,
        },
        "compat": {
            "c_abi": "i686-nanvix-sysv-1",
            "cxx_abi": "libc++",
            "abi": "static-elf",
            "min_nanvix_os": "0.19.9",
        },
        "features": {
            "localization": True,
            "filesystem": True,
            "wide_chars": True,
            "compiler_rt": "builtins-only",
            "dynamic_loader": False,
        },
    }


class ReleaseContractTests(unittest.TestCase):
    work = ROOT / "tests" / ".contract-test-work"

    def tearDown(self) -> None:
        if self.work.exists():
            shutil.rmtree(self.work)

    def test_successful_immutable_verification_and_nested_mirror(self) -> None:
        embedded = manifest()
        labels = z.flattened_manifest_labels(embedded)
        with (
            mock.patch.object(z, "run", return_value=0) as run,
            mock.patch.object(z, "inspect_image_labels", return_value=labels) as inspect,
            mock.patch.object(z, "read_image_manifest", return_value=embedded) as read,
        ):
            verified = z.verify_image_metadata(IMAGE_REF, pull=True)

        self.assertEqual(verified, embedded)
        run.assert_called_once_with(["docker", "pull", IMAGE_REF], dry_run=False)
        inspect.assert_called_once_with(IMAGE_REF)
        read.assert_called_once_with(IMAGE_REF, dry_run=False)

        contract = z.build_release_contract("c-clang", IMAGE_NAME, DIGEST, verified)
        for key in ("target", "toolchain", "libc", "compat", "features"):
            self.assertEqual(contract[key], embedded[key])
        self.assertEqual(contract["image"]["ref"], IMAGE_REF)

    def test_verification_fails_on_any_label_mismatch(self) -> None:
        embedded = manifest()
        labels = z.flattened_manifest_labels(embedded)
        labels["dev.nanvix.sdk.compat.c_abi"] = "wrong"
        with (
            mock.patch.object(z, "inspect_image_labels", return_value=labels),
            mock.patch.object(z, "read_image_manifest", return_value=embedded),
            self.assertRaises(SystemExit),
        ):
            z.verify_image_metadata(IMAGE_REF)

    def test_atomic_contract_write_is_deterministic_and_idempotent(self) -> None:
        contract = z.build_release_contract("c-clang", IMAGE_NAME, DIGEST, manifest())
        output = self.work / "sdk-release.json"
        self.assertTrue(z.atomic_write_json(output, contract))
        first = output.read_bytes()
        self.assertFalse(z.atomic_write_json(output, contract))
        self.assertEqual(output.read_bytes(), first)
        self.assertEqual(first, z.deterministic_json(contract).encode())

    def test_payload_is_stable_and_digest_idempotent(self) -> None:
        contract = z.build_release_contract("c-clang", IMAGE_NAME, DIGEST, manifest())
        release_url = "https://github.com/nanvix/sdk/releases/tag/v0.20.0-sdk.1"
        first = z.dispatch_payload(contract, release_url)
        second = z.dispatch_payload(contract, release_url)
        self.assertEqual(first, second)
        self.assertEqual(first["event_type"], "nanvix-sdk-released")
        self.assertEqual(first["client_payload"]["contract"], contract)
        self.assertEqual(
            first["client_payload"]["idempotency_key"],
            f"v0.20.0-sdk.1:{DIGEST}",
        )

    def test_missing_digest_and_bad_derived_version_fail_closed(self) -> None:
        with self.assertRaises(SystemExit):
            z.immutable_image_ref(IMAGE_NAME, "")
        embedded = manifest()
        embedded["libc"]["nanvix_version"] = "0.19.9"
        with self.assertRaises(SystemExit):
            z.build_release_contract("c-clang", IMAGE_NAME, DIGEST, embedded)

    def test_embedded_manifest_rejects_wrong_staged_sysroot(self) -> None:
        provider = {
            "role": "c",
            "provider": "clang",
            "target": {},
            "toolchain": {},
            "compat": {},
            "features": {},
        }
        libc = {
            "nanvix_tag": "v0.20.0",
            "sysroot_sha256": "d" * 64,
        }
        with (
            mock.patch.dict(
                provider_build.os.environ,
                {"NANVIX_SYSROOT_SHA256": "e" * 64},
                clear=True,
            ),
            self.assertRaises(ValueError),
        ):
            provider_build.build_manifest(provider, libc)


if __name__ == "__main__":
    unittest.main()
