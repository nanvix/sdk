# Canary tests

Build-only smoke tests that run **inside** the SDK image to prove it is a
self-contained cross-toolchain. They mirror the LLVM port's smoke tests
(`llvm/.nanvix/tests/`).

| File        | Exercises                                                      |
| ----------- | -------------------------------------------------------------- |
| `hello.c`   | libc (`printf`) + compiler-rt builtins                         |
| `hello.cpp` | `new`/`delete` (libc++abi) + libc++ + libunwind + compiler-rt  |
| `run.sh`    | Compiles + links both for `i686-unknown-nanvix` (does not run) |

Driven by `./z.py verify`, which mounts this directory into the built image and
runs `run.sh` with `docker run`.

> Optional (future): also build the `zlib` port through the image as a heavier
> end-to-end check.
