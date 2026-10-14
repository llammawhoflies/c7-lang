<div align="center">

# C7

**Rust's syntax, C's ABI. Nothing else.**

![version](https://img.shields.io/badge/version-0.7.2-orange)
![license](https://img.shields.io/badge/license-MIT-blue)
![backend](https://img.shields.io/badge/backend-C99-lightgrey)
![status](https://img.shields.io/badge/status-stable-brightgreen)

</div>

---

C7 is a small systems language for people who like how Rust *reads* but need
what C *ships*. Every C7 program compiles to plain, readable C99. That means
it runs anywhere a C compiler does, links against any C library with no FFI
layer, and produces binaries your existing toolchain already understands.

```rust
use std::io;

fn main() {
    let name = "world";
    println!("Hello, {}!", name);
}
```

## Why C7?

- **Immutable by default.** `let` bindings are `const`. You opt in to mutation with `let mut`.
- **Rust-style diagnostics.** Error codes, source spans and a `help:` line on every error.
- **Zero-overhead interop.** A C7 `struct` *is* a C struct. A C7 `fn` *is* a C function. You can call `sqrt` without writing bindings.
- **Type inference** for locals: `let dx = a.x - b.x;` just works.
- **No runtime, no GC, no build system.** One compiler file and a C backend.
- **Readable output.** Run `c7c build --emit-c` to see exactly what your code becomes.

## Install

C7 needs Python 3.8+ and any C compiler (`gcc`, `clang`, `tcc`, or `pip install ziglang`).

```bash
git clone https://github.com/<you>/c7.git
cd c7
python c7c.py --version
```

## Usage

```bash
python c7c.py run examples/hello.c7          # compile and run
python c7c.py build examples/fib.c7 -o fib   # produce a binary
python c7c.py build examples/structs.c7 --emit-c   # just show me the C
```

## A taste

```rust
use std::math;

struct Point {
    x: f64,
    y: f64,
}

fn distance(a: Point, b: Point) -> f64 {
    let dx = a.x - b.x;
    let dy = a.y - b.y;
    return sqrt(dx * dx + dy * dy);
}

fn main() {
    let p = Point { x: 3.0, y: 4.0 };
    println!("{:.2}", distance(Point { x: 0.0, y: 0.0 }, p));
}
```

And when you get it wrong:

```text
error[E0384]: cannot assign twice to immutable variable `x`
 --> examples/errors/immutable.c7:6:5
  |
6 |     x = 6;
  |     ^^^^^^
  = help: consider making this binding mutable: `let mut x`

error: could not compile `immutable` due to 1 previous error
```

## Language overview

See [`docs/SPEC.md`](docs/SPEC.md) for the full reference. In short:

| C7                          | C99                                   |
|-----------------------------|---------------------------------------|
| `i8 … i64`, `u8 … u64`      | `int8_t … int64_t`, `uint8_t … uint64_t` |
| `f32`, `f64`, `bool`, `char`| `float`, `double`, `bool`, `char`     |
| `str`                       | `const char*`                         |
| `*T`                        | `T*`                                  |
| `let x = 1;`                | `const int32_t x = 1;`                |
| `let mut x = 1;`            | `int32_t x = 1;`                      |
| `for i in 0..n { }`         | `for (int32_t i = 0; i < n; i++) { }` |
| `loop { }`                  | `for (;;) { }`                        |
| `x as f64`                  | `((double)(x))`                       |
| `println!("{}", x);`        | `printf("%d\n", x);`                  |
| `panic!("oops");`           | prints to stderr, exits with 101      |

## Roadmap

- [x] Structs, functions, type inference for locals
- [x] Rust-style formatted printing (`{}`, `{:.N}`)
- [x] Immutability checking
- [ ] `enum` and `match`
- [ ] Slices (`[T]`) with bounds checking
- [ ] `defer`
- [ ] Self-hosting compiler (targeted for 1.0)

## Contributing

Issues and PRs are welcome. Run `python tests/run_tests.py` before submitting.

## License

MIT. See [LICENSE](LICENSE).
