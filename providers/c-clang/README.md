# c-clang provider

The **clang** provider for the Nanvix C SDK role. Builds
`ghcr.io/nanvix/nanvix-sdk-c-clang:<version>` from the pinned Nanvix LLVM fork
plus the libc staged per the top-level [`libc.lock`](../../libc.lock).

## Files

| File            | Purpose                                                        |
| --------------- | -------------------------------------------------------------- |
| `provider.toml` | Role/provider descriptor + compat keys stamped into the image  |
| `Dockerfile`    | Builds the SDK image (drives `./z`, stages libc, sets labels)  |
| `build.py`      | Helper invoked inside the Dockerfile to assemble `/opt/nanvix` |
| `llvm/`         | Submodule → `nanvix/llvm` at the pinned commit                 |

## The `llvm` submodule

`llvm/` is a git submodule (declared in the repo-root `.gitmodules`).
Initialize it before building:

```bash
git submodule update --init --recursive providers/c-clang/llvm
```

The submodule tracks the fork's default branch (`nanvix/v22.1.8`). Its
checked-out commit is the **LLVM half** of the SDK coupling; the **libc half**
is `libc.lock`. Bumping either is a reviewed commit that yields a new image
version.

## Building

The `Dockerfile` builder stage runs the fork's `z` orchestrator (`./z setup`,
then the stage 0 / stage 1 `configure`·`build`·`install` sequence) to build the
toolchain and runtimes from source, staging the Nanvix libc (`libc.lock`'s
`nanvix_tag`) into `/opt/nanvix/sysroot`.

LLVM is compiled in-container for reproducibility; `z` uses **sccache**,
persisted across builds via a BuildKit cache mount, so an invalidated build layer
(e.g. a `libc.lock` bump) recompiles from cache rather than from scratch.

Stage 1 stages that libc release from `nanvix/nanvix` (fetched over public HTTP)
into the sysroot, so **no GitHub token is required**. A token is optional and
only raises the GitHub API rate limit for the release-metadata lookup; when set,
`z.py` forwards `$GH_TOKEN` (or `$GITHUB_TOKEN`) as a BuildKit secret:

```bash
./z.py build-image --provider c-clang --no-dry-run   # no token needed
export GH_TOKEN=<token>                               # optional: higher rate limit
```
