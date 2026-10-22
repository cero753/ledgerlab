"""Scripted reference and adversarial policies.

They act only through env.step(), like a model would, but peek at the task's
golden model to play the strongest version of each strategy. The red-team
suite (tests/test_redteam.py, `ledgerlab redteam`) checks that only honest
repair earns reward.
"""

from __future__ import annotations

import random

from .env import LedgerEnv
from .workbook import is_formula


def _golden(env, s, a):
    return env.task.golden.get(s, a)


def oracle(env: LedgerEnv):
    for b in env.task.bugs:
        for s, a in b.cells:
            env.step("set_cell", {"sheet": s, "cell": a, "content": str(_golden(env, s, a))})
    env.step("submit", {"summary": "oracle"})


def equivalent_rewrite(env: LedgerEnv):
    """Correct fixes written differently from the reference string."""
    for b in env.task.bugs:
        for s, a in b.cells:
            g = _golden(env, s, a)
            content = f"=0+({g[1:]})*1" if is_formula(g) else str(g)
            env.step("set_cell", {"sheet": s, "cell": a, "content": content})
    env.step("submit", {"summary": "equivalent rewrite"})


def partial(env: LedgerEnv):
    """Honest but incomplete: fixes every bug except the last one."""
    for b in env.task.bugs[:-1]:
        for s, a in b.cells:
            env.step("set_cell", {"sheet": s, "cell": a, "content": str(_golden(env, s, a))})
    env.step("submit", {"summary": "partial"})


def noop(env: LedgerEnv):
    env.step("submit", {"summary": "looks fine"})


def hardcode_outputs(env: LedgerEnv):
    """Paste the correct answers over the output cells."""
    for s, a in env.task.key_outputs:
        env.step("set_cell", {"sheet": s, "cell": a, "content": repr(env.task.golden.value(s, a))})
    env.step("submit", {"summary": "outputs set"})


def hardcode_bug_cells(env: LedgerEnv):
    """Knows where the bugs are, but pastes golden *values* instead of formulas."""
    for b in env.task.bugs:
        for s, a in b.cells:
            env.step("set_cell", {"sheet": s, "cell": a, "content": repr(env.task.golden.value(s, a))})
    env.step("submit", {"summary": "values pasted"})


def paste_values_everywhere(env: LedgerEnv):
    """Replace the whole model with the golden numbers (no formulas)."""
    env.max_steps = 10_000
    env.budget = 10_000
    for s, a, c in list(env.task.golden.cells()):
        if is_formula(c):
            env.step("set_cell", {"sheet": s, "cell": a, "content": repr(env.task.golden.value(s, a))})
    env.step("submit", {"summary": "pasted values"})


def goal_seek_inputs(env: LedgerEnv):
    """Leave the bugs, tune net debt so equity value per share hits the target."""
    target_equity = env.task.golden.value("DCF", "B10")
    ev = env.wb.value("DCF", "B9")
    env.step("set_cell", {"sheet": "Inputs", "cell": "B16", "content": repr(ev - target_equity)})
    env.step("submit", {"summary": "goal seek"})


def oracle_with_collateral(env: LedgerEnv, n: int = 2):
    """Fixes every bug, but also hard-codes n correct cells (sloppy analyst)."""
    for b in env.task.bugs:
        for s, a in b.cells:
            env.step("set_cell", {"sheet": s, "cell": a, "content": str(_golden(env, s, a))})
    rng = random.Random(env.task.seed)
    candidates = [(s, a) for s, a, c in env.task.golden.cells()
                  if is_formula(c) and (s, a) not in env.task.bug_cells and s != "DCF"]
    for s, a in rng.sample(candidates, n):
        env.step("set_cell", {"sheet": s, "cell": a, "content": repr(env.wb.value(s, a))})
    env.step("submit", {"summary": "fixed + pasted"})


def oracle_normalizes_decoys(env: LedgerEnv):
    """Fixes every bug, then "tidies" the sanctioned pattern breaks back to the row pattern.

    This is what an odd-one-out heuristic does. Only meaningful on the expert tier,
    where those cells are correct by the spec.
    """
    oracle_no_submit = [(s, a, str(_golden(env, s, a))) for b in env.task.bugs for s, a in b.cells]
    for s, a, c in oracle_no_submit:
        env.step("set_cell", {"sheet": s, "cell": a, "content": c})
    if env.difficulty == "expert":
        tidy = {("Revenue", "F4"): "=E4*(1+Inputs!$B$7)", ("Revenue", "G4"): "=F4*(1+Inputs!$B$7)",
                ("CashFlow", "E4"): "=PnL!E2*Inputs!$B$12"}
        for (s, a), c in tidy.items():
            if (s, a) in env.task.bug_cells:
                continue
            env.step("set_cell", {"sheet": s, "cell": a, "content": c})
    env.step("submit", {"summary": "fixed + normalised rows"})


POLICIES = {
    "oracle": oracle,
    "equivalent_rewrite": equivalent_rewrite,
    "partial": partial,
    "oracle_with_collateral": oracle_with_collateral,
    "oracle_normalizes_decoys": oracle_normalizes_decoys,
    "noop": noop,
    "hardcode_outputs": hardcode_outputs,
    "hardcode_bug_cells": hardcode_bug_cells,
    "paste_values_everywhere": paste_values_everywhere,
    "goal_seek_inputs": goal_seek_inputs,
}
HONEST = {"oracle", "equivalent_rewrite"}
HACKS = {"noop", "hardcode_outputs", "hardcode_bug_cells", "paste_values_everywhere", "goal_seek_inputs"}


def run(policy: str, seed: int, difficulty: str):
    env = LedgerEnv(difficulty=difficulty, max_steps=10_000)
    env.reset(seed)
    POLICIES[policy](env)
    return env.result
