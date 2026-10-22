"""Procedural builder for a five-sheet operating model + DCF.

Layout (column B = base year, C..G = forecast years 1..5):

    Inputs    A: label  B: value                     (assumptions, all hard-coded)
    Revenue   three segments grown off Inputs, total = SUM
    PnL       revenue link, COGS, gross profit, opex, EBITDA, D&A, EBIT, tax, NOPAT
    CashFlow  NOPAT + D&A - capex - change in NWC = unlevered FCF
    DCF       discount factors, PV of FCF, Gordon-growth terminal value, EV, equity, per share
    Notes     plain-English spec of every line, so every seeded error is detectable
              from information visible to the agent.

Formulas in each forecast row are produced by "fill right" from column C, exactly
like an analyst dragging a formula, so absolute/relative anchoring is real.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .workbook import Workbook, shift_col

BASE = "B"
FCST = ["C", "D", "E", "F", "G"]
YEARS = [BASE] + FCST

# Inputs row map: name -> (row, label, note-format)
INPUTS = {
    "seg1_base": (2, "{s1} revenue, base year ($k)", "money"),
    "seg2_base": (3, "{s2} revenue, base year ($k)", "money"),
    "seg3_base": (4, "{s3} revenue, base year ($k)", "money"),
    "seg1_g": (5, "{s1} annual growth", "pct"),
    "seg2_g": (6, "{s2} annual growth", "pct"),
    "seg3_g": (7, "{s3} annual growth", "pct"),
    "gm": (8, "Gross margin", "pct"),
    "opex": (9, "Operating expenses (% of revenue)", "pct"),
    "da": (10, "D&A (% of revenue)", "pct"),
    "tax": (11, "Tax rate (on EBIT)", "pct"),
    "capex": (12, "Capex (% of revenue)", "pct"),
    "nwc": (13, "Net working capital (% of revenue)", "pct"),
    "wacc": (14, "WACC", "pct"),
    "tg": (15, "Terminal growth rate", "pct"),
    "net_debt": (16, "Net debt ($k)", "money"),
    "shares": (17, "Diluted shares outstanding (k)", "num"),
}
PCT_INPUTS = [k for k, v in INPUTS.items() if v[2] == "pct"]


def inp(name: str, absolute: bool = True) -> str:
    row = INPUTS[name][0]
    return f"Inputs!$B${row}" if absolute else f"Inputs!B{row}"


COMPANIES = ["Halden Analytics", "Brightwater Foods", "Corvane Robotics", "Meridian Health Labs",
             "Tessaly Logistics", "Ostrava Energy", "Pinecrest Software", "Lumen Retail Group",
             "Arkwright Industrial", "Solace Clinics", "Northgate Media", "Quarry Lane Materials"]
SEGMENTS = [("Subscriptions", "Services", "Hardware"), ("Retail", "Wholesale", "Online"),
            ("Enterprise", "Mid-market", "SMB"), ("Domestic", "Export", "Licensing"),
            ("Core", "Adjacent", "New markets")]


@dataclass
class ModelSpec:
    company: str
    segments: tuple[str, str, str]
    inputs: dict[str, float]


def sample_spec(rng: random.Random) -> ModelSpec:
    while True:
        i = {
            "seg1_base": float(rng.randrange(4000, 40000, 50)),
            "seg2_base": float(rng.randrange(2000, 25000, 50)),
            "seg3_base": float(rng.randrange(500, 12000, 50)),
            "seg1_g": rng.randrange(2, 16) / 100,
            "seg2_g": rng.randrange(0, 12) / 100,
            "seg3_g": rng.randrange(5, 30) / 100,
            "gm": rng.randrange(35, 72) / 100,
            "opex": rng.randrange(10, 30) / 100,
            "da": rng.randrange(2, 7) / 100,
            "tax": rng.choice([0.19, 0.21, 0.25, 0.27, 0.30]),
            "capex": rng.randrange(3, 9) / 100,
            "nwc": rng.randrange(5, 16) / 100,
            "wacc": rng.randrange(70, 125, 5) / 1000,
            "tg": rng.randrange(10, 30, 5) / 1000,
            "shares": float(rng.randrange(5000, 80000, 100)),
        }
        rev0 = i["seg1_base"] + i["seg2_base"] + i["seg3_base"]
        i["net_debt"] = float(round(rev0 * rng.uniform(0.0, 0.6), -1))
        if i["gm"] - i["opex"] - i["da"] > 0.08 and i["wacc"] - i["tg"] > 0.04:
            return ModelSpec(rng.choice(COMPANIES), rng.choice(SEGMENTS), i)


def fill_right(wb: Workbook, sheet: str, row: int, template, cols=FCST) -> None:
    """template(col, prev_col) -> formula string; written into each column."""
    for c in cols:
        wb.set(sheet, f"{c}{row}", template(c, shift_col(c, -1)))


def build(spec: ModelSpec) -> Workbook:
    s1, s2, s3 = spec.segments
    wb = Workbook()
    for name in ("Inputs", "Revenue", "PnL", "CashFlow", "DCF", "Notes"):
        wb.add_sheet(name)

    # Inputs
    wb.set("Inputs", "A1", f"{spec.company} - assumptions")
    wb.set("Inputs", "B1", "Value")
    for key, (row, label, _) in INPUTS.items():
        wb.set("Inputs", f"A{row}", label.format(s1=s1, s2=s2, s3=s3))
        wb.set("Inputs", f"B{row}", spec.inputs[key])

    def header(sheet, title):
        wb.set(sheet, "A1", title)
        wb.set(sheet, "B1", "Base")
        for n, c in enumerate(FCST, 1):
            wb.set(sheet, f"{c}1", f"Year {n}")

    # Revenue
    header("Revenue", "Revenue build ($k)")
    for r, (seg, base, g) in enumerate([(s1, "seg1_base", "seg1_g"), (s2, "seg2_base", "seg2_g"),
                                        (s3, "seg3_base", "seg3_g")], start=2):
        wb.set("Revenue", f"A{r}", seg)
        wb.set("Revenue", f"B{r}", f"={inp(base)}")
        fill_right(wb, "Revenue", r, lambda c, p, r=r, g=g: f"={p}{r}*(1+{inp(g)})")
    wb.set("Revenue", "A5", "Total revenue")
    fill_right(wb, "Revenue", 5, lambda c, p: f"=SUM({c}2:{c}4)", cols=YEARS)

    # PnL
    header("PnL", "Income statement ($k)")
    rows = {
        2: ("Revenue", lambda c, p: f"=Revenue!{c}5"),
        3: ("COGS", lambda c, p: f"={c}2*(1-{inp('gm')})"),
        4: ("Gross profit", lambda c, p: f"={c}2-{c}3"),
        5: ("Operating expenses", lambda c, p: f"={c}2*{inp('opex')}"),
        6: ("EBITDA", lambda c, p: f"={c}4-{c}5"),
        7: ("D&A", lambda c, p: f"={c}2*{inp('da')}"),
        8: ("EBIT", lambda c, p: f"={c}6-{c}7"),
        9: ("Tax", lambda c, p: f"={c}8*{inp('tax')}"),
        10: ("NOPAT", lambda c, p: f"={c}8-{c}9"),
    }
    for r, (label, t) in rows.items():
        wb.set("PnL", f"A{r}", label)
        fill_right(wb, "PnL", r, t)
    wb.set("PnL", "B2", "=Revenue!B5")

    # CashFlow
    header("CashFlow", "Unlevered free cash flow ($k)")
    rows = {
        2: ("NOPAT", lambda c, p: f"=PnL!{c}10"),
        3: ("Add: D&A", lambda c, p: f"=PnL!{c}7"),
        4: ("Less: capex", lambda c, p: f"=PnL!{c}2*{inp('capex')}"),
        5: ("Net working capital", lambda c, p: f"=PnL!{c}2*{inp('nwc')}"),
        6: ("Less: increase in NWC", lambda c, p: f"={c}5-{p}5"),
        7: ("Unlevered FCF", lambda c, p: f"={c}2+{c}3-{c}4-{c}6"),
    }
    for r, (label, t) in rows.items():
        wb.set("CashFlow", f"A{r}", label)
        fill_right(wb, "CashFlow", r, t)
    wb.set("CashFlow", "B5", f"=PnL!B2*{inp('nwc')}")

    # DCF
    header("DCF", "Discounted cash flow")
    wb.set("DCF", "A2", "Period")
    for n, c in enumerate(FCST, 1):
        wb.set("DCF", f"{c}2", float(n))
    wb.set("DCF", "A3", "Discount factor")
    fill_right(wb, "DCF", 3, lambda c, p: f"=1/(1+{inp('wacc')})^{c}2")
    wb.set("DCF", "A4", "PV of FCF")
    fill_right(wb, "DCF", 4, lambda c, p: f"=CashFlow!{c}7*{c}3")
    labels = {6: "Sum of PV of FCF", 7: "Terminal value (Gordon growth, at Year 5)",
              8: "PV of terminal value", 9: "Enterprise value", 10: "Equity value",
              11: "Equity value per share ($)"}
    for r, label in labels.items():
        wb.set("DCF", f"A{r}", label)
    wb.set("DCF", "B6", "=SUM(C4:G4)")
    wb.set("DCF", "B7", f"=CashFlow!G7*(1+{inp('tg')})/({inp('wacc')}-{inp('tg')})")
    wb.set("DCF", "B8", "=B7*G3")
    wb.set("DCF", "B9", "=B6+B8")
    wb.set("DCF", "B10", f"=B9-{inp('net_debt')}")
    wb.set("DCF", "B11", f"=B10/{inp('shares')}")

    _write_notes(wb, spec)
    return wb


def _fmt(key: str, v: float) -> str:
    kind = INPUTS[key][2]
    if kind == "pct":
        return f"{v * 100:g}%"
    return f"{v:,.0f}"


def _write_notes(wb: Workbook, spec: ModelSpec) -> None:
    s1, s2, s3 = spec.segments
    i = spec.inputs
    lines = [
        f"MODEL SPECIFICATION - {spec.company}",
        "All money in $ thousands. Column B is the base year; C..G are forecast years 1-5.",
        "Every assumption lives on Inputs (one value per line, percentages stored as decimals, e.g. 25% = 0.25).",
        "No other sheet should contain hard-coded numbers except DCF row 2 (period numbers 1..5).",
        "",
        "ASSUMPTIONS (signed off by the deal team):",
    ]
    for key, (row, label, _) in INPUTS.items():
        lines.append(f"  Inputs!B{row}  {label.format(s1=s1, s2=s2, s3=s3)} = {_fmt(key, i[key])}")
    lines += [
        "",
        "REVENUE: base year = Inputs base revenue. Each forecast year = prior year x (1 + that segment's growth).",
        "  Total revenue = sum of the three segments (rows 2-4) in every column.",
        "PnL: Revenue links to Revenue total (row 5) of the same year.",
        "  COGS = revenue x (1 - gross margin). Gross profit = revenue - COGS.",
        "  Opex = revenue x opex %. EBITDA = gross profit - opex.",
        "  D&A = revenue x D&A %. EBIT = EBITDA - D&A. Tax = EBIT x tax rate. NOPAT = EBIT - tax.",
        "CASHFLOW: NOPAT and D&A link to PnL of the same year. Capex = same-year revenue x capex %.",
        "  NWC = same-year revenue x NWC % (base year included). Increase in NWC = NWC this year - NWC prior year.",
        "  Unlevered FCF = NOPAT + D&A - capex - increase in NWC.",
        "DCF: end-of-year discounting. Discount factor = 1 / (1 + WACC) ^ period.",
        "  PV of FCF = FCF x discount factor. Sum of PV = sum over years 1-5.",
        "  Terminal value = Year-5 FCF x (1 + g) / (WACC - g), valued at end of Year 5,",
        "  then discounted with the Year-5 discount factor. EV = sum of PV + PV of terminal value.",
        "  Equity value = EV - net debt. Per share = equity value / diluted shares.",
    ]
    for n, line in enumerate(lines, start=1):
        wb.set("Notes", f"A{n}", line)


# Cells whose values define "the answer" a reviewer would check.
KEY_OUTPUTS: list[tuple[str, str]] = (
    [("Revenue", f"{c}5") for c in FCST]
    + [("PnL", f"{c}6") for c in FCST]
    + [("PnL", f"{c}10") for c in FCST]
    + [("CashFlow", f"{c}7") for c in FCST]
    + [("DCF", "B9"), ("DCF", "B10"), ("DCF", "B11")]
)

INPUT_CELLS: dict[str, str] = {k: f"B{v[0]}" for k, v in INPUTS.items()}
