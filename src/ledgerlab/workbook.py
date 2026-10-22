"""In-memory workbook with a small, deterministic formula engine.

The engine deliberately supports only the grammar the task generator emits
(cell refs, cross-sheet refs, ranges inside functions, + - * / ^, unary minus,
parentheses, SUM/AVERAGE/MIN/MAX/ROUND/ABS). Keeping it small makes rewards
reproducible bit-for-bit and removes any dependency on Excel or LibreOffice.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

Content = float | int | str | None  # str starting with "=" is a formula


# --------------------------------------------------------------------------- #
# Addressing helpers
# --------------------------------------------------------------------------- #

_ADDR_RE = re.compile(r"^\$?([A-Z]{1,3})\$?(\d+)$")


def col_to_idx(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


def idx_to_col(idx: int) -> str:
    s = ""
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def split_addr(addr: str) -> tuple[str, int]:
    m = _ADDR_RE.match(addr.upper())
    if not m:
        raise ValueError(f"bad cell address: {addr!r}")
    return m.group(1), int(m.group(2))


def shift_col(col: str, by: int) -> str:
    return idx_to_col(col_to_idx(col) + by)


def expand_range(a: str, b: str) -> list[str]:
    c1, r1 = split_addr(a)
    c2, r2 = split_addr(b)
    i1, i2 = sorted((col_to_idx(c1), col_to_idx(c2)))
    r1, r2 = sorted((r1, r2))
    return [f"{idx_to_col(i)}{r}" for r in range(r1, r2 + 1) for i in range(i1, i2 + 1)]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class CellError(str):
    """Excel-style error value (#DIV/0!, #VALUE!, #REF!, #CYCLE!, #PARSE!)."""


class _EvalError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------- #
# Parser (recursive descent)  ->  AST of tuples
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<func>[A-Z]+(?=\())
  | (?P<ref>(?:[A-Za-z_][A-Za-z0-9_]*!)?\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)
  | (?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<op>[-+*/^(),])
    """,
    re.X,
)

FUNCS = {"SUM", "AVERAGE", "MIN", "MAX", "ROUND", "ABS"}


def _tokenize(src: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            raise _EvalError("#PARSE!")
        pos = m.end()
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group()))
    return out


class _Parser:
    def __init__(self, tokens, sheet):
        self.t, self.i, self.sheet = tokens, 0, sheet

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if tok[0] is None or (value is not None and tok[1] != value):
            raise _EvalError("#PARSE!")
        self.i += 1
        return tok

    def parse(self):
        node = self.expr()
        if self.i != len(self.t):
            raise _EvalError("#PARSE!")
        return node

    def expr(self):
        node = self.term()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.power()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            node = ("bin", op, node, self.power())
        return node

    def power(self):
        node = self.unary()
        if self.peek()[1] == "^":
            self.take()
            node = ("bin", "^", node, self.power())
        return node

    def unary(self):
        if self.peek()[1] == "-":
            self.take()
            return ("neg", self.unary())
        if self.peek()[1] == "+":
            self.take()
            return self.unary()
        return self.primary()

    def primary(self):
        kind, val = self.peek()
        if kind == "num":
            self.take()
            return ("num", float(val))
        if kind == "func":
            self.take()
            if val not in FUNCS:
                raise _EvalError("#NAME?")
            self.take("(")
            args = []
            if self.peek()[1] != ")":
                args.append(self.arg())
                while self.peek()[1] == ",":
                    self.take()
                    args.append(self.arg())
            self.take(")")
            return ("call", val, args)
        if kind == "ref":
            self.take()
            node = self._ref(val)
            if node[0] == "range":
                raise _EvalError("#VALUE!")  # bare ranges only allowed as function args
            return node
        if val == "(":
            self.take()
            node = self.expr()
            self.take(")")
            return node
        raise _EvalError("#PARSE!")

    def arg(self):
        kind, val = self.peek()
        if kind == "ref" and ":" in val:
            self.take()
            return self._ref(val)
        return self.expr()

    def _ref(self, text):
        sheet = self.sheet
        if "!" in text:
            sheet, text = text.split("!", 1)
        if ":" in text:
            a, b = text.split(":")
            return ("range", sheet, tuple(expand_range(a.replace("$", ""), b.replace("$", ""))))
        return ("ref", sheet, text.replace("$", ""))


@lru_cache(maxsize=65536)
def parse_formula(formula: str, sheet: str):
    """Parse '=...' into an AST. Cached; raises _EvalError on bad syntax."""
    return _Parser(_tokenize(formula.lstrip("=")), sheet).parse()


def references(formula: str, sheet: str) -> list[tuple[str, str]]:
    """All (sheet, addr) cells a formula reads. Empty list on parse error."""
    try:
        ast = parse_formula(formula, sheet)
    except _EvalError:
        return []
    out: list[tuple[str, str]] = []

    def walk(n):
        k = n[0]
        if k == "ref":
            out.append((n[1], n[2]))
        elif k == "range":
            out.extend((n[1], a) for a in n[2])
        elif k == "neg":
            walk(n[1])
        elif k == "bin":
            walk(n[2])
            walk(n[3])
        elif k == "call":
            for a in n[2]:
                walk(a)

    walk(ast)
    return out


# --------------------------------------------------------------------------- #
# Workbook
# --------------------------------------------------------------------------- #


def is_formula(content: Content) -> bool:
    return isinstance(content, str) and content.startswith("=")


@dataclass
class Workbook:
    sheets: dict[str, dict[str, Content]] = field(default_factory=dict)
    _cache: dict | None = field(default=None, repr=False, compare=False)

    # ---- content ---------------------------------------------------------- #
    def add_sheet(self, name: str) -> None:
        self.sheets.setdefault(name, {})

    def get(self, sheet: str, addr: str) -> Content:
        return self.sheets.get(sheet, {}).get(addr.upper().replace("$", ""))

    def set(self, sheet: str, addr: str, content: Content) -> None:
        if sheet not in self.sheets:
            raise KeyError(f"no sheet named {sheet!r}")
        addr = addr.upper().replace("$", "")
        split_addr(addr)
        if content is None or content == "":
            self.sheets[sheet].pop(addr, None)
        else:
            self.sheets[sheet][addr] = content
        self._cache = None

    def cells(self):
        for s, cells in self.sheets.items():
            for a, c in cells.items():
                yield s, a, c

    def copy(self) -> "Workbook":
        return Workbook({s: dict(c) for s, c in self.sheets.items()})

    # ---- evaluation ------------------------------------------------------- #
    def value(self, sheet: str, addr: str):
        if self._cache is None:
            self._cache = {}
        return self._value(sheet, addr.upper().replace("$", ""), set())

    def _value(self, sheet, addr, stack):
        key = (sheet, addr)
        if key in self._cache:
            return self._cache[key]
        if sheet not in self.sheets:
            return CellError("#REF!")
        content = self.sheets[sheet].get(addr)
        if not is_formula(content):
            v = content
        elif key in stack:
            return CellError("#CYCLE!")
        else:
            stack.add(key)
            try:
                v = self._eval(parse_formula(content, sheet), stack)
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    v = CellError("#NUM!")
            except _EvalError as e:
                v = CellError(e.code)
            except ZeroDivisionError:
                v = CellError("#DIV/0!")
            except OverflowError:
                v = CellError("#NUM!")
            stack.discard(key)
        self._cache[key] = v
        return v

    def _num(self, sheet, addr, stack) -> float:
        v = self._value(sheet, addr, stack)
        if v is None:
            return 0.0
        if isinstance(v, CellError):
            raise _EvalError(str(v))
        if isinstance(v, str):
            raise _EvalError("#VALUE!")
        return float(v)

    def _eval(self, n, stack) -> float:
        k = n[0]
        if k == "num":
            return n[1]
        if k == "ref":
            return self._num(n[1], n[2], stack)
        if k == "neg":
            return -self._eval(n[1], stack)
        if k == "bin":
            a, b = self._eval(n[2], stack), self._eval(n[3], stack)
            op = n[1]
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                return a / b
            return a**b
        if k == "call":
            name, args = n[1], n[2]
            if name == "ROUND":
                if len(args) != 2:
                    raise _EvalError("#VALUE!")
                return float(round(self._eval(args[0], stack), int(self._eval(args[1], stack))))
            if name == "ABS":
                if len(args) != 1:
                    raise _EvalError("#VALUE!")
                return abs(self._eval(args[0], stack))
            vals: list[float] = []
            for a in args:
                if a[0] == "range":
                    for addr in a[2]:
                        v = self._value(a[1], addr, stack)
                        if isinstance(v, CellError):
                            raise _EvalError(str(v))
                        if isinstance(v, (int, float)):
                            vals.append(float(v))
                else:
                    vals.append(self._eval(a, stack))
            if name == "SUM":
                return sum(vals)
            if not vals:
                raise _EvalError("#DIV/0!" if name == "AVERAGE" else "#VALUE!")
            if name == "AVERAGE":
                return sum(vals) / len(vals)
            return min(vals) if name == "MIN" else max(vals)
        raise _EvalError("#VALUE!")

    def evaluate_all(self) -> dict[tuple[str, str], object]:
        return {(s, a): self.value(s, a) for s, a, _ in self.cells()}

    # ---- interop ---------------------------------------------------------- #
    def to_xlsx(self, path: str) -> None:
        from openpyxl import Workbook as XL
        from openpyxl.styles import Font

        xl = XL()
        xl.remove(xl.active)
        for name, cells in self.sheets.items():
            ws = xl.create_sheet(name)
            for addr, content in cells.items():
                ws[addr] = content
                if split_addr(addr)[1] == 1:
                    ws[addr].font = Font(bold=True)
            ws.column_dimensions["A"].width = 34
        xl.save(path)

    @classmethod
    def from_xlsx(cls, path: str) -> "Workbook":
        from openpyxl import load_workbook

        xl = load_workbook(path)
        wb = cls()
        for ws in xl.worksheets:
            wb.add_sheet(ws.title)
            for row in ws.iter_rows():
                for c in row:
                    if c.value is not None:
                        wb.sheets[ws.title][c.coordinate] = c.value
        return wb
