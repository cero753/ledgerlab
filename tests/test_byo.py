"""Bring-your-own workbook: any correct model becomes a verified repair task."""
import pytest

from ledgerlab.byo import analyze, make_task
from ledgerlab.env import LedgerEnv
from ledgerlab.rewards import score
from ledgerlab.task import generate
from ledgerlab.workbook import Workbook


@pytest.fixture(scope="module")
def xlsx(tmp_path_factory):
    # a correct model saved to disk, then re-read as if a customer sent it
    p = tmp_path_factory.mktemp("byo") / "customer_model.xlsx"
    generate(11, "hard").golden.to_xlsx(str(p))
    return p


def test_analyze_full_coverage(xlsx):
    r = analyze(xlsx)
    assert r.coverage == 1.0 and r.inputs and r.outputs


@pytest.mark.parametrize("seed", range(5))
def test_byo_task_is_graded_like_generated_tasks(xlsx, seed):
    t = make_task(xlsx, n_bugs=3, seed=seed)
    assert len(t.bugs) == 3
    assert score(t, t.golden).reward == 1.0
    assert score(t, t.workbook).reward == 0.0
    env = LedgerEnv()
    env.load(t)
    for b in t.bugs:
        for s, a in b.cells:
            env.step("set_cell", {"sheet": s, "cell": a, "content": str(t.golden.get(s, a))})
    env.step("submit", {"summary": "oracle"})
    assert env.result.reward == 1.0


def test_engine_handles_real_world_syntax():
    wb = Workbook()
    wb.add_sheet("Employee Master")
    wb.add_sheet("S")
    for i, (status, pay) in enumerate([("Active", 10), ("Left", 5), ("Active", 7)], start=2):
        wb.set("Employee Master", f"G{i}", status)
        wb.set("Employee Master", f"P{i}", pay)
    cases = {
        "=SUMIFS('Employee Master'!$P$2:$P$4,'Employee Master'!$G$2:$G$4,\"Active\")": 17,
        "=COUNTIF('Employee Master'!G2:G4,\"<>Active\")": 1,
        "=IF('Employee Master'!G3=\"\",0,1)": 1,
        "=YEAR(DATE(2026,14,1))": 2027,
        "=EOMONTH(DATE(2026,2,10),0)-DATE(2026,2,1)+1": 28,
        "=IFERROR(1/0,\"n/a\")": "n/a",
        "=\"Q\"&ROUND(2.4,0)": "Q2",
    }
    for f, want in cases.items():
        wb.set("S", "A1", f)
        assert wb.value("S", "A1") == want, f
