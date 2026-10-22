"""Task generation with quality control.

A task is accepted only if:
  1. every injected bug is cell-disjoint from the others,
  2. every bug, applied alone, moves at least one key output beyond tolerance,
  3. no bug is masked: with all bugs present, fixing any single one still moves
     a key output (so partial credit always tracks real progress),
  4. the corrupted model evaluates without spreadsheet errors (#DIV/0! etc.),
  5. every bug is inferable from visible information (by construction: the
     Notes sheet states each assumption and every line's logic).
Rejected samples are re-drawn from the same seeded RNG, so tasks stay reproducible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .bugs import TIERS, Bug
from .model import KEY_OUTPUTS, ModelSpec, build, sample_spec
from .workbook import CellError, Workbook

DETECT_TOL = 1e-3  # relative change a bug must cause in some key output


def outputs(wb: Workbook, keys=KEY_OUTPUTS) -> list:
    return [wb.value(s, a) for s, a in keys]


def outputs_differ(a: list, b: list, tol: float = DETECT_TOL) -> bool:
    for x, y in zip(a, b):
        if isinstance(x, (CellError, str)) or isinstance(y, (CellError, str)):
            if x != y:
                return True
            continue
        x, y = float(x or 0), float(y or 0)
        if abs(x - y) > tol * max(1.0, abs(y)):
            return True
    return False


def apply_bugs(golden: Workbook, bugs: list[Bug]) -> Workbook:
    wb = golden.copy()
    for b in bugs:
        for (s, a), content in b.cells.items():
            wb.set(s, a, content)
    return wb


@dataclass
class Task:
    seed: int
    difficulty: str
    spec: ModelSpec | None
    golden: Workbook
    bugs: list[Bug]
    workbook: Workbook = field(repr=False)
    # Set for bring-your-own workbooks (byo.py); generated tasks use the model template.
    key_outputs: list[tuple[str, str]] = field(default_factory=lambda: list(KEY_OUTPUTS))
    input_cells: list[tuple[str, str]] | None = None
    name: str = ""
    brief: str = ""

    @property
    def bug_cells(self) -> set[tuple[str, str]]:
        return {c for b in self.bugs for c in b.cells}


def _accept(golden: Workbook, bugs: list[Bug], keys=KEY_OUTPUTS) -> bool:
    cells = [c for b in bugs for c in b.cells]
    if len(cells) != len(set(cells)):
        return False
    base = outputs(golden, keys)
    buggy = apply_bugs(golden, bugs)
    bo = outputs(buggy, keys)
    if any(isinstance(v, CellError) for v in bo):
        return False
    for i, b in enumerate(bugs):
        if not outputs_differ(outputs(apply_bugs(golden, [b]), keys), base):
            return False
        rest = bugs[:i] + bugs[i + 1:]
        if not outputs_differ(outputs(apply_bugs(golden, rest), keys), bo):
            return False
    return True


def generate(seed: int, difficulty: str = "medium", max_tries: int = 500) -> Task:
    if difficulty not in TIERS:
        raise ValueError(f"difficulty must be one of {list(TIERS)}")
    pool, n_bugs = TIERS[difficulty]
    rng = random.Random(f"ledgerlab:{difficulty}:{seed}")
    for _ in range(max_tries):
        spec = sample_spec(rng)
        golden = build(spec, expert=difficulty == "expert")
        if difficulty == "expert" and not golden.value("DCF", "B11") > 0:
            continue  # decoys can push marginal companies negative; keep valuations sane
        kinds = rng.sample(pool, n_bugs)
        bugs = [k(golden, rng) for k in kinds]
        if _accept(golden, bugs):
            return Task(seed, difficulty, spec, golden, bugs, apply_bugs(golden, bugs), name=spec.company)
    raise RuntimeError(f"could not generate an acceptable task for seed={seed}")
