import pytest

import hashlib
import json

from ledgerlab.bugs import ALL_KINDS, EXPERT_KINDS, TIERS
from ledgerlab.model import KEY_OUTPUTS
from ledgerlab.task import apply_bugs, generate, outputs, outputs_differ
from ledgerlab.workbook import CellError

SEEDS = range(40)


@pytest.mark.parametrize("difficulty", list(TIERS))
def test_deterministic(difficulty):
    a, b = generate(3, difficulty), generate(3, difficulty)
    assert a.workbook.sheets == b.workbook.sheets
    assert [x.kind for x in a.bugs] == [x.kind for x in b.bugs]


@pytest.mark.parametrize("difficulty", list(TIERS))
def test_quality_control(difficulty):
    for seed in SEEDS:
        t = generate(seed, difficulty)
        assert len(t.bugs) == TIERS[difficulty][1]
        assert not any(isinstance(v, CellError) for v in outputs(t.golden))
        assert t.golden.value("DCF", "B11") > 0
        for i, b in enumerate(t.bugs):  # every bug detectable and unmasked
            assert outputs_differ(outputs(apply_bugs(t.golden, [b])), outputs(t.golden))
            rest = t.bugs[:i] + t.bugs[i + 1:]
            assert outputs_differ(outputs(apply_bugs(t.golden, rest)), outputs(t.workbook))


def test_every_bug_kind_occurs():
    seen = {b.kind for s in range(200) for b in generate(s, "hard").bugs}
    assert seen == set(ALL_KINDS)


def test_notes_state_every_assumption():
    t = generate(0, "medium")
    notes = " ".join(c for _, _, c in t.golden.cells() if isinstance(c, str))
    for r in range(2, 18):
        assert f"Inputs!B{r}" in notes


def test_key_outputs_are_formulas():
    t = generate(0, "easy")
    assert all(str(t.golden.get(s, a)).startswith("=") for s, a in KEY_OUTPUTS)


def test_every_expert_kind_occurs():
    seen = {b.kind for s in range(200) for b in generate(s, "expert").bugs}
    assert seen == set(EXPERT_KINDS)


@pytest.mark.parametrize("difficulty,digest", [
    ("easy", "4cfff55660c7788a"), ("medium", "4b8b34e2adb43942"), ("hard", "d1dc07501c958a1c")])
def test_published_tiers_are_frozen(difficulty, digest):
    """Baselines were run on these tasks; generator changes must not alter them."""
    h = hashlib.sha256()
    for s in [0, 1, 2, 1000, 1001, 1002, 1003]:
        t = generate(s, difficulty)
        h.update(json.dumps(t.workbook.sheets, sort_keys=True).encode())
        h.update(json.dumps(t.golden.sheets, sort_keys=True).encode())
    assert h.hexdigest()[:16] == digest
