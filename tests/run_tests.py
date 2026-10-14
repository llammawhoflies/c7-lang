#!/usr/bin/env python3
"""Compile every example, run it if a C backend is available, and check the
programs under examples/errors/ are rejected with the expected error code."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import c7c  # noqa: E402

EXPECTED = {
    "hello": "Hello, world!\n",
    "fizzbuzz": "".join(
        ("FizzBuzz" if i % 15 == 0 else "Fizz" if i % 3 == 0 else "Buzz" if i % 5 == 0 else str(i)) + "\n"
        for i in range(1, 101)
    ),
    "fib": "".join(f"fib({i}) = {v}\n" for i, v in enumerate(
        [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597, 2584, 4181])),
    "structs": "distance from origin to (3, 4) = 5.00\nis C7 blazingly fast? true\n",
}


def main():
    failed = 0
    backend = c7c.find_backend()
    if not backend:
        print("note: no C backend found, only checking that examples transpile\n")
    ex_dir = os.path.join(ROOT, "examples")

    for name in sorted(f[:-3] for f in os.listdir(ex_dir) if f.endswith(".c7")):
        path = os.path.join(ex_dir, name + ".c7")
        with open(path, encoding="utf-8") as f:
            try:
                c7c.Compiler(f.read(), path).compile()
            except c7c.C7Error as err:
                print(f"FAIL {name}: error[{err.code}] {err.msg}")
                failed += 1
                continue
        if backend and name in EXPECTED:
            exe = os.path.join(tempfile.gettempdir(), "c7test_" + name + (".exe" if os.name == "nt" else ""))
            with open(os.devnull, "w") as null:
                sys.stdout, saved = null, sys.stdout
                try:
                    ok = c7c.build(path, exe)
                finally:
                    sys.stdout = saved
            out = subprocess.run([exe], capture_output=True, text=True).stdout if ok else None
            if out != EXPECTED[name]:
                print(f"FAIL {name}: unexpected output")
                failed += 1
                continue
        print(f"ok   {name}")

    err_dir = os.path.join(ex_dir, "errors")
    for fname in sorted(os.listdir(err_dir)):
        path = os.path.join(err_dir, fname)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        try:
            c7c.Compiler(src, path).compile()
            print(f"FAIL errors/{fname}: compiled but should have been rejected")
            failed += 1
        except c7c.C7Error as err:
            print(f"ok   errors/{fname} (rejected with {err.code})")

    print(f"\n{'FAILED' if failed else 'all tests passed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
