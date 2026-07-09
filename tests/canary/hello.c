/* Copyright(c) The Maintainers of Nanvix. */
/* Licensed under the MIT License. */

/*
 * Canary C program: exercises libc (printf) and links against compiler-rt
 * builtins. Built INSIDE the SDK image by run.sh to prove the image is a
 * self-contained cross-toolchain. Mirrors llvm/.nanvix/tests/smoke.
 */

#include <stdio.h>

int main(void)
{
    printf("hello from nanvix-sdk (c)\n");
    return 0;
}
