# Canary tests

Build-only smoke tests that run **inside** the SDK image to prove it is a
self-contained cross-toolchain. They mirror the LLVM port's smoke tests
(`llvm/.nanvix/tests/`).

| Path          | Exercises                                                     |
| ------------- | ------------------------------------------------------------- |
| `hello.c`     | libc (`printf`) + compiler-rt builtins                        |
| `hello.cpp`   | `new`/`delete` (libc++abi) + libc++ + libunwind + compiler-rt |
| `autotools/`  | Autoconf/Automake configure and make flow                     |
| `cmake/`      | The baked-in `/opt/nanvix/nanvix.cmake` toolchain file        |
| `run.sh`      | Builds all four static i386 ELFs and rejects `PT_INTERP`      |

Driven by `./z.py verify`, which mounts this directory into the built image and
runs `run.sh` with `docker run`.

> Optional (future): also build the `zlib` port through the image as a heavier
> end-to-end check.
