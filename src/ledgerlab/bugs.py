"""Seeded analyst errors.

Each injector picks a site in the golden model and returns a Bug: the cells it
corrupts (with the corrupted content) plus a human description. The golden
content of those cells is the reference fix, but any edit that is
*behaviourally equivalent* under input perturbation also counts (see rewards.py).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .model import FCST, INPUTS, PCT_INPUTS, YEARS
from .workbook import Workbook, col_to_idx, shift_col

Cell = tuple[str, str]


@dataclass(frozen=True)
class Bug:
    kind: str
    cells: dict[Cell, object]  # cell -> corrupted content
    description: str


def _pick(rng, xs):
    return xs[rng.randrange(len(xs))]


# --------------------------------------------------------------------------- #
# Injectors: (golden, rng) -> Bug | None
# --------------------------------------------------------------------------- #


def hardcoded_plug(g: Workbook, rng: random.Random):
    sites = ([("Revenue", f"{c}{r}") for r in (2, 3, 4) for c in FCST]
             + [("PnL", f"{c}{r}") for r in (3, 5, 7, 9) for c in FCST]
             + [("CashFlow", f"{c}4") for c in FCST])
    s, a = _pick(rng, sites)
    v = g.value(s, a)
    drift = rng.choice([-1, 1]) * rng.uniform(0.04, 0.15)
    return Bug("hardcoded_plug", {(s, a): float(round(v * (1 + drift)))},
               f"{s}!{a} formula overwritten with a stale hard-coded number")


def range_omission(g: Workbook, rng):
    if rng.random() < 0.75:
        c = _pick(rng, YEARS)
        return Bug("range_omission", {("Revenue", f"{c}5"): f"=SUM({c}2:{c}3)"},
                   f"Revenue!{c}5 total misses the third segment (SUM range too short)")
    return Bug("range_omission", {("DCF", "B6"): "=SUM(C4:F4)"},
               "DCF!B6 sum of PV omits Year 5")


def sign_flip(g: Workbook, rng):
    c = _pick(rng, FCST)
    site = _pick(rng, [("PnL", 4), ("PnL", 6), ("PnL", 8), ("PnL", 10), ("CashFlow", 7)])
    s, r = site
    f = g.get(s, f"{c}{r}")
    if s == "CashFlow":
        bad = f.replace(f"-{c}4", f"+{c}4")
        desc = "capex added instead of subtracted"
    else:
        bad = f.replace("-", "+", 1)
        desc = "subtraction written as addition"
    return Bug("sign_flip", {(s, f"{c}{r}"): bad}, f"{s}!{c}{r}: {desc}")


def crosslink_wrong(g: Workbook, rng):
    c = _pick(rng, FCST)
    if rng.random() < 0.5:
        row = rng.choice([2, 4])
        return Bug("crosslink_wrong", {("PnL", f"{c}2"): f"=Revenue!{c}{row}"},
                   f"PnL!{c}2 links to a single segment instead of total revenue")
    return Bug("crosslink_wrong", {("CashFlow", f"{c}3"): f"=PnL!{c}6"},
               f"CashFlow!{c}3 adds back EBITDA instead of D&A")


def unit_mismatch(g: Workbook, rng):
    key = _pick(rng, ["gm", "opex", "da", "tax", "capex", "nwc"])
    addr = f"B{INPUTS[key][0]}"
    v = g.get("Inputs", addr)
    return Bug("unit_mismatch", {("Inputs", addr): round(v * 100, 6)},
               f"Inputs!{addr} entered as a whole-number percent ({v * 100:g} instead of {v:g})")


def anchor_slip(g: Workbook, rng):
    """Analyst forgot the $ anchors on an Inputs reference, then filled right."""
    sites = [("Revenue", 2, "seg1_g"), ("Revenue", 3, "seg2_g"), ("Revenue", 4, "seg3_g"),
             ("PnL", 3, "gm"), ("PnL", 5, "opex"), ("PnL", 7, "da"), ("PnL", 9, "tax"),
             ("CashFlow", 4, "capex"), ("CashFlow", 5, "nwc")]
    s, r, key = _pick(rng, sites)
    irow = INPUTS[key][0]
    cells = {}
    for c in FCST[1:]:  # column C still resolves to Inputs!B (correct), drift starts at D
        f = g.get(s, f"{c}{r}")
        drift = shift_col("B", col_to_idx(c) - col_to_idx("C"))
        cells[(s, f"{c}{r}")] = f.replace(f"Inputs!$B${irow}", f"Inputs!{drift}{irow}")
    return Bug("anchor_slip", cells,
               f"{s} row {r}: Inputs!B{irow} reference not anchored, drifts right across D:G")


def period_mislink(g: Workbook, rng):
    c = _pick(rng, FCST)
    p = shift_col(c, -1)
    s, f = _pick(rng, [("CashFlow", lambda: (f"{c}2", f"=PnL!{p}10")),
                       ("CashFlow", lambda: (f"{c}3", f"=PnL!{p}7")),
                       ("PnL", lambda: (f"{c}2", f"=Revenue!{p}5"))])
    addr, bad = f()
    return Bug("period_mislink", {(s, addr): bad}, f"{s}!{addr} links to the prior year")


def growth_chain_break(g: Workbook, rng):
    c = _pick(rng, FCST[1:])
    r = rng.choice([2, 3, 4])
    pp = shift_col(c, -2)
    f = g.get("Revenue", f"{c}{r}")
    bad = f"={pp}{r}" + f[f.index("*"):]
    return Bug("growth_chain_break", {("Revenue", f"{c}{r}"): bad},
               f"Revenue!{c}{r} grows off two years back instead of the prior year")


def nwc_level_not_change(g: Workbook, rng):
    c = _pick(rng, FCST)
    return Bug("nwc_level_not_change", {("CashFlow", f"{c}7"): f"={c}2+{c}3-{c}4-{c}5"},
               f"CashFlow!{c}7 subtracts the NWC balance instead of the increase in NWC")


def tax_on_ebitda(g: Workbook, rng):
    c = _pick(rng, FCST)
    return Bug("tax_on_ebitda", {("PnL", f"{c}9"): f"={c}6*Inputs!$B${INPUTS['tax'][0]}"},
               f"PnL!{c}9 taxes EBITDA instead of EBIT")


def discount_off_by_one(g: Workbook, rng):
    w = INPUTS["wacc"][0]
    return Bug("discount_off_by_one",
               {("DCF", f"{c}3"): f"=1/(1+Inputs!$B${w})^({c}2-1)" for c in FCST},
               "DCF row 3 discounts one period too few (exponent = period - 1)")


def terminal_wrong_year(g: Workbook, rng):
    f = g.get("DCF", "B7")
    return Bug("terminal_wrong_year", {("DCF", "B7"): f.replace("CashFlow!G7", "CashFlow!F7")},
               "DCF!B7 terminal value built off Year-4 FCF")


def terminal_not_discounted(g: Workbook, rng):
    return Bug("terminal_not_discounted", {("DCF", "B8"): "=B7"},
               "DCF!B8 terminal value not discounted back")


EASY = [hardcoded_plug, range_omission, sign_flip, crosslink_wrong, unit_mismatch]
MEDIUM = EASY + [anchor_slip, period_mislink, growth_chain_break, nwc_level_not_change, tax_on_ebitda]
HARD = MEDIUM + [discount_off_by_one, terminal_wrong_year, terminal_not_discounted]

TIERS = {
    "easy": (EASY, 1),
    "medium": (MEDIUM, 3),
    "hard": (HARD, 5),
}

ALL_KINDS = [f.__name__ for f in HARD]
