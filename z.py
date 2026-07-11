#!/usr/bin/env python3

# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix SDK builder / publisher.

Builds, validates, and releases the Nanvix SDK images. The canonical artifact is
an OCI image per provider (and, later, per layer); this script is the single
entry point that turns the pinned inputs (the llvm submodule + libc.lock)
into those images and publishes them. Every image is built from source: there is
no prebuilt-toolchain shortcut, so a published image is always reproducible from
the pinned inputs.

Consumers do not need this script or any extra tooling: they `docker pull` a
published image and run the toolchain inside it directly (see doc/consuming.md).

Usage:
    ./z.py build-image [--provider c-clang]   Build a provider/layer SDK image
    ./z.py verify [--image IMG]               Build tests/canary INSIDE the image
    ./z.py release [--push]                   Build + tag + push + write manifest
    ./z.py show [--provider c-clang]          Print the artifact manifest
    ./z.py update [--provider c-clang]        Bump pinned inputs to latest upstream
    ./z.py plan-release [--force]             Decide the next SDK release tag

The heavy build/push/verify steps run for real by default; pass --dry-run to
print the underlying commands instead of executing them.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

# ===========================================================================
# Constants
# ===========================================================================

REPO_ROOT = Path(__file__).parent.resolve()
LIBC_LOCK = REPO_ROOT / "libc.lock"
SDK_MANIFEST = REPO_ROOT / "sdk.manifest.toml"
PROVIDERS_DIR = REPO_ROOT / "providers"
LAYERS_DIR = REPO_ROOT / "layers"
SCHEMA_DIR = REPO_ROOT / "schema"
GITMODULES = REPO_ROOT / ".gitmodules"
REGISTRY = "ghcr.io/nanvix"

# Repo paths whose contents determine the built image. A release tag is cut only
# when one of these changed since the last tag, so doc/test/workflow-only pushes
# do not mint a new SDK version. providers/ carries the llvm submodule
# gitlink (the LLVM half) plus each provider's Dockerfile/build.py/provider.toml;
# libc.lock is the libc half; cmake/ is baked into the image; z.py drives the
# build; sdk.manifest.toml + schema/ are the release contract.
RELEASE_INPUT_PATHS = [
    "libc.lock",
    "sdk.manifest.toml",
    "z.py",
    "cmake",
    "schema",
    "providers",
]

# ===========================================================================
# Terminal colors
# ===========================================================================

_RESET = "\033[0m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"


def info(msg: str) -> None:
    print(f"{_GREEN}[z]{_RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{_YELLOW}[z]{_RESET} {msg}", file=sys.stderr)


def die(msg: str) -> NoReturn:
    print(f"{_RED}[z] error:{_RESET} {msg}", file=sys.stderr)
    raise SystemExit(1)


# ===========================================================================
# Helpers
# ===========================================================================

def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        die(f"missing {path}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def run(cmd: list[str], *, dry_run: bool, extra_env: dict[str, str] | None = None) -> int:
    """Run a command, or print it under --dry-run."""
    printable = " ".join(cmd)
    if dry_run:
        warn(f"DRY-RUN: {printable}")
        return 0
    info(f"$ {printable}")
    env = None
    # Force BuildKit's plain progress for a non-interactive image build (e.g.
    # output redirected to a log file): the default TTY renderer truncates lines
    # and rewrites in place, so a redirected log ends up condensed. Plain writes
    # the full per-step output linearly. Covers both the default `docker build`
    # and the `docker buildx build` used on CI (see cmd_build_image).
    is_image_build = (cmd[:2] == ["docker", "build"]
                      or cmd[:3] == ["docker", "buildx", "build"])
    if is_image_build and not sys.stdout.isatty():
        env = {**os.environ, "BUILDKIT_PROGRESS": "plain"}
    if extra_env:
        env = {**(env or os.environ), **extra_env}
    return subprocess.call(cmd, env=env)


def provider_dir(provider: str) -> Path:
    path = PROVIDERS_DIR / provider
    if not path.exists():
        die(f"unknown provider '{provider}' (looked in {path})")
    return path


def image_ref(provider: str, tag: str) -> str:
    return f"{REGISTRY}/nanvix-sdk-{provider}:{tag}"


def umbrella_ref(tag: str) -> str:
    """The umbrella SDK alias (ghcr.io/nanvix/nanvix-sdk:<tag>).

    A single public coordinate downstream ports pull the SDK by, without having
    to know the provider name; it aliases the released provider image.
    """
    return f"{REGISTRY}/nanvix-sdk:{tag}"


# ===========================================================================
# Schema validation
# ===========================================================================
# A tiny, dependency-free validator covering the JSON Schema subset used by
# schema/*.json (type / required / properties / additionalProperties / const /
# enum / pattern). Keeping it stdlib-only means z.py validates the coupling in
# any CI environment without a `pip install`.

def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.exists():
        die(f"missing schema {path}")
    return json.loads(path.read_text())


def _type_ok(value: Any, typ: str) -> bool:
    if typ == "object":
        return isinstance(value, dict)
    if typ == "array":
        return isinstance(value, list)
    if typ == "string":
        return isinstance(value, str)
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "boolean":
        return isinstance(value, bool)
    return True


def _walk_schema(instance: Any, schema: dict[str, Any], loc: str, errors: list[str]) -> None:
    typ = schema.get("type")
    if typ and not _type_ok(instance, typ):
        errors.append(f"{loc or '<root>'}: expected {typ}, got {type(instance).__name__}")
        return
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{loc or '<root>'}: expected {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{loc or '<root>'}: {instance!r} is not one of {schema['enum']}")
    if "pattern" in schema and isinstance(instance, str):
        if re.search(schema["pattern"], instance) is None:
            errors.append(f"{loc or '<root>'}: {instance!r} does not match /{schema['pattern']}/")
    if isinstance(instance, dict):
        obj = cast("dict[str, Any]", instance)
        props: dict[str, Any] = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in obj:
                errors.append(f"{loc or '<root>'}: missing required key '{key}'")
        additional: Any = schema.get("additionalProperties", True)
        for key, value in obj.items():
            sub = f"{loc}.{key}" if loc else key
            if key in props:
                _walk_schema(value, props[key], sub, errors)
            elif additional is False:
                errors.append(f"{sub}: unexpected key (additionalProperties=false)")
            elif isinstance(additional, dict):
                _walk_schema(value, cast("dict[str, Any]", additional), sub, errors)


def validate_schema(instance: Any, schema: dict[str, Any], desc: str) -> None:
    errors: list[str] = []
    _walk_schema(instance, schema, "", errors)
    if errors:
        die(f"{desc} failed schema validation:\n  - " + "\n  - ".join(errors))


# ===========================================================================
# Coupling: the two pinned halves (llvm submodule + libc.lock)
# ===========================================================================

def parse_gitmodules() -> dict[str, dict[str, str]]:
    """Parse .gitmodules into {submodule_path: {key: value}}."""
    modules: dict[str, dict[str, str]] = {}
    if not GITMODULES.exists():
        return modules
    current: str | None = None
    for raw in GITMODULES.read_text().splitlines():
        line = raw.strip()
        if line.startswith("[submodule"):
            match = re.match(r'\[submodule\s+"(.+)"\]', line)
            current = match.group(1) if match else None
            if current:
                modules[current] = {}
        elif current and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            modules[current][key.strip()] = value.strip()
    return modules


def submodule_commit(path: Path) -> str:
    """Resolve the checked-out submodule HEAD, or "" if unavailable."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return ""


def compose_manifest(provider: dict[str, Any], libc: dict[str, Any],
                     version: str, llvm_commit: str = "") -> dict[str, Any]:
    """Compose the per-artifact manifest (mirror of build.py, for validation)."""
    target = provider.get("target", {})
    toolchain = provider.get("toolchain", {})
    compat = provider.get("compat", {})
    return {
        "schema_version": 1,
        "role": provider.get("role"),
        "provider": provider.get("provider"),
        "sdk_version": version,
        "target": {
            "triple": target.get("triple", ""),
            "alias": target.get("alias", ""),
        },
        "toolchain": {
            "llvm_version": toolchain.get("llvm_version", ""),
            "llvm_commit": llvm_commit or toolchain.get("llvm_commit", ""),
            "port_branch": toolchain.get("port_branch", ""),
        },
        "libc": {
            "nanvix_tag": libc.get("nanvix_tag", ""),
            "nanvix_commit": libc.get("nanvix_commit", ""),
            "sysroot_sha256": libc.get("sysroot_sha256", ""),
        },
        "compat": {
            "c_abi": compat.get("c_abi", libc.get("c_abi", "")),
            "cxx_abi": compat.get("cxx_abi", ""),
            "abi": compat.get("abi", ""),
            "min_nanvix_os": libc.get("min_nanvix_os", ""),
        },
        "features": provider.get("features", {}),
    }


def manifest_labels(manifest: dict[str, Any]) -> list[str]:
    """Flatten the composed manifest into `--label dev.nanvix.sdk.*` build args.

    Mirrors /opt/nanvix/nanvix-sdk.json onto OCI labels so consumers can resolve
    provenance/compatibility with a plain `docker inspect`, without running the
    image. Nested manifest objects are flattened with dotted keys
    (e.g. dev.nanvix.sdk.toolchain.llvm_commit); empty values are skipped so a
    partially-resolved pin does not stamp blank labels.
    """
    def flatten(prefix: str, value: Any, out: dict[str, str]) -> None:
        if isinstance(value, dict):
            for key, sub in cast("dict[str, Any]", value).items():
                flatten(f"{prefix}.{key}", sub, out)
        elif isinstance(value, bool):
            out[prefix] = "true" if value else "false"
        elif value is not None and value != "":
            out[prefix] = str(value)

    labels: dict[str, str] = {}
    flatten("dev.nanvix.sdk", manifest, labels)
    args: list[str] = []
    for key, value in labels.items():
        args += ["--label", f"{key}={value}"]
    return args


def check_coupling(provider: dict[str, Any], libc: dict[str, Any], pdir: Path) -> None:
    """Guard the invariants tying a provider to the shared pins."""
    # The C ABI epoch is the shared interop contract: provider.toml must agree
    # with libc.lock, or ports are not actually interchangeable across providers.
    provider_abi = provider.get("compat", {}).get("c_abi")
    libc_abi = libc.get("c_abi")
    if provider_abi != libc_abi:
        die(f"c_abi mismatch: provider.toml has {provider_abi!r} "
            f"but libc.lock has {libc_abi!r}")
    # The toolchain branch declared in provider.toml must match the branch the
    # llvm submodule actually tracks in .gitmodules.
    sub_rel = str((pdir / "llvm").relative_to(REPO_ROOT))
    declared = parse_gitmodules().get(sub_rel, {}).get("branch")
    port_branch = provider.get("toolchain", {}).get("port_branch")
    if declared and port_branch and declared != port_branch:
        die(f"toolchain branch mismatch: provider.toml port_branch={port_branch!r} "
            f"but .gitmodules tracks {declared!r} for {sub_rel}")


def require_release_pins(libc: dict[str, Any], llvm_commit: str) -> None:
    """A release must freeze fully-resolved pins for both halves of the coupling."""
    missing: list[str] = []
    if not libc.get("nanvix_commit"):
        missing.append("libc.lock: nanvix_commit (the commit the tag resolves to)")
    if not llvm_commit:
        missing.append("providers/<provider>/llvm: submodule must be checked out")
    if missing:
        die("release requires fully-resolved pins; missing:\n  - " + "\n  - ".join(missing))
    # The staged-sysroot digest is computed during the build and recorded back
    # into libc.lock by cmd_release, so it is expected to be empty on the very
    # first release of a libc pin; warn rather than block in that case.
    if not libc.get("sysroot_sha256"):
        warn("libc.lock sysroot_sha256 is empty: recording it from this build.")


# ===========================================================================
# Update: bump the pinned inputs to their latest upstream versions
# ===========================================================================
# Mirror of llvm/z's `update`: query upstream metadata and rewrite the
# pins in place. The libc half is libc.lock's nanvix_tag/nanvix_commit (the
# latest nanvix/nanvix release); the LLVM half is the llvm submodule
# gitlink (the tip of its tracked branch). Stdlib-only so it runs in any CI
# environment without a `pip install`.

def github_get_json(url: str) -> dict[str, Any]:
    """GET a GitHub REST API URL as JSON, honoring $GH_TOKEN/$GITHUB_TOKEN."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "nanvix-sdk-z",
    }
    token = next(
        (os.environ[name] for name in ("GH_TOKEN", "GITHUB_TOKEN") if os.environ.get(name)),
        "",
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return cast("dict[str, Any]", json.loads(resp.read().decode()))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        die(f"GitHub API request failed for {url}: {exc}")
        raise  # unreachable: die() exits; re-raise keeps the return type total.


def github_get_file(repo: str, path: str, ref: str) -> str:
    """Fetch a repo file's text at `ref` via the contents API.

    Uses the JSON contents endpoint (inline base64) rather than the `raw` media
    type: the raw endpoint 302-redirects to a different host and urllib drops the
    Authorization header across hosts, which trips the unauthenticated rate
    limit. Staying on api.github.com keeps the request authenticated.
    """
    data = github_get_json(
        f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    )
    if data.get("encoding") != "base64" or "content" not in data:
        die(f"{repo}@{ref[:12]}: unexpected contents response for {path}")
    return base64.b64decode(data["content"]).decode()


def fork_release_tag(repo: str, ref: str) -> str:
    """Read the Nanvix libc release the fork's `z` script targets at `ref`.

    The fork encodes the coupled libc release as the default of
    NANVIX_RELEASE_TAG in its top-level `z` script. Deriving the libc pin from
    this single source of truth keeps the two SDK halves (llvm submodule
    and libc.lock) from skewing when a new libc release lands before the fork
    branch has acknowledged it.
    """
    text = github_get_file(repo, "z", ref)
    match = re.search(
        r'NANVIX_RELEASE_TAG="\$\{Z_NANVIX_RELEASE_TAG:-([^}"]+)\}"', text
    )
    if match is None:
        die(f"{repo}@{ref[:12]}: could not find NANVIX_RELEASE_TAG default in z")
        raise AssertionError  # unreachable: die() exits.
    return cast("str", match.group(1))


def resolve_ref_commit(repo: str, ref: str) -> str:
    """Resolve a branch or tag to its commit SHA via the GitHub API."""
    return cast("str", github_get_json(
        f"https://api.github.com/repos/{repo}/commits/{ref}"
    ).get("sha", ""))


def github_repo_slug(url: str) -> str:
    """Extract 'owner/name' from a GitHub submodule URL (https or ssh)."""
    match = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else ""


def set_lock_value(path: Path, key: str, value: str) -> bool:
    """Rewrite a `key = "..."` line in a TOML lock file in place.

    Returns True when the file was changed, False when it already held `value`.
    """
    text = path.read_text()
    pattern = re.compile(rf'^({re.escape(key)}\s*=\s*)"([^"]*)"', re.MULTILINE)
    match = pattern.search(text)
    if match is None:
        die(f'{path.name}: no `{key} = "..."` line to update')
    if match.group(2) == value:
        return False
    path.write_text(pattern.sub(lambda m: f'{m.group(1)}"{value}"', text, count=1))
    return True


@dataclass(frozen=True)
class LlvmTarget:
    """Resolved coupling anchor: the llvm submodule's tracked-branch tip."""

    repo: str
    branch: str
    sub: Path
    old: str
    new: str


def resolve_llvm_target(pdir: Path, provider: dict[str, Any]) -> LlvmTarget:
    """Resolve the llvm submodule's tracked-branch tip commit.

    `new` is "" when the tip cannot be resolved (missing .gitmodules entry,
    unparsable slug, or an API failure), in which case the caller skips the
    coupled bump entirely rather than landing a half-updated pin.
    """
    sub = pdir / "llvm"
    sub_rel = str(sub.relative_to(REPO_ROOT))
    module = parse_gitmodules().get(sub_rel, {})
    url = module.get("url", "")
    branch = provider.get("toolchain", {}).get("port_branch") or module.get("branch", "")
    if not url or not branch:
        warn(f"{sub_rel}: no submodule url/branch in .gitmodules; skipping LLVM bump")
        return LlvmTarget("", branch, sub, "", "")

    repo = github_repo_slug(url)
    if not repo:
        warn(f"{sub_rel}: cannot parse repo slug from {url!r}; skipping LLVM bump")
        return LlvmTarget("", branch, sub, "", "")

    old = submodule_commit(sub)
    new = resolve_ref_commit(repo, branch)
    if not new:
        warn(f"{repo}@{branch}: could not resolve branch tip; skipping LLVM bump")
        return LlvmTarget(repo, branch, sub, old, "")
    return LlvmTarget(repo, branch, sub, old, new)


def git_auth_env() -> dict[str, str]:
    """Env that authenticates github.com git fetches with $GH_TOKEN/$GITHUB_TOKEN.

    A plain `git fetch` against github.com is unauthenticated and subject to the
    anonymous scraping rate limit (HTTP 429), which failed the update workflow's
    submodule bump even though the API calls in the same run were authenticated.
    The token is injected through git's GIT_CONFIG_* env vars (as an
    http.extraheader Authorization) rather than on the command line, so it never
    lands in the logged `$ git ...` line.

    http.extraheader is *multi-valued* and accumulates across config sources, so
    the header must replace -- not supplement -- any inherited one. In CI,
    actions/checkout (persist-credentials) already configures an
    http.https://github.com/.extraheader on the submodule; a second copy makes git
    send two Authorization headers, which github.com rejects with
    `remote: Duplicate header: "Authorization"` (HTTP 400). An empty value first
    resets that inherited list so ours is the only Authorization header sent.
    """
    token = next(
        (os.environ[name] for name in ("GH_TOKEN", "GITHUB_TOKEN") if os.environ.get(name)),
        "",
    )
    if not token:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "2",
        # Empty value resets any extraheader inherited from checkout's persisted
        # credentials; without it git sends two Authorization headers (HTTP 400).
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_1": f"AUTHORIZATION: basic {basic}",
    }


def checkout_submodule(target: LlvmTarget, *, dry_run: bool) -> None:
    """Detach the submodule working tree onto the resolved branch tip.

    The gitlink bump is then committed by the caller (the update workflow's
    create-pull-request step).
    """
    sub_rel = str(target.sub.relative_to(REPO_ROOT))
    # Fetch + detach onto the new tip so the parent tree records the new gitlink.
    # The fetch is authenticated (git_auth_env) to stay off github.com's
    # anonymous rate limit, which otherwise 429s the CI submodule bump.
    if run(["git", "-C", str(target.sub), "fetch", "origin", target.branch],
           dry_run=dry_run, extra_env=git_auth_env()) != 0:
        die(f"git fetch failed for {sub_rel}")
    if run(["git", "-C", str(target.sub), "checkout", "--detach", target.new], dry_run=dry_run) != 0:
        die(f"git checkout {target.new} failed for {sub_rel}")


def emit_github_output(**pairs: str | bool) -> None:
    """Append `key=value` lines to $GITHUB_OUTPUT when running under CI."""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as fh:
        for key, value in pairs.items():
            rendered = ("true" if value else "false") if isinstance(value, bool) else value
            fh.write(f"{key}={rendered}\n")


# ===========================================================================
# Release planning: derive the next SDK tag from the pins + git history
# ===========================================================================
# The SDK version is `{libc nanvix_tag}-sdk.{N}`. The libc release is the base;
# N increments per SDK release cut against that same base, so the repo can
# publish several SDK builds for one Nanvix version when only the LLVM submodule
# or this repo's packaging changed, while a new Nanvix version restarts N at 1.

def latest_sdk_tag() -> str:
    """Most recent `v*-sdk.*` tag reachable from HEAD, or "" if there is none."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "describe", "--tags",
         "--match", "v*-sdk.*", "--abbrev=0"],
        capture_output=True, text=True,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def next_revision(base: str) -> int:
    """Next `-sdk.N` revision for `base`, one past the highest existing tag."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "tag", "--list", f"{base}-sdk.*"],
        capture_output=True, text=True,
    )
    pattern = re.compile(rf"^{re.escape(base)}-sdk\.(\d+)$")
    revisions = [
        int(match.group(1))
        for line in out.stdout.splitlines()
        if (match := pattern.match(line.strip()))
    ]
    return max(revisions) + 1 if revisions else 1


def release_inputs_changed(since_ref: str) -> bool:
    """True when any RELEASE_INPUT_PATHS entry changed between since_ref and HEAD."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--quiet", since_ref, "HEAD",
         "--", *RELEASE_INPUT_PATHS],
        capture_output=True, text=True,
    )
    # `git diff --quiet` exits 0 when identical and 1 when they differ; treat any
    # non-zero (including an unexpected error) as "changed" so a release is never
    # silently skipped.
    return result.returncode != 0


# ===========================================================================
# Release write-back: pin the resolved artifacts into the descriptors
# ===========================================================================

def image_digest(ref: str, *, dry_run: bool) -> str:
    """Return a pushed image's registry digest (`sha256:...`), or "" if unknown.

    The digest only exists after a `docker push`, so this is a no-op under
    --dry-run. Reads the first RepoDigests entry recorded locally by the push.
    """
    if dry_run:
        return ""
    out = subprocess.run(
        ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", ref],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        warn(f"could not resolve registry digest for {ref}")
        return ""
    _, _, digest = out.stdout.strip().partition("@")
    return digest


def read_image_manifest(ref: str, *, dry_run: bool) -> dict[str, Any]:
    """Read /opt/nanvix/nanvix-sdk.json out of a built image, or {} on failure."""
    if dry_run:
        return {}
    out = subprocess.run(
        ["docker", "run", "--rm", ref, "cat", "/opt/nanvix/nanvix-sdk.json"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        warn(f"could not read manifest from {ref}: {out.stderr.strip()}")
        return {}
    try:
        return cast("dict[str, Any]", json.loads(out.stdout))
    except ValueError:
        warn(f"{ref}: /opt/nanvix/nanvix-sdk.json is not valid JSON")
        return {}


def _section_span(lines: list[str], section: str | None) -> tuple[int | None, int]:
    """Return the [start, end) line span of a TOML table (None = top-level)."""
    header = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    if section is None:
        for i, line in enumerate(lines):
            if header.match(line):
                return 0, i
        return 0, len(lines)
    for i, line in enumerate(lines):
        match = header.match(line)
        if match and match.group(1).strip() == section:
            start = i + 1
            for j in range(start, len(lines)):
                if header.match(lines[j]):
                    return start, j
            return start, len(lines)
    return None, len(lines)


def set_toml_field(text: str, section: str | None, key: str, value: str) -> str:
    """Update-or-insert `key = "value"` within a TOML table, preserving comments.

    A missing key is appended after the table's last existing key (so it lands
    next to its siblings rather than at the end of the file).
    """
    lines = text.splitlines(keepends=True)
    start, end = _section_span(lines, section)
    if start is None:
        die(f"{SDK_MANIFEST.name}: table [{section}] not found")
    key_line = re.compile(rf"^(\s*){re.escape(key)}\s*=\s*")
    any_key = re.compile(r"^\s*[^#\s][^=]*=")
    last_key: int | None = None
    for i in range(start, end):
        match = key_line.match(lines[i])
        if match:
            newline = "\n" if lines[i].endswith("\n") else ""
            lines[i] = f'{match.group(1)}{key} = "{value}"{newline}'
            return "".join(lines)
        if any_key.match(lines[i]):
            last_key = i
    insert_at = (last_key + 1) if last_key is not None else start
    lines.insert(insert_at, f'{key} = "{value}"\n')
    return "".join(lines)


def update_umbrella_manifest(provider: str, tag: str, provider_digest: str,
                             umbrella_digest: str, *, dry_run: bool) -> None:
    """Pin the released sdk_version + image tags/digests into sdk.manifest.toml."""
    text = SDK_MANIFEST.read_text()
    text = set_toml_field(text, None, "sdk_version", tag)
    text = set_toml_field(text, "umbrella", "tag", tag)
    if umbrella_digest:
        text = set_toml_field(text, "umbrella", "digest", umbrella_digest)
    text = set_toml_field(text, f"providers.{provider}", "tag", tag)
    if provider_digest:
        text = set_toml_field(text, f"providers.{provider}", "digest", provider_digest)
    if dry_run:
        warn(f"DRY-RUN: would pin sdk_version={tag} + tags/digests into {SDK_MANIFEST.name}")
        return
    SDK_MANIFEST.write_text(text)
    info(f"pinned {SDK_MANIFEST.name}: sdk_version={tag}")


# ===========================================================================
# Commands
# ===========================================================================

def cmd_build_image(args: argparse.Namespace) -> None:
    """docker build a provider (or layer) SDK image from source."""
    pdir = provider_dir(args.provider)
    provider = load_toml(pdir / "provider.toml")
    if not provider.get("enabled", True):
        die(f"provider '{args.provider}' is a scaffold (enabled = false)")

    libc = load_toml(LIBC_LOCK)

    # Fail fast on a broken coupling before spending a build: validate the shared
    # libc pin, check the provider<->pin invariants, and validate the manifest
    # the image will carry.
    validate_schema(libc, load_schema("libc.lock.schema.json"), "libc.lock")
    check_coupling(provider, libc, pdir)

    tag = args.tag or args.version
    llvm_commit = submodule_commit(pdir / "llvm")
    manifest = compose_manifest(provider, libc, tag, llvm_commit)
    validate_schema(manifest, load_schema("nanvix-sdk.schema.json"),
                    f"{args.provider} manifest")

    ref = image_ref(args.provider, tag)
    # On CI a named buildx builder (docker-container driver) is passed via
    # --builder so the persistent sccache cache mount (SCCACHE_DIR=/sccache in
    # the Dockerfile) can be carried across runs: the workflow restores it with
    # actions/cache and injects it with buildkit-cache-dance. That driver does
    # not populate the local image store implicitly, so `--load` is required to
    # make the built image available to `z.py verify` and to `docker push` on
    # release. Without --builder (local dev), the default `docker build` driver
    # loads the image automatically.
    builder = getattr(args, "builder", None)
    cmd: list[str]
    if builder:
        cmd = ["docker", "buildx", "build", "--builder", builder, "--load"]
    else:
        cmd = ["docker", "build"]
    cmd += [
        "-f", str(pdir / "Dockerfile"),
        "-t", ref,
        "--build-arg", f"NANVIX_RELEASE_TAG={libc['nanvix_tag']}",
        "--build-arg", f"NANVIX_SDK_VERSION={tag}",
    ]
    if llvm_commit:
        cmd += ["--build-arg", f"NANVIX_LLVM_COMMIT={llvm_commit}"]
    # The Nanvix libc release is fetched over public HTTP during the build, so a
    # token is optional and only raises the GitHub API rate limit. Forward it as
    # a BuildKit secret when one is available in the environment.
    gh_token_env = next(
        (name for name in ("GH_TOKEN", "GITHUB_TOKEN") if os.environ.get(name)),
        "",
    )
    if gh_token_env:
        cmd += ["--secret", f"id=gh_token,env={gh_token_env}"]
    # Mirror the composed manifest onto dev.nanvix.sdk.* OCI labels; the
    # Dockerfile only carries the static org.opencontainers.image.* set.
    cmd += manifest_labels(manifest)
    cmd += [str(REPO_ROOT)]  # build context = repo root

    info(f"building {ref} from source (libc {libc['nanvix_tag']}, "
         f"c_abi {libc['c_abi']})")
    rc = run(cmd, dry_run=args.dry_run)
    if rc != 0:
        die("docker build failed")


def cmd_verify(args: argparse.Namespace) -> None:
    """Build tests/canary INSIDE the image to prove it is self-contained."""
    image: str = args.image or image_ref(args.provider, args.version)
    canary = REPO_ROOT / "tests" / "canary"
    info(f"verifying {image} against {canary}")
    # Mount tests/canary into the built image and compile it there with docker.
    # Run as a non-root user (mirroring how nanvix-zutil drives the image with
    # `--user`) so this exercises the `docker run --user` consumption path and
    # fails if the prefix was installed root-only: a root canary would happily
    # traverse a 0700 /opt/nanvix and silently miss that regression.
    cmd = ["docker", "run", "--rm"]
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
    cmd += [
        "-v", f"{canary}:/work", "-w", "/work",
        image, "sh", "./run.sh",
    ]
    rc = run(cmd, dry_run=args.dry_run)
    if rc != 0:
        die("canary verification failed")
    info("canary OK")


def cmd_release(args: argparse.Namespace) -> None:
    """Build from source, tag, push the image(s), and refresh sdk.manifest.toml."""
    pdir = provider_dir(args.provider)
    libc = load_toml(LIBC_LOCK)

    # A release is the reviewed, reproducible artifact others rely on, so every
    # pin must be fully resolved before it is cut.
    llvm_commit = submodule_commit(pdir / "llvm")
    require_release_pins(libc, llvm_commit)

    tag = args.tag or args.version
    ref = image_ref(args.provider, tag)
    info(f"releasing sdk_version={tag} to {REGISTRY}")
    cmd_build_image(args)

    # The staged-sysroot digest is computed inside the build; record it back into
    # the libc pin so subsequent builds of this pin are content-verifiable.
    manifest = read_image_manifest(ref, dry_run=args.dry_run)
    sysroot_sha256 = cast("str", manifest.get("libc", {}).get("sysroot_sha256", ""))
    if sysroot_sha256 and sysroot_sha256 != libc.get("sysroot_sha256"):
        if args.dry_run:
            warn(f"DRY-RUN: would record sysroot_sha256 in {LIBC_LOCK.name}")
        elif set_lock_value(LIBC_LOCK, "sysroot_sha256", sysroot_sha256):
            info(f"recorded sysroot_sha256={sysroot_sha256[:19]}... in {LIBC_LOCK.name}")

    provider_digest = umbrella_digest = ""
    if args.push:
        run(["docker", "push", ref], dry_run=args.dry_run)
        provider_digest = image_digest(ref, dry_run=args.dry_run)
        # Publish the umbrella alias pointing at this provider image so ports
        # can pull ghcr.io/nanvix/nanvix-sdk:<tag> by its public coordinate.
        alias = umbrella_ref(tag)
        run(["docker", "tag", ref, alias], dry_run=args.dry_run)
        run(["docker", "push", alias], dry_run=args.dry_run)
        umbrella_digest = image_digest(alias, dry_run=args.dry_run)

    # Pin the resolved sdk_version + tags/digests back into the release contract.
    update_umbrella_manifest(args.provider, tag, provider_digest, umbrella_digest,
                             dry_run=args.dry_run)


def cmd_plan_release(args: argparse.Namespace) -> None:
    """Decide the next SDK release tag from the libc pin + git tag history.

    Emits `should_release` / `version` / `reason` to $GITHUB_OUTPUT for the tag
    workflow (.github/workflows/tag.yml), which creates and pushes the tag when a
    release is warranted; that tag push then drives the release pipeline.

    The version is `{libc.lock nanvix_tag}-sdk.{N}`. A release is cut when a
    release-affecting input (RELEASE_INPUT_PATHS) changed since the last
    `v*-sdk.*` tag, when none exists yet, or when --force is given -- so a bump of
    the LLVM submodule or of this repo's packaging cuts a new SDK version for the
    same Nanvix release, while doc/test/workflow-only pushes do not.
    """
    libc = load_toml(LIBC_LOCK)
    base = cast("str", libc.get("nanvix_tag", "")).strip()
    if not base:
        die("libc.lock: nanvix_tag is empty; cannot derive an SDK version")
    if not base.startswith("v"):
        die(f"libc.lock: nanvix_tag {base!r} must start with 'v' (e.g. v0.18.46)")

    last = latest_sdk_tag()
    if not last:
        should, reason = True, "no prior SDK tag"
    elif args.force:
        should, reason = True, "forced"
    elif release_inputs_changed(last):
        should, reason = True, f"inputs changed since {last}"
    else:
        should, reason = False, f"no release-affecting change since {last}"

    version = f"{base}-sdk.{next_revision(base)}"

    if should:
        info(f"release planned: {version} ({reason})")
    else:
        info(f"no release: {reason}")
    emit_github_output(should_release=should, version=version, reason=reason)


def cmd_show(args: argparse.Namespace) -> None:
    """Print the composed artifact manifest for a provider."""
    pdir = provider_dir(args.provider)
    provider = load_toml(pdir / "provider.toml")
    libc = load_toml(LIBC_LOCK)
    llvm_commit = submodule_commit(pdir / "llvm")
    manifest = compose_manifest(provider, libc, args.version, llvm_commit)
    validate_schema(manifest, load_schema("nanvix-sdk.schema.json"),
                    f"{args.provider} manifest")
    print(json.dumps(manifest, indent=2))


def cmd_update(args: argparse.Namespace) -> None:
    """Bump the pinned toolchain inputs, keeping the two SDK halves coupled.

    The llvm branch tip is the single coupling anchor: its `z` script
    declares (via NANVIX_RELEASE_TAG) which nanvix/nanvix libc release the
    toolchain targets. Both pins are therefore derived from that one commit --
    libc.lock is set to the release the tip declares, not merely the newest
    release published upstream -- so the halves can never skew. This closes the
    race that produced PR #1, where libc.lock jumped to a fresh release while the
    llvm pin still predated the fork's own acknowledgment of it.

    Emits changed/tag/updated_vars to $GITHUB_OUTPUT so the update workflow can
    decide whether to open a pull request.
    """
    pdir = provider_dir(args.provider)
    provider = load_toml(pdir / "provider.toml")
    libc = load_toml(LIBC_LOCK)
    updated: list[str] = []

    # Resolve the coupling anchor first; everything else is derived from it.
    target = resolve_llvm_target(pdir, provider)
    tag = cast("str", libc.get("nanvix_tag", ""))
    if not target.new:
        # Tip unresolved: leave both halves untouched rather than bumping libc
        # on its own and re-introducing the skew this function exists to prevent.
        warn("llvm tip unresolved; leaving pinned inputs unchanged")
        emit_github_output(changed=False, tag=tag, updated_vars="")
        return

    # --- libc half: pin libc.lock to the release the branch tip declares ------
    tag = fork_release_tag(target.repo, target.new)
    if tag != libc.get("nanvix_tag"):
        commit = resolve_ref_commit("nanvix/nanvix", tag)
        if not commit:
            die(f"nanvix/nanvix: release tag {tag!r} declared by "
                f"{target.repo}@{target.branch} does not resolve to a commit")
        info(f"libc: {libc.get('nanvix_tag', '(unset)')} -> {tag} ({commit[:12]})")
        updated.extend(("nanvix_tag", "nanvix_commit"))
        if args.dry_run:
            warn(f"DRY-RUN: would rewrite nanvix_tag/nanvix_commit in {LIBC_LOCK.name}")
        else:
            set_lock_value(LIBC_LOCK, "nanvix_tag", tag)
            set_lock_value(LIBC_LOCK, "nanvix_commit", commit)
    else:
        info(f"libc: already up to date ({tag})")

    # --- LLVM half: advance the submodule to the coupled branch tip -----------
    if target.new != target.old:
        info(f"llvm: {target.old[:12] or '(none)'} -> {target.new[:12]}")
        updated.append("llvm")
        checkout_submodule(target, dry_run=args.dry_run)
    else:
        info(f"llvm: already up to date ({target.new[:12]})")

    # Never land an invalid libc pin: re-validate after a real rewrite.
    if "nanvix_tag" in updated and not args.dry_run:
        validate_schema(load_toml(LIBC_LOCK), load_schema("libc.lock.schema.json"), "libc.lock")

    changed = bool(updated)
    if changed:
        info(f"updated: {', '.join(updated)}")
    else:
        info("all toolchain inputs already up to date")

    emit_github_output(changed=changed, tag=tag, updated_vars=",".join(updated))


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="z.py",
        description="Nanvix SDK builder / publisher.",
    )
    # Common options, shared by every subcommand so they may follow the command
    # (e.g. `z.py build-image --provider c-clang --dry-run`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--version", default="0.0.0-dev", help="SDK version / image tag.")
    common.add_argument("--provider", default="c-clang", help="Provider (or layer) to act on.")
    common.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=False,
                        help="Print the underlying commands instead of running them. "
                             "Real execution is the default; --no-dry-run forces it explicitly.")

    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build-image", parents=[common], help="Build a provider/layer SDK image")
    p_build.add_argument("--tag", help="Override the image tag (defaults to --version).")
    p_build.add_argument("--builder",
                         help="Named buildx builder to build with (docker-container driver). "
                              "CI sets this so the sccache cache mount persists across runs; "
                              "implies `docker buildx build --load`.")
    p_build.set_defaults(func=cmd_build_image)

    p_verify = sub.add_parser("verify", parents=[common], help="Build tests/canary inside the image")
    p_verify.add_argument("--image", help="Image ref to verify (defaults to the provider tag).")
    p_verify.set_defaults(func=cmd_verify)

    p_release = sub.add_parser("release", parents=[common], help="Build + tag + push + write manifest")
    p_release.add_argument("--tag", help="Override the image tag (defaults to --version).")
    p_release.add_argument("--push", action="store_true", help="Push images to the registry.")
    p_release.add_argument("--builder",
                           help="Named buildx builder to build with (docker-container driver). "
                                "CI sets this so the sccache cache mount persists across runs; "
                                "implies `docker buildx build --load`.")
    p_release.set_defaults(func=cmd_release)

    p_show = sub.add_parser("show", parents=[common], help="Print the artifact manifest")
    p_show.set_defaults(func=cmd_show)

    p_update = sub.add_parser("update", parents=[common],
                              help="Bump pinned inputs to latest upstream versions")
    p_update.set_defaults(func=cmd_update)

    p_plan = sub.add_parser("plan-release", parents=[common],
                            help="Decide the next SDK release tag from the pins + git history")
    p_plan.add_argument("--force", action="store_true",
                        help="Plan a release even when no release-affecting input changed.")
    p_plan.set_defaults(func=cmd_plan_release)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
