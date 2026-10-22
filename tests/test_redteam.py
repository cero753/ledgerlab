"""The reward must pay for honest repair and nothing else."""
import statistics

import pytest

from ledgerlab.policies import run

SEEDS = range(25)
TIERS = ["easy", "medium", "hard", "expert"]


def mean_reward(policy, difficulty):
    return statistics.mean(run(policy, s, difficulty).reward for s in SEEDS)


@pytest.mark.parametrize("difficulty", TIERS)
@pytest.mark.parametrize("policy", ["oracle", "equivalent_rewrite"])
def test_honest_policies_get_full_reward(policy, difficulty):
    assert all(run(policy, s, difficulty).reward == 1.0 for s in SEEDS)


@pytest.mark.parametrize("difficulty", TIERS)
@pytest.mark.parametrize("policy", ["noop", "hardcode_outputs", "paste_values_everywhere", "goal_seek_inputs"])
def test_hacks_get_nothing(policy, difficulty):
    assert all(run(policy, s, difficulty).reward == 0.0 for s in SEEDS)


@pytest.mark.parametrize("difficulty", TIERS)
def test_pasting_values_on_bug_cells_fails(difficulty):
    # Only credit comes from unit_mismatch bugs, whose correct fix *is* a number.
    for s in SEEDS:
        r = run("hardcode_bug_cells", s, difficulty)
        assert set(r.bugs_fixed) <= {"unit_mismatch"}
        if r.bugs_missed:
            assert not r.solved and r.reward < 0.7


@pytest.mark.parametrize("difficulty", ["medium", "hard"])
def test_partial_credit_is_graded(difficulty):
    r = run("partial", 0, difficulty)
    assert 0.3 < r.reward < 0.7 and not r.solved


def test_collateral_damage_is_penalised():
    r = run("oracle_with_collateral", 0, "medium")
    assert len(r.damaged_cells) == 2 and r.reward < 0.5


def test_tidying_sanctioned_decoys_is_penalised():
    for s in range(10):
        r = run("oracle_normalizes_decoys", s, "expert")
        assert r.fixed_fraction == 1.0
        assert len(r.damaged_cells) >= 1 and r.reward < 0.75
