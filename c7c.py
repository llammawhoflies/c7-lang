#!/usr/bin/env python3
"""c7c - the reference compiler for the C7 programming language.

C7 source is lowered to portable C99, which is then handed to the system
C compiler (gcc, clang, tcc, or `python -m ziglang cc`).

Usage:
    c7c build <file.c7> [-o OUT] [--emit-c]
    c7c run   <file.c7>
    c7c --version
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

VERSION = "0.7.2"

TYPES = {
    "i8": "int8_t", "i16": "int16_t", "i32": "int32_t", "i64": "int64_t",
    "u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "u64": "uint64_t",
    "usize": "size_t", "f32": "float", "f64": "double",
    "bool": "bool", "char": "char", "str": "const char*", "void": "void",
}

MODULES = {
    "std::io": "stdio.h",
    "std::mem": "stdlib.h",
    "std::string": "string.h",
    "std::math": "math.h",
    "std::time": "time.h",
}

BASE_INCLUDES = ["stdio.h", "stdlib.h", "stdint.h", "stdbool.h", "stddef.h"]

INT_TYPES = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "usize"}
FLOAT_TYPES = {"f32", "f64"}

RE_FN = re.compile(r"^fn\s+(\w+)\s*\((.*)\)\s*(?:->\s*(\S+))?\s*\{$")
RE_STRUCT = re.compile(r"^struct\s+(\w+)\s*\{$")
RE_FIELD = re.compile(r"^(\w+)\s*:\s*(.+?),?$")
RE_LET = re.compile(r"^let\s+(mut\s+)?(\w+)\s*(?::\s*([^=]+?))?\s*=\s*(.+);$")
RE_LET_UNINIT = re.compile(r"^let\s+(mut\s+)?(\w+)\s*:\s*(.+);$")
RE_FOR = re.compile(r"^for\s+(\w+)\s+in\s+(.+?)\s*(\.\.=?)\s*(.+?)\s*\{$")
RE_MACRO = re.compile(r"^(println|print|eprintln|panic)!\((.*)\);$")
RE_ASSIGN = re.compile(r"^(\w+)\s*[+\-*/%]?=(?!=)")
RE_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


class C7Error(Exception):
    def __init__(self, code, msg, lineno=None, help=None):
        super().__init__(msg)
        self.code, self.msg, self.lineno, self.help = code, msg, lineno, help


def split_args(s):
    """Split on top-level commas, respecting quotes and brackets."""
    out, cur, depth, quote, i = [], [], 0, None, 0
    while i < len(s):
        ch = s[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def strip_comment(s):
    """Return (code, comment) splitting off a trailing // comment outside strings."""
    quote = None
    i = 0
    while i < len(s):
        ch = s[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif s.startswith("//", i):
            return s[:i].rstrip(), s[i:]
        i += 1
    return s, ""


def strip_parens(e):
    while e.startswith("(") and e.endswith(")"):
        depth = 0
        for i, ch in enumerate(e):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and i != len(e) - 1:
                return e
        e = e[1:-1].strip()
    return e


class Compiler:
    def __init__(self, src, filename):
        self.lines = src.splitlines()
        self.filename = filename
        self.includes = list(BASE_INCLUDES)
        self.structs = {}      # name -> {field: type}
        self.fns = {}          # name -> (ret_type, params)
        self.syms = {}         # name -> (type, mutable)
        self.stack = []        # open blocks: "struct" | "fn" | "block"
        self.cur_struct = None
        self.struct_out = []
        self.body = []
        self.lineno = 0
        self.uses_math = False

    # ---- types -----------------------------------------------------------

    def ctype(self, t):
        t = t.strip()
        if t.startswith("*"):
            return self.ctype(t[1:]) + "*"
        if t in TYPES:
            return TYPES[t]
        if t in self.structs:
            return t
        raise C7Error("E0412", f"cannot find type `{t}` in this scope", self.lineno)

    def parse_params(self, s):
        params = []
        for p in split_args(s):
            m = re.fullmatch(r"(mut\s+)?(\w+)\s*:\s*(.+)", p)
            if not m:
                raise C7Error("E0561", f"malformed parameter `{p}`", self.lineno,
                              "parameters are written `name: Type`")
            params.append((m.group(2), m.group(3).strip(), bool(m.group(1))))
        return params

    def infer(self, e):
        e = strip_parens(e.strip())
        if not e:
            return None
        if RE_STRING.fullmatch(e):
            return "str"
        if re.fullmatch(r"'(?:[^'\\]|\\.)'", e):
            return "char"
        if e in ("true", "false"):
            return "bool"
        if re.fullmatch(r"-?\d+\.\d*(?:[eE][-+]?\d+)?", e):
            return "f64"
        if re.fullmatch(r"-?\d+", e):
            return "i32"
        m = re.fullmatch(r"(.+)\s+as\s+(\*?\w+)", e)
        if m:
            return m.group(2)
        if e in self.syms:
            return self.syms[e][0]
        m = re.fullmatch(r"(\w+)\.(\w+)", e)
        if m and m.group(1) in self.syms:
            st = self.syms[m.group(1)][0]
            return self.structs.get(st, {}).get(m.group(2))
        m = re.fullmatch(r"(\w+)\s*\{.*\}", e)
        if m and m.group(1) in self.structs:
            return m.group(1)
        m = re.fullmatch(r"(\w+)\s*\(.*\)", e)
        if m:
            if m.group(1) in self.fns:
                return self.fns[m.group(1)][0]
            if m.group(1) in ("sqrt", "pow", "sin", "cos", "fabs", "floor", "ceil"):
                return "f64"
        if re.search(r"==|!=|<=|>=|&&|\|\||[<>]|^!", e):
            return "bool"
        parts = [p for p in re.split(r"[+\-*/%]", e) if p.strip()]
        if len(parts) > 1:
            types = [self.infer(p) for p in parts]
            for t in types:
                if t in FLOAT_TYPES:
                    return t
            for t in types:
                if t:
                    return t
        return None

    # ---- expressions -----------------------------------------------------

    def expr(self, e):
        strings = []

        def stash(m):
            strings.append(m.group(0))
            return f"\x00{len(strings) - 1}\x00"

        e = RE_STRING.sub(stash, e)
        e = re.sub(r"\b(\w+)\s+as\s+(\*?\w+)",
                   lambda m: f"(({self.ctype(m.group(2))})({m.group(1)}))", e)

        def struct_lit(m):
            name = m.group(1)
            if name not in self.structs:
                return m.group(0)
            parts = []
            for f in split_args(m.group(2)):
                k, sep, v = f.partition(":")
                if not sep:
                    raise C7Error("E0063", f"missing `:` in initializer of `{name}`", self.lineno)
                if k.strip() not in self.structs[name]:
                    raise C7Error("E0560", f"struct `{name}` has no field named `{k.strip()}`",
                                  self.lineno)
                parts.append(f".{k.strip()} = {v.strip()}")
            return f"({name}){{ {', '.join(parts)} }}"

        e = re.sub(r"\b(\w+)\s*\{([^{}]*)\}", struct_lit, e)
        if re.search(r"\b(sqrt|pow|sin|cos|fabs|floor|ceil)\s*\(", e):
            self.uses_math = True
            if "math.h" not in self.includes:
                self.includes.append("math.h")
        return re.sub(r"\x00(\d+)\x00", lambda m: strings[int(m.group(1))], e)

    def format_macro(self, macro, args_s):
        args = split_args(args_s)
        if not args or not RE_STRING.fullmatch(args[0]):
            raise C7Error("E0001", f"format argument must be a string literal", self.lineno,
                          f'you might be missing a string literal to format with: `{macro}!("{{}}", ...)`')
        fmt, rest = args[0][1:-1], args[1:]
        pieces, cargs, pos, used = [], [], 0, 0
        for m in re.finditer(r"\{(?::\.(\d+))?\}", fmt):
            pieces.append(fmt[pos:m.start()].replace("%", "%%"))
            if used >= len(rest):
                raise C7Error("E0002", f"{used + 1} positional argument(s) in format string, "
                              f"but there is {len(rest)} argument(s)", self.lineno)
            a = rest[used]
            used += 1
            spec, carg = self.conversion(self.infer(a), self.expr(a), m.group(1))
            pieces.append(spec)
            cargs.append(carg)
            pos = m.end()
        pieces.append(fmt[pos:].replace("%", "%%"))
        if used < len(rest):
            raise C7Error("E0003", "argument never used", self.lineno,
                          "formatting specifier missing: add `{}` to the format string")
        cfmt = "".join(pieces)
        tail = "".join(", " + a for a in cargs)
        if macro == "println":
            return f'printf("{cfmt}\\n"{tail});'
        if macro == "print":
            return f'printf("{cfmt}"{tail});'
        if macro == "eprintln":
            return f'fprintf(stderr, "{cfmt}\\n"{tail});'
        return (f'fprintf(stderr, "thread \'main\' panicked at {self.filename}:{self.lineno}:\\n'
                f'{cfmt}\\n"{tail}); exit(101);')

    @staticmethod
    def conversion(t, a, precision):
        if t == "bool":
            return "%s", f'(({a}) ? "true" : "false")'
        if t in FLOAT_TYPES:
            return (f"%.{precision}f" if precision else "%g"), f"(double)({a})"
        if t == "i64":
            return "%lld", f"(long long)({a})"
        if t == "u64":
            return "%llu", f"(unsigned long long)({a})"
        if t == "usize":
            return "%zu", a
        if t in ("u8", "u16", "u32"):
            return "%u", f"(unsigned)({a})"
        if t == "str":
            return "%s", a
        if t == "char":
            return "%c", a
        return "%d", f"(int)({a})"

    # ---- statements ------------------------------------------------------

    def pass1(self):
        in_struct = None
        for i, raw in enumerate(self.lines, 1):
            self.lineno = i
            s, _ = strip_comment(raw.strip())
            if in_struct:
                if s == "}":
                    in_struct = None
                elif s:
                    m = RE_FIELD.match(s)
                    if not m:
                        raise C7Error("E0585", f"expected `field: Type`, found `{s}`", i)
                    self.structs[in_struct][m.group(1)] = m.group(2).strip()
                continue
            m = RE_STRUCT.match(s)
            if m:
                in_struct = m.group(1)
                self.structs[in_struct] = {}
                continue
            m = RE_FN.match(s)
            if m:
                name, params, ret = m.group(1), m.group(2), m.group(3) or "void"
                if name in self.fns:
                    raise C7Error("E0428", f"the name `{name}` is defined multiple times", i)
                self.fns[name] = (ret, params)

    def signature(self, name):
        ret, params_s = self.fns[name]
        params = self.parse_params(params_s)
        if name == "main":
            if params:
                raise C7Error("E0580", "`main` function has wrong type", self.lineno,
                              "`main` takes no parameters; use std::env for arguments")
            return "int main(void)"
        cparams = ", ".join(
            f"{'' if mut else 'const '}{self.ctype(t)} {n}" for n, t, mut in params
        ) or "void"
        return f"{self.ctype(ret)} {name}({cparams})"

    def assign_check(self, s):
        m = RE_ASSIGN.match(s)
        if m and m.group(1) in self.syms and not self.syms[m.group(1)][1]:
            raise C7Error("E0384", f"cannot assign twice to immutable variable `{m.group(1)}`",
                          self.lineno,
                          f"consider making this binding mutable: `let mut {m.group(1)}`")

    def translate(self, s):
        """Translate one stripped, comment-free line. Returns C text or None."""
        if not s:
            return ""

        m = re.fullmatch(r"use\s+([\w:]+);", s)
        if m:
            if m.group(1) not in MODULES:
                raise C7Error("E0432", f"unresolved import `{m.group(1)}`", self.lineno)
            if MODULES[m.group(1)] not in self.includes:
                self.includes.append(MODULES[m.group(1)])
            if m.group(1) == "std::math":
                self.uses_math = True
            return None

        if self.stack and self.stack[-1] == "struct":
            if s == "}":
                self.stack.pop()
                name, self.cur_struct = self.cur_struct, None
                return f"}} {name};"
            m = RE_FIELD.match(s)
            return f"{self.ctype(m.group(2))} {m.group(1)};"

        m = RE_STRUCT.match(s)
        if m:
            self.stack.append("struct")
            self.cur_struct = m.group(1)
            return f"typedef struct {m.group(1)} {{"

        if s == "}":
            if not self.stack:
                raise C7Error("E0004", "unexpected closing delimiter: `}`", self.lineno)
            if self.stack.pop() == "fn":
                self.syms = {}
            return "}"

        m = re.fullmatch(r"\}\s*else\s+if\s+(.+?)\s*\{", s)
        if m:
            return f"}} else if ({self.expr(m.group(1))}) {{"
        if re.fullmatch(r"\}\s*else\s*\{", s):
            return "} else {"

        m = RE_FN.match(s)
        if m:
            if self.stack:
                raise C7Error("E0005", "nested functions are not supported", self.lineno)
            self.stack.append("fn")
            self.syms = {n: (t, mut) for n, t, mut in self.parse_params(m.group(2))}
            return self.signature(m.group(1)) + " {"

        m = re.fullmatch(r"(if|while)\s+(.+?)\s*\{", s)
        if m:
            self.stack.append("block")
            return f"{m.group(1)} ({self.expr(m.group(2))}) {{"

        if s == "loop {":
            self.stack.append("block")
            return "for (;;) {"

        m = RE_FOR.match(s)
        if m:
            var, lo, op, hi = m.groups()
            t = self.infer(lo) if self.infer(lo) in INT_TYPES else "i64"
            self.syms[var] = (t, False)
            self.stack.append("block")
            cmp = "<=" if op == "..=" else "<"
            return (f"for ({self.ctype(t)} {var} = {self.expr(lo)}; "
                    f"{var} {cmp} {self.expr(hi)}; {var}++) {{")

        m = RE_LET.match(s)
        if m:
            mut, name, t, value = bool(m.group(1)), m.group(2), m.group(3), m.group(4)
            t = t.strip() if t else self.infer(value)
            if not t:
                raise C7Error("E0282", "type annotations needed", self.lineno,
                              f"consider giving `{name}` an explicit type: `let {name}: T = ...`")
            self.syms[name] = (t, mut)
            const = "" if mut or t == "str" else "const "
            return f"{const}{self.ctype(t)} {name} = {self.expr(value)};"

        m = RE_LET_UNINIT.match(s)
        if m:
            self.syms[m.group(2)] = (m.group(3).strip(), True)
            return f"{self.ctype(m.group(3))} {m.group(2)};"

        m = RE_MACRO.match(s)
        if m:
            return self.format_macro(m.group(1), m.group(2))

        if not s.endswith(";"):
            raise C7Error("E0006", "expected `;`", self.lineno)
        self.assign_check(s)
        return self.expr(s)

    def compile(self):
        try:
            self.pass1()
            for i, raw in enumerate(self.lines, 1):
                self.lineno = i
                indent = raw[: len(raw) - len(raw.lstrip())]
                code, comment = strip_comment(raw.strip())
                in_struct = (self.stack and self.stack[-1] == "struct") or RE_STRUCT.match(code)
                out = self.translate(code)
                if out is None:
                    continue
                line = (indent + out + (" " + comment if out and comment else comment)).rstrip()
                (self.struct_out if in_struct else self.body).append(line)
            if self.stack:
                self.lineno = len(self.lines)
                raise C7Error("E0007", "this file contains an unclosed delimiter", self.lineno)
            if "main" not in self.fns:
                raise C7Error("E0601", f"`main` function not found in crate `{self.crate()}`",
                              None, f"consider adding a `main` function to `{self.filename}`")
        except C7Error as err:
            if err.lineno is None:
                err.lineno = self.lineno
            raise

        protos = [self.signature(n) + ";" for n in self.fns if n != "main"]
        head = [f"// Generated by c7c {VERSION} from {os.path.basename(self.filename)}. Do not edit.",
                *[f"#include <{h}>" for h in self.includes], ""]
        parts = head
        if self.struct_out:
            parts += self.struct_out + [""]
        if protos:
            parts += protos + [""]
        return "\n".join(parts + self.body).rstrip() + "\n"

    def crate(self):
        return os.path.splitext(os.path.basename(self.filename))[0]


def report(err, compiler):
    print(f"\033[1;31merror[{err.code}]\033[0m\033[1m: {err.msg}\033[0m", file=sys.stderr)
    if err.lineno:
        src = compiler.lines[err.lineno - 1] if err.lineno <= len(compiler.lines) else ""
        col = len(src) - len(src.lstrip()) + 1
        w = len(str(err.lineno))
        pad = " " * w
        print(f"{pad}\033[1;34m-->\033[0m {compiler.filename}:{err.lineno}:{col}", file=sys.stderr)
        print(f"{pad} \033[1;34m|\033[0m", file=sys.stderr)
        print(f"\033[1;34m{err.lineno} |\033[0m {src}", file=sys.stderr)
        print(f"{pad} \033[1;34m|\033[0m {' ' * (col - 1)}\033[1;31m{'^' * max(1, len(src.strip()))}\033[0m",
              file=sys.stderr)
    if err.help:
        print(f"{' ' * len(str(err.lineno or 0))} \033[1;34m=\033[0m \033[1mhelp\033[0m: {err.help}",
              file=sys.stderr)
    print(f"\n\033[1;31merror\033[0m: could not compile `{compiler.crate()}` due to 1 previous error",
          file=sys.stderr)


def find_backend():
    for cc in ("cc", "gcc", "clang", "tcc"):
        path = shutil.which(cc)
        if path:
            return [path]
    try:
        import ziglang  # noqa: F401
        return [sys.executable, "-m", "ziglang", "cc"]
    except ImportError:
        return None


def build(path, out=None, emit_c=False):
    with open(path, encoding="utf-8") as f:
        compiler = Compiler(f.read(), path)
    try:
        c_src = compiler.compile()
    except C7Error as err:
        report(err, compiler)
        return None

    stem = compiler.crate()
    if emit_c:
        c_path = out or stem + ".c"
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(c_src)
        print(f"\033[1;32m     Emitted\033[0m {c_path}")
        return c_path

    backend = find_backend()
    if not backend:
        print("\033[1;31merror\033[0m: no C backend found (install gcc, clang or tcc, "
              "or `pip install ziglang`)", file=sys.stderr)
        print("note: use `--emit-c` to write the generated C without compiling it", file=sys.stderr)
        return None

    exe = out or stem + (".exe" if os.name == "nt" else "")
    print(f"\033[1;32m   Compiling\033[0m {stem} v{VERSION} ({path})")
    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, stem + ".c")
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(c_src)
        cmd = backend + ["-std=c99", "-O2", "-w", c_path, "-o", exe]
        if compiler.uses_math and os.name != "nt":
            cmd.append("-lm")
        if subprocess.run(cmd).returncode != 0:
            print("\033[1;31merror\033[0m: C backend failed (this is a c7c bug, please report it)",
                  file=sys.stderr)
            return None
    print(f"\033[1;32m    Finished\033[0m release [optimized] target -> {exe}")
    return exe


def main(argv=None):
    if os.name == "nt":
        os.system("")  # enable ANSI colors in the Windows console
    ap =argparse.ArgumentParser(prog="c7c", description="The C7 compiler.")
    ap.add_argument("--version", action="version", version=f"c7c {VERSION} (stable)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="compile a .c7 file")
    b.add_argument("file")
    b.add_argument("-o", "--out")
    b.add_argument("--emit-c", action="store_true", help="write the generated C and stop")
    r = sub.add_parser("run", help="compile and run a .c7 file")
    r.add_argument("file")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        return 0 if build(args.file, args.out, args.emit_c) else 1

    exe = build(args.file, os.path.join(tempfile.gettempdir(), "c7run_" +
                                       os.path.splitext(os.path.basename(args.file))[0] +
                                       (".exe" if os.name == "nt" else "")))
    if not exe:
        return 1
    print(f"\033[1;32m     Running\033[0m `{exe}`")
    return subprocess.run([exe]).returncode


if __name__ == "__main__":
    sys.exit(main())
