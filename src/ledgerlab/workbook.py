"""In-memory workbook with a small, deterministic formula engine.

The engine deliberately supports only the grammar the task generator emits
(cell refs, cross-sheet refs, ranges inside functions, + - * / ^, unary minus,
parentheses, SUM/AVERAGE/MIN/MAX/ROUND/ABS). Keeping it small makes rewards
reproducible bit-for-bit and removes any dependency on Excel or LibreOffice.
"""

from __future__ import annotations

import datetime
import fnmatch
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

Content = float | int | str | None  # str starting with "=" is a formula
_EXCEL_EPOCH = datetime.datetime(1899, 12, 30)


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
  | (?P<ref>(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_.]*)!)?\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)
  | (?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<bool>TRUE|FALSE)
  | (?P<cmp><=|>=|<>|[<>=])
  | (?P<op>[-+*/^(),%&])
    """,
    re.X,
)

FUNCS = {"SUM", "AVERAGE", "MIN", "MAX", "ROUND", "ABS", "IF", "IFERROR", "AND", "OR", "NOT",
         "SUMPRODUCT", "NPV", "SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS", "AVERAGEIF", "AVERAGEIFS",
         "TODAY", "DATE", "YEAR", "MONTH", "DAY", "EOMONTH", "EDATE", "TEXT", "CONCAT", "CONCATENATE"}


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
        node = self.concat()
        while self.peek()[0] == "cmp":
            op = self.take()[1]
            node = ("cmp", op, node, self.concat())
        return node

    def concat(self):
        node = self.additive()
        while self.peek()[1] == "&":
            self.take()
            node = ("cat", node, self.additive())
        return node

    def additive(self):
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

    def postfix(self, node):
        while self.peek()[1] == "%":
            self.take()
            node = ("bin", "/", node, ("num", 100.0))
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
        return self.postfix(self._primary())

    def _primary(self):
        kind, val = self.peek()
        if kind == "num":
            self.take()
            return ("num", float(val))
        if kind == "str":
            self.take()
            return ("str", val[1:-1].replace('""', '"'))
        if kind == "bool":
            self.take()
            return ("num", 1.0 if val == "TRUE" else 0.0)
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
            sheet, text = text.rsplit("!", 1)
            sheet = sheet.strip("'")
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
        elif k == "cat":
            walk(n[1])
            walk(n[2])
        elif k in ("bin", "cmp"):
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


# --------------------------------------------------------------------------- #
# Scalar helpers (Excel semantics, simplified)
# --------------------------------------------------------------------------- #

TODAY_SERIAL = 46317  # TODAY() is frozen (2026-10-22) so rewards stay deterministic


def _serial(y: int, m: int, d: int) -> float:
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return float((datetime.datetime(y, m, d) - _EXCEL_EPOCH).days)


def _from_serial(v: float) -> datetime.datetime:
    return _EXCEL_EPOCH + datetime.timedelta(days=int(v))


def _days_in(y: int, m: int) -> int:
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    nxt = datetime.datetime(y + (m == 12), m % 12 + 1, 1)
    return (nxt - datetime.datetime(y, m, 1)).days


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _compare(a, b, op: str) -> bool:
    # empty cells compare as 0 against numbers and "" against text
    if a is None:
        a = "" if isinstance(b, str) else 0.0
    if b is None:
        b = "" if isinstance(a, str) else 0.0
    if isinstance(a, str) and isinstance(b, str):
        a, b = a.lower(), b.lower()
    elif isinstance(a, str) or isinstance(b, str):  # Excel: numbers sort before text
        if op in ("=", "<>"):
            return op == "<>"
        a, b = (1, 0) if isinstance(a, str) else (0, 1)
    return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]


_CRIT_RE = re.compile(r"^(<=|>=|<>|<|>|=)?(.*)$", re.S)


def _criteria(value, crit) -> bool:
    if not isinstance(crit, str):
        return value is not None and _compare(value, crit, "=")
    op, rest = _CRIT_RE.match(crit).groups()
    op = op or "="
    try:
        target = float(rest)
    except ValueError:
        target = rest
    if target == "" and op in ("=", "<>"):
        empty = value is None or value == ""
        return empty if op == "=" else not empty
    if isinstance(target, str) and op in ("=", "<>") and any(ch in target for ch in "*?"):
        hit = isinstance(value, str) and fnmatch.fnmatchcase(value.lower(), target.lower())
        return hit if op == "=" else not hit
    if isinstance(target, float) and not isinstance(value, (int, float)):
        return op == "<>"
    return _compare(value, target, op)


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
                v = self._x(parse_formula(content, sheet), stack)
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    v = CellError("#NUM!")
            except _EvalError as e:
                v = CellError(e.code)
            except ZeroDivisionError:
                v = CellError("#DIV/0!")
            except OverflowError:
                v = CellError("#NUM!")
            except (TypeError, ValueError):
                v = CellError("#VALUE!")
            stack.discard(key)
        self._cache[key] = v
        return v

    # Values are float | str | None (empty). _x returns a scalar; _eval coerces to number.
    def _cell(self, sheet, addr, stack):
        v = self._value(sheet, addr, stack)
        if isinstance(v, CellError):
            raise _EvalError(str(v))
        return v

    @staticmethod
    def _to_num(v) -> float:
        if v is None:
            return 0.0
        if isinstance(v, str):
            raise _EvalError("#VALUE!")
        return float(v)

    def _eval(self, n, stack) -> float:
        return self._to_num(self._x(n, stack))

    def _x(self, n, stack):
        k = n[0]
        if k in ("num", "str"):
            return n[1]
        if k == "ref":
            return self._cell(n[1], n[2], stack)
        if k == "neg":
            return -self._eval(n[1], stack)
        if k == "cat":
            return _text(self._x(n[1], stack)) + _text(self._x(n[2], stack))
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
        if k == "cmp":
            return float(_compare(self._x(n[2], stack), self._x(n[3], stack), n[1]))
        if k == "call":
            return self._call(n[1], n[2], stack)
        raise _EvalError("#VALUE!")

    def _truthy(self, n, stack) -> bool:
        v = self._x(n, stack)
        if isinstance(v, str):
            if v.upper() in ("TRUE", "FALSE"):
                return v.upper() == "TRUE"
            raise _EvalError("#VALUE!")
        return bool(v)

    def _call(self, name, args, stack):
        if name == "IF":
            if len(args) not in (2, 3):
                raise _EvalError("#VALUE!")
            if self._truthy(args[0], stack):
                return self._x(args[1], stack)
            return self._x(args[2], stack) if len(args) == 3 else 0.0
        if name == "IFERROR":
            if len(args) != 2:
                raise _EvalError("#VALUE!")
            try:
                return self._x(args[0], stack)
            except (_EvalError, ZeroDivisionError, OverflowError):
                return self._x(args[1], stack)
        if name == "NOT":
            return float(not self._truthy(args[0], stack))
        if name in ("AND", "OR"):
            vals = [bool(v) for a in args for v in self._range_vals(a, stack)]
            return float(all(vals) if name == "AND" else any(vals))
        if name == "SUMPRODUCT":
            cols = [self._range_vals(a, stack) for a in args]
            if len({len(c) for c in cols}) != 1:
                raise _EvalError("#VALUE!")
            total = 0.0
            for row in zip(*cols):
                p = 1.0
                for x in row:
                    p *= x
                total += p
            return total
        if name == "NPV":
            rate = self._eval(args[0], stack)
            flows = [v for a in args[1:] for v in self._range_vals(a, stack)]
            return sum(v / (1 + rate) ** (i + 1) for i, v in enumerate(flows))
        if name == "ROUND":
            if len(args) != 2:
                raise _EvalError("#VALUE!")
            return float(round(self._eval(args[0], stack), int(self._eval(args[1], stack))))
        if name == "ABS":
            return abs(self._eval(args[0], stack))
        if name in ("SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS", "AVERAGEIF", "AVERAGEIFS"):
            return self._conditional(name, args, stack)
        if name == "TODAY":
            return float(TODAY_SERIAL)
        if name == "DATE":
            y, m, d = (int(self._eval(a, stack)) for a in args)
            return _serial(y, m, 1) + d - 1
        if name in ("YEAR", "MONTH", "DAY"):
            dt = _from_serial(self._eval(args[0], stack))
            return float({"YEAR": dt.year, "MONTH": dt.month, "DAY": dt.day}[name])
        if name in ("EOMONTH", "EDATE"):
            dt = _from_serial(self._eval(args[0], stack))
            months = dt.year * 12 + dt.month - 1 + int(self._eval(args[1], stack))
            y, m = divmod(months, 12)
            if name == "EDATE":
                return _serial(y, m + 1, min(dt.day, _days_in(y, m + 1)))
            return _serial(y, m + 1, _days_in(y, m + 1))
        if name == "TEXT":
            return _text(self._x(args[0], stack))
        if name in ("CONCAT", "CONCATENATE"):
            return "".join(_text(self._x(a, stack)) for a in args)
        vals: list[float] = []
        for a in args:
            if a[0] == "range":
                for addr in a[2]:
                    v = self._cell(a[1], addr, stack)
                    if isinstance(v, (int, float)):
                        vals.append(float(v))
            else:
                vals.append(self._eval(a, stack))
        if name == "SUM":
            return sum(vals)
        if not vals:
            if name in ("MIN", "MAX"):
                return 0.0
            raise _EvalError("#DIV/0!")
        if name == "AVERAGE":
            return sum(vals) / len(vals)
        return min(vals) if name == "MIN" else max(vals)

    def _cells_of(self, a, stack) -> list:
        if a[0] != "range":
            return [self._x(a, stack)]
        return [self._cell(a[1], addr, stack) for addr in a[2]]

    def _conditional(self, name, args, stack):
        if name in ("SUMIF", "AVERAGEIF"):
            rng, crit = self._cells_of(args[0], stack), self._x(args[1], stack)
            target = self._cells_of(args[2], stack) if len(args) == 3 else rng
            pairs = [(rng, crit)]
        elif name == "COUNTIF":
            target, pairs = None, [(self._cells_of(args[0], stack), self._x(args[1], stack))]
        elif name == "COUNTIFS":
            target = None
            pairs = [(self._cells_of(args[i], stack), self._x(args[i + 1], stack))
                     for i in range(0, len(args), 2)]
        else:  # SUMIFS / AVERAGEIFS: target range first
            target = self._cells_of(args[0], stack)
            pairs = [(self._cells_of(args[i], stack), self._x(args[i + 1], stack))
                     for i in range(1, len(args), 2)]
        n = len(pairs[0][0])
        if any(len(r) != n for r, _ in pairs) or (target is not None and len(target) != n):
            raise _EvalError("#VALUE!")
        hits = [i for i in range(n) if all(_criteria(r[i], c) for r, c in pairs)]
        if target is None:
            return float(len(hits))
        nums = [float(target[i]) for i in hits if isinstance(target[i], (int, float))]
        if name.startswith("AVERAGE"):
            if not nums:
                raise _EvalError("#DIV/0!")
            return sum(nums) / len(nums)
        return sum(nums)

    def _range_vals(self, a, stack) -> list[float]:
        if a[0] != "range":
            return [self._eval(a, stack)]
        return [float(v) if isinstance(v, (int, float)) else 0.0 for v in self._cells_of(a, stack)]

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
                    v = c.value
                    if v is None:
                        continue
                    if isinstance(v, bool):
                        v = float(v)
                    elif isinstance(v, (datetime.datetime, datetime.date)):
                        v = float((datetime.datetime(v.year, v.month, v.day) - _EXCEL_EPOCH).days)
                    elif not isinstance(v, (int, float, str)):
                        v = getattr(v, "text", None) or str(v)  # array formulas etc.
                    wb.sheets[ws.title][c.coordinate] = v
        return wb
