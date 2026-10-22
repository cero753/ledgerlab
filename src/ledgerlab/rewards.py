"""Verifier: turns a final workbook into a reward.

Design goals
------------
* Accept any *correct* fix, not just the reference string. A cell is correct if,
  dropped into the golden model, it reproduces the golden value under the
  nominal inputs AND under K random perturbations of every input
  (metamorphic testing). `=C2*(1+Inputs!$B$5)` and `=C2+C2*Inputs!$B$5` both pass.
* Reject hard-coding. Typing the right number into a cell matches at nominal
  inputs but fails the moment inputs move, so it scores as a broken cell.
* Penalise collateral damage: any non-bug cell whose behaviour changed, any
  tampered assumption, any deleted formula.
* Grade partial progress (fixed bugs / total bugs) so the signal is dense enough
  for RL, while gating the "solved" bonus on every key output matching.

reward = integrity * (0.7 * fixed_fraction + 0.3 * solved)
integrity = max(0, 1 - 0.25 * damaged_cells)
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field

from .model import INPUT_CELLS, INPUTS, KEY_OUTPUTS, PCT_INPUTS
from .task import Task
from .workbook import CellError, Workbook, is_formula

W_FIXED, W_SOLVED, DAMAGE_PENALTY = 0.7, 0.3, 0.25
MATCH_TOL = 1e-6
N_PERTURB = 3


@dataclass
class RewardBreakdown:
    reward: float
    fixed_fraction: float
    solved: bool
    integrity: float
    bugs_fixed: list[str] = field(default_factory=list)
    bugs_missed: list[str] = field(default_factory=list)
    damaged_cells: list[str] = field(default_factory=list)
    benign_rewrites: int = 0
    hardcoded_cells: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _close(x, y) -> bool:
    if isinstance(x, CellError) or isinstance(y, CellError) or isinstance(x, str) or isinstance(y, str):
        return x == y
    x, y = float(x or 0), float(y or 0)
    return abs(x - y) <= MATCH_TOL * max(1.0, abs(y))


def scenarios(task: Task, k: int = N_PERTURB) -> list[dict[tuple[str, str], float]]:
    """K seeded perturbations of every assumption cell."""
    rng = random.Random(f"perturb:{task.difficulty}:{task.seed}")
    if task.input_cells is not None:  # bring-your-own workbook: perturb detected inputs
        out = []
        for _ in range(k):
            sc = {}
            for s, a in task.input_cells:
                v = float(task.golden.get(s, a))
                sc[(s, a)] = v * rng.uniform(0.85, 1.15) + (rng.uniform(-0.005, 0.005) if abs(v) < 1 else 0)
            out.append(sc)
        return out
    base = task.spec.inputs
    out = []
    for _ in range(k):
        while True:
            sc = {key: base[key] * rng.uniform(0.8, 1.2) for key in INPUTS}
            for key in PCT_INPUTS:  # also nudge additively so 0% inputs move
                sc[key] += rng.uniform(-0.01, 0.01)
            if sc["wacc"] - sc["tg"] > 0.02:
                break
        out.append({("Inputs", INPUT_CELLS[key]): v for key, v in sc.items()})
    return out


def _with_inputs(wb: Workbook, sc: dict[tuple[str, str], float]) -> Workbook:
    wb = wb.copy()
    for (s, addr), v in sc.items():
        wb.set(s, addr, v)
    return wb


class Verifier:
    def __init__(self, task: Task):
        self.task = task
        self.scen = scenarios(task)
        self.golden_scen = [_with_inputs(task.golden, sc) for sc in self.scen]
        self.inputs = set(task.input_cells or [])  # template tasks: the whole Inputs sheet

    def _context(self, agent: Workbook) -> Workbook:
        """Golden model plus any scratch cells the agent added in empty space."""
        ctx = self.task.golden.copy()
        for s, a, c in agent.cells():
            if self.task.golden.get(s, a) is None:
                ctx.sheets[s][a] = c
        return ctx

    def cell_ok(self, agent: Workbook, ctx: Workbook, cell: tuple[str, str]) -> bool:
        s, a = cell
        content = agent.get(s, a)
        golden = self.task.golden
        if (s, a) in self.inputs or (task_is_template(self.task) and s == "Inputs"):
            return _close(content, golden.get(s, a))
        trial = ctx.copy()
        trial.set(s, a, content)
        if not _close(trial.value(s, a), golden.value(s, a)):
            return False
        for sc, g in zip(self.scen, self.golden_scen):
            if not _close(_with_inputs(trial, sc).value(s, a), g.value(s, a)):
                return False
        return True

    def solved(self, agent: Workbook) -> bool:
        g = self.task.golden
        keys = self.task.key_outputs
        if not all(_close(agent.value(s, a), g.value(s, a)) for s, a in keys):
            return False
        for sc, gs in zip(self.scen, self.golden_scen):
            ws = _with_inputs(agent, sc)
            if not all(_close(ws.value(s, a), gs.value(s, a)) for s, a in keys):
                return False
        return True

    def score(self, agent: Workbook) -> RewardBreakdown:
        task, golden = self.task, self.task.golden
        ctx = self._context(agent)
        bug_cells = task.bug_cells

        fixed, missed = [], []
        for b in task.bugs:
            if all(self.cell_ok(agent, ctx, c) for c in b.cells):
                fixed.append(b.kind)
            else:
                missed.append(b.kind)

        damaged, hardcoded, benign = [], [], 0
        for s, a, gcontent in golden.cells():
            if (s, a) in bug_cells or s == "Notes":
                continue
            if not (is_formula(gcontent) or isinstance(gcontent, (int, float))):
                continue  # text labels are cosmetic
            acontent = agent.get(s, a)
            if acontent == gcontent:
                continue
            if self.cell_ok(agent, ctx, (s, a)):
                benign += 1
            else:
                damaged.append(f"{s}!{a}")
                if is_formula(gcontent) and not is_formula(acontent):
                    hardcoded.append(f"{s}!{a}")
        for b in task.bugs:  # hard-coded "fixes" on bug cells are reported too
            for s, a in b.cells:
                if is_formula(golden.get(s, a)) and not is_formula(agent.get(s, a)) \
                        and not self.cell_ok(agent, ctx, (s, a)):
                    hardcoded.append(f"{s}!{a}")

        integrity = max(0.0, 1.0 - DAMAGE_PENALTY * len(damaged))
        fixed_fraction = len(fixed) / len(task.bugs)
        solved = self.solved(agent)
        reward = integrity * (W_FIXED * fixed_fraction + W_SOLVED * float(solved))
        return RewardBreakdown(round(reward, 6), fixed_fraction, solved, integrity,
                               fixed, missed, damaged, benign, sorted(set(hardcoded)))


def task_is_template(task: Task) -> bool:
    return task.input_cells is None


def score(task: Task, agent: Workbook) -> RewardBreakdown:
    return Verifier(task).score(agent)
