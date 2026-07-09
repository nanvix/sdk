# Consuming the SDK

The SDK is published as a self-contained OCI image. A downstream port pulls one
image tag and cross-compiles by running that image directly — there is no extra
tooling to install on the host.

## 1. Pull

```bash
docker pull ghcr.io/nanvix/nanvix-sdk-c-clang:<version>
```

The tag is the SDK version, formatted `v<nanvix>-sdk.<N>` (e.g.
`v0.18.46-sdk.1`): `v<nanvix>` is the Nanvix libc release the image targets and
`<N>` is that release's SDK build revision. The umbrella tag
`ghcr.io/nanvix/nanvix-sdk:<version>` carries the identical tag and aliases the
same image, so either name pulls the same release.

## 2. Build inside the image

Mount the port's source and run the toolchain. `clang`/`clang++`/`lld` and the
usual build tools are on `PATH`, and the Nanvix sysroot is baked in:

```bash
IMG=ghcr.io/nanvix/nanvix-sdk-c-clang:<version>

# One-off compile.
docker run --rm -v "$PWD:/src" -w /src "$IMG" \
    clang --target=i686-unknown-nanvix -O2 hello.c -o hello

# make / autotools ports.
docker run --rm -v "$PWD:/src" -w /src "$IMG" make
docker run --rm -v "$PWD:/src" -w /src "$IMG" ./configure --host=i686-nanvix
```

For CMake ports, point at the baked-in toolchain file:

```bash
docker run --rm -v "$PWD:/src" -w /src "$IMG" \
    cmake -B build -DCMAKE_TOOLCHAIN_FILE=/opt/nanvix/nanvix.cmake
docker run --rm -v "$PWD:/src" -w /src "$IMG" cmake --build build
```

Or build the port `FROM` the image in its own Dockerfile:

```dockerfile
FROM ghcr.io/nanvix/nanvix-sdk-c-clang:<version>
WORKDIR /src
COPY . .
RUN make
```

## 3. Inspect

The provenance + compatibility manifest is baked into the image and mirrored as
`dev.nanvix.sdk.*` OCI labels:

```bash
docker run --rm "$IMG" cat /opt/nanvix/nanvix-sdk.json
docker inspect "$IMG"      # dev.nanvix.sdk.* labels
```

Use the manifest's `compat` (`c_abi`, `cxx_abi`, `abi`, `min_nanvix_os`) and
`features` (localization, filesystem, wide_chars, compiler_rt, dynamic_loader)
to gate what a port may rely on, instead of reading per-release prose.

## 4. Run: matching kernel + `nanvixd` binaries

The SDK image is **build-time only** — it cross-compiles, it does not boot
Nanvix. To *run* what you built you need the Nanvix runtime: the guest
`kernel.elf` and the host-side monitor (`nanvixd` on Linux, `uservm`/`nanvixd`
on Windows). These are **host-native** executables (Linux ELF / Windows PE), so
they run on your host — not inside the SDK container — and the SDK does **not**
re-vendor them. They ship from the [`nanvix/nanvix`][nvx-rel] releases; the SDK
only records the coordinate that matches your image.

Run the **exact** release your image was built against, so your binary's
syscall/libc ABI matches the kernel. That coordinate is in the image manifest —
`libc.nanvix_tag` (release) and `libc.nanvix_commit` (exact commit):

```bash
IMG=ghcr.io/nanvix/nanvix-sdk-c-clang:<version>
TAG=$(docker run --rm "$IMG" cat /opt/nanvix/nanvix-sdk.json | jq -r .libc.nanvix_tag)
echo "$TAG"     # e.g. v0.18.46   (.libc.nanvix_commit is the exact commit)
```

Download the **standalone** runtime for your host (pick `128mb` or `256mb`); the
commit embedded in the filename must equal `libc.nanvix_commit`:

```bash
# Linux host
gh release download "$TAG" --repo nanvix/nanvix \
    --pattern 'nanvix-x86-microvm-standalone-release-128mb-*.tar.bz2'
mkdir -p nanvix-rt && tar -xjf nanvix-x86-microvm-standalone-release-128mb-*.tar.bz2 -C nanvix-rt
# nanvix-rt/bin/ -> kernel.elf  nanvixd.elf  procd.elf  vfsd.elf  memd.elf  mkimage.elf  mkramfs.elf
```

```powershell
# Windows host (PowerShell)
gh release download $TAG --repo nanvix/nanvix --pattern 'nanvix-windows-x86-microvm-standalone-release-128mb-*.zip'
Expand-Archive nanvix-windows-x86-microvm-standalone-release-128mb-*.zip -DestinationPath nanvix-rt
# nanvix-rt\ -> kernel.elf  nanvixd.exe  uservm.exe  mkimage.exe  mkramfs.exe
```

Boot the ELF you cross-compiled in step 2 (substitute your binary). See the
upstream [`doc/run.md`][nvx-run] for the authoritative procedure:

```bash
# Linux — requires Ubuntu 24.04 with KVM enabled
cd nanvix-rt && ./bin/nanvixd.elf -console-file /dev/stdout -- /path/to/hello
```

```powershell
# Windows — requires Windows 11 with Windows Hypervisor Platform enabled
cd nanvix-rt; .\uservm.exe -kernel .\kernel.elf -initrd C:\path\to\hello -standalone
```

One SDK version thus pins both halves: the toolchain you build with and the
kernel you run on. The SDK stays a thin packaging repo; the runtime bytes remain
owned by, and consumed directly from, `nanvix/nanvix`.

[nvx-rel]: https://github.com/nanvix/nanvix/releases
[nvx-run]: https://github.com/nanvix/nanvix/blob/dev/doc/run.md

## Target facts

| Fact           | Value                              |
| -------------- | ---------------------------------- |
| Triple         | `i686-unknown-nanvix`              |
| Alias          | `i686-nanvix`                      |
| Prefix         | `/opt/nanvix` (`$NANVIX_SDK_ROOT`) |
| Toolchain file | `/opt/nanvix/nanvix.cmake`         |
| Linkage        | static ELF (no dynamic loader)     |
| C++ runtime    | libc++                             |
