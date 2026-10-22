"""Bring your own workbook: turn any correct .xlsx model into verified repair tasks.

    from ledgerlab.byo import analyze, make_task
    report = analyze("model.xlsx")          # engine coverage, detected inputs/outputs
    task = make_task("model.xlsx", n_bugs=3, seed=0)

The workbook you supply is treated as the golden model. Generic, formula-level
mutators seed errors; the same task QC (detectable, unmasked, error-free) and
the same metamorphic verifier apply. Inputs are numeric constants that formulas
read; outputs are formula cells nothing else reads. Both can be overridden.
"""

from __future__ import annotations

import pathlib
import random
from dataclasses import dataclass, field

from .bugs import Bug
from .task import Task, _accept, apply_bugs, outputs
from .workbook import (CellError, Workbook, _EvalError, _tokenize, col_to_idx, idx_to_col,
                       is_formula, references, split_addr)

Cell = tuple[str, str]


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #


@dataclass
class Report:
    sheets: int
    formulas: int
    evaluated_ok: int
    errors: dict[str, list[str]] = field(default_factory=dict)  # error code -> cells
    inputs: list[Cell] = field(default_factory=list)
    outputs: list[Cell] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.evaluated_ok / max(1, self.formulas)

    def __str__(self) -> str:
        lines = [f"sheets: {self.sheets}   formulas: {self.formulas}   "
                 f"engine coverage: {self.coverage:.1%}",
                 f"detected inputs: {len(self.inputs)}   detected outputs: {len(self.outputs)}"]
        for code, cells in self.errors.items():
            lines.append(f"  {code}: {len(cells)} cell(s), e.g. {', '.join(cells[:5])}")
        return "\n".join(lines)


def _load(src) -> Workbook:
    return src if isinstance(src, Workbook) else Workbook.from_xlsx(str(src))


def _numeric(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def detect_io(wb: Workbook) -> tuple[list[Cell], list[Cell]]:
    read_by: dict[Cell, int] = {}
    for s, a, c in wb.cells():
        if is_formula(c):
            for ref in references(c, s):
                read_by[ref] = read_by.get(ref, 0) + 1
    inputs = sorted(c for c in read_by if _numeric(wb.get(*c)))
    outs = sorted((s, a) for s, a, c in wb.cells()
                  if is_formula(c) and (s, a) not in read_by and _numeric(wb.value(s, a)))
    return inputs, outs


def analyze(src) -> Report:
    wb = _load(src)
    formulas = [(s, a) for s, a, c in wb.cells() if is_formula(c)]
    errors: dict[str, list[str]] = {}
    ok = 0
    for s, a in formulas:
        v = wb.value(s, a)
        if isinstance(v, CellError):
            errors.setdefault(str(v), []).append(f"{s}!{a}")
        else:
            ok += 1
    ins, outs = detect_io(wb)
    return Report(len(wb.sheets), len(formulas), ok, errors, ins, outs)


# --------------------------------------------------------------------------- #
# Generic mutators: (formula tokens) -> mutated formula | None
# --------------------------------------------------------------------------- #


def _join(tokens) -> str:
    return "=" + "".join(t for _, t in tokens)


def _binary_ops(tokens, ops):
    return [i for i, (k, t) in enumerate(tokens)
            if t in ops and i > 0 and (tokens[i - 1][0] in ("ref", "num") or tokens[i - 1][1] == ")")]


def _shift_ref(text: str, dcol: int, drow: int) -> str | None:
    if ":" in text:
        return None
    prefix, addr = (text.rsplit("!", 1)[0] + "!", text.rsplit("!", 1)[1]) if "!" in text else ("", text)
    col, row = split_addr(addr)
    ci, r = col_to_idx(col) + dcol, row + drow
    if ci < 1 or r < 1:
        return None
    return f"{prefix}{idx_to_col(ci)}{r}"


def m_sign_flip(tokens, rng):
    idx = _binary_ops(tokens, ("+", "-"))
    if not idx:
        return None
    i = rng.choice(idx)
    t = list(tokens)
    t[i] = ("op", "+" if t[i][1] == "-" else "-")
    return _join(t), "sign flipped (+/-)"


def m_op_swap(tokens, rng):
    idx = _binary_ops(tokens, ("*", "/"))
    if not idx:
        return None
    i = rng.choice(idx)
    t = list(tokens)
    t[i] = ("op", "/" if t[i][1] == "*" else "*")
    return _join(t), "multiply/divide swapped"


def m_prior_period(tokens, rng):
    idx = [i for i, (k, x) in enumerate(tokens) if k == "ref" and ":" not in x and "$" not in x]
    if not idx:
        return None
    i = rng.choice(idx)
    new = _shift_ref(tokens[i][1], -1, 0)
    if not new:
        return None
    t = list(tokens)
    t[i] = ("ref", new)
    return _join(t), "reference points one column left (prior period)"


def m_wrong_row(tokens, rng):
    idx = [i for i, (k, x) in enumerate(tokens) if k == "ref" and ":" not in x]
    if not idx:
        return None
    i = rng.choice(idx)
    new = _shift_ref(tokens[i][1].replace("$", ""), 0, rng.choice([-1, 1]))
    if not new:
        return None
    t = list(tokens)
    t[i] = ("ref", new)
    return _join(t), "reference points to an adjacent row (wrong line item)"


def m_range_short(tokens, rng):
    idx = [i for i, (k, x) in enumerate(tokens) if k == "ref" and ":" in x]
    if not idx:
        return None
    i = rng.choice(idx)
    text = tokens[i][1]
    prefix = text.rsplit("!", 1)[0] + "!" if "!" in text else ""
    a, b = text.rsplit("!", 1)[-1].replace("$", "").split(":")
    (c1, r1), (c2, r2) = split_addr(a), split_addr(b)
    if r2 > r1:
        b = f"{c2}{r2 - 1}"
    elif col_to_idx(c2) > col_to_idx(c1):
        b = f"{idx_to_col(col_to_idx(c2) - 1)}{r2}"
    else:
        return None
    t = list(tokens)
    t[i] = ("ref", f"{prefix}{a}:{b}")
    return _join(t), "range stops one cell short"


FORMULA_MUTATORS = [m_sign_flip, m_op_swap, m_prior_period, m_wrong_row, m_range_short]


def _mutate(wb: Workbook, cell: Cell, rng: random.Random, inputs: list[Cell]) -> Bug | None:
    s, a = cell
    c = wb.get(s, a)
    if cell in inputs:
        v = float(c)
        if 0 < abs(v) < 1:
            return Bug("byo_unit_mismatch", {cell: round(v * 100, 9)},
                       f"{s}!{a} percentage entered as a whole number")
        return None
    if rng.random() < 0.2:
        v = wb.value(s, a)
        if _numeric(v) and v != 0:
            drift = rng.choice([-1, 1]) * rng.uniform(0.04, 0.15)
            return Bug("byo_hardcoded_plug", {cell: round(v * (1 + drift), 2)},
                       f"{s}!{a} formula overwritten with a stale number")
    try:
        tokens = _tokenize(c.lstrip("="))
    except _EvalError:
        return None
    for m in rng.sample(FORMULA_MUTATORS, len(FORMULA_MUTATORS)):
        out = m(tokens, rng)
        if out:
            formula, desc = out
            return Bug(f"byo_{m.__name__[2:]}", {cell: formula}, f"{s}!{a} {desc}")
    return None


def _near_outputs(wb: Workbook, keys: list[Cell], depth: int = 4, cap: int = 400) -> list[Cell]:
    """Formula cells within `depth` precedent hops of the key outputs.

    On large workbooks most formulas are row-level detail whose errors barely move
    the outputs; sampling near the outputs keeps generation fast and errors material.
    """
    seen: set[Cell] = set()
    frontier = list(keys)
    for _ in range(depth):
        nxt = []
        for s, a in frontier:
            c = wb.get(s, a)
            if (s, a) in seen or not is_formula(c):
                continue
            seen.add((s, a))
            refs = references(c, s)
            nxt.extend(refs if len(refs) <= 50 else refs[:50])
        frontier = nxt
        if len(seen) >= cap:
            break
    return sorted(seen)


def make_task(src, n_bugs: int = 3, seed: int = 0, key_outputs: list[Cell] | None = None,
              input_cells: list[Cell] | None = None, brief: str | None = None,
              max_tries: int = 2000) -> Task:
    wb = _load(src)
    name = pathlib.Path(str(src)).stem if not isinstance(src, Workbook) else "workbook"
    ins, outs = detect_io(wb)
    inputs = input_cells or ins
    keys = key_outputs or outs
    bad = [f"{s}!{a}" for s, a in keys if not _numeric(wb.value(s, a))]
    if bad or not keys:
        raise ValueError(f"key outputs must evaluate to numbers; problems: {bad or 'none detected'}")
    rng = random.Random(f"ledgerlab:byo:{name}:{seed}")
    formula_cells = _near_outputs(wb, keys)
    pool = formula_cells + [c for c in inputs if 0 < abs(float(wb.get(*c))) < 1]
    for _ in range(max_tries):
        sites = rng.sample(pool, min(n_bugs, len(pool)))
        bugs = [b for b in (_mutate(wb, c, rng, inputs) for c in sites) if b]
        if len(bugs) == n_bugs and _accept(wb, bugs, keys):
            text = brief or (
                f"Review the workbook '{name}'. It was a correct model until {n_bugs} error(s) were "
                "introduced: broken references, flipped signs, swapped operators, short ranges, "
                "pasted-over formulas or mis-entered percentages. Find and fix them in place. "
                "Do not change correct cells. Hard-coded numbers over formulas fail grading.")
            return Task(seed, "byo", None, wb, bugs, apply_bugs(wb, bugs), key_outputs=list(keys),
                        input_cells=list(inputs), name=name, brief=text)
    raise RuntimeError("could not seed detectable, unmasked errors; supply key_outputs or fewer bugs")
