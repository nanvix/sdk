// Copyright(c) The Maintainers of Nanvix.
// Licensed under the MIT License.

//
// Canary C++ program: exercises new/delete (libc++abi) and std::printf (libc),
// linked against libc++, libc++abi, libunwind, and compiler-rt. Built INSIDE
// the SDK image by run.sh. Mirrors llvm/.nanvix/tests/smoke.
//

#include <cstdio>

struct Greeter {
    const char *msg;
    explicit Greeter(const char *m) : msg(m) {}
    void hello() const { std::printf("%s\n", msg); }
};

int main()
{
    Greeter *g = new Greeter("hello from nanvix-sdk (c++)");
    g->hello();
    delete g;
    return 0;
}
