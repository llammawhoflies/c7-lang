# C7 Language Reference (v0.7)

This document describes the language accepted by `c7c` 0.7.x.

## 1. Lexical structure

- Source files use the `.c7` extension and are UTF-8.
- Comments are `// line comments`. Block comments are reserved for 0.8.
- Statements end with `;`. Blocks are delimited by `{` and `}`.
- One statement per line.

## 2. Modules

`use` imports a standard module. Each module maps to a C header:

| Module        | Header      |
|---------------|-------------|
| `std::io`     | `stdio.h`   |
| `std::mem`    | `stdlib.h`  |
| `std::string` | `string.h`  |
| `std::math`   | `math.h`    |
| `std::time`   | `time.h`    |

`std::io`, `std::mem` and the integer/boolean headers are always available,
so `use std::io;` is optional but conventional.

## 3. Types

| C7      | Meaning                     |
|---------|-----------------------------|
| `i8` `i16` `i32` `i64` | signed integers |
| `u8` `u16` `u32` `u64` | unsigned integers |
| `usize` | pointer-sized unsigned integer |
| `f32` `f64` | floating point |
| `bool`  | `true` / `false` |
| `char`  | a single byte character |
| `str`   | immutable string (pointer to NUL-terminated bytes) |
| `*T`    | raw pointer to `T` |
| `void`  | no value (function returns only) |

User-defined structs are also types (§6).

## 4. Bindings

```rust
let x: i32 = 5;      // immutable, explicit type
let y = 2.5;         // immutable, inferred as f64
let mut n = 0;       // mutable, inferred as i32
let mut buf: *u8;    // mutable, uninitialized
```

Assigning to an immutable binding is error `E0384`.

Inference rules for an unannotated `let`:

- integer literal → `i32`, float literal → `f64`
- string literal → `str`, `'c'` → `char`, `true`/`false` → `bool`
- comparison or logical expression → `bool`
- `expr as T` → `T`
- a call → the function's declared return type
- a struct literal → that struct
- arithmetic → `f64` if any operand is floating point, otherwise the first operand's type

If none apply, the compiler reports `E0282: type annotations needed`.

## 5. Functions

```rust
fn add(a: i32, b: i32) -> i32 {
    return a + b;
}
```

- Parameters are immutable unless written `mut name: T`.
- Omitting `-> T` means the function returns `void`.
- Functions may be called before their definition.
- `fn main()` or `fn main() -> i32` is the entry point and takes no parameters.

## 6. Structs

```rust
struct Point {
    x: f64,
    y: f64,
}

let p = Point { x: 1.0, y: 2.0 };
println!("{}", p.x);
```

Structs have exactly the layout of the equivalent C struct.

## 7. Control flow

```rust
if cond { } else if other { } else { }
while cond { }
loop { }                  // infinite; leave with `break;`
for i in 0..10 { }        // 0 to 9
for i in 1..=10 { }       // 1 to 10
```

Conditions do not take parentheses.

## 8. Casts

`expr as T` converts between numeric types and pointers.

## 9. Formatted output

| Macro                 | Writes to | Newline |
|-----------------------|-----------|---------|
| `print!(fmt, ...)`    | stdout    | no      |
| `println!(fmt, ...)`  | stdout    | yes     |
| `eprintln!(fmt, ...)` | stderr    | yes     |
| `panic!(fmt, ...)`    | stderr    | yes, then exits with status 101 |

Placeholders: `{}` formats any value by its type (booleans print as
`true`/`false`). `{:.N}` prints a float with `N` decimal places. The number of
placeholders must match the number of arguments (`E0002`, `E0003`).

## 10. Calling C

Any C function from an imported header can be called directly:

```rust
use std::math;
let r = sqrt(2.0);
```

## Appendix: error index

| Code  | Meaning |
|-------|---------|
| E0001 | format argument must be a string literal |
| E0002 | not enough arguments for format string |
| E0003 | argument never used |
| E0004 | unexpected closing delimiter |
| E0005 | nested functions are not supported |
| E0006 | expected `;` |
| E0007 | unclosed delimiter |
| E0063 | malformed struct initializer |
| E0282 | type annotations needed |
| E0384 | cannot assign twice to immutable variable |
| E0412 | unknown type |
| E0428 | name defined multiple times |
| E0432 | unresolved import |
| E0560 | struct has no such field |
| E0561 | malformed parameter |
| E0580 | `main` has the wrong type |
| E0585 | malformed struct field |
| E0601 | `main` function not found |
