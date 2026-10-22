# ledgerlab

**An RL environment for financial-model repair agents, with a verifier built to resist reward hacking.**

Every episode generates a new five-sheet operating model + DCF (Inputs → Revenue → P&L → Cash flow → DCF, plus a Notes
spec) and seeds it with realistic analyst errors. The agent reads and edits cells through tools until it submits.
The grader then re-runs the agent's model **under perturbed assumptions**. A correct fix passes, whatever formula it
uses. A pasted number does not.

Why this task: on SpreadsheetBench 2 ([arXiv 2606.29955](https://arxiv.org/abs/2606.29955)) the best model
reaches 34.9% overall but only **12% on spreadsheet debugging**. Debugging is checkable, high-value work that
analysts do every day, so it is a good fit for RL with verifiable rewards.

```
uv sync
uv run pytest                        # 50 tests: engine, task QC, red-team
uv run ledgerlab redteam             # reward table for scripted honest + hacking policies
uv run ledgerlab export --seed 7 --difficulty hard --out task.xlsx --reveal
```

## The episode

```python
from ledgerlab import LedgerEnv, TOOLS, SYSTEM_PROMPT

env = LedgerEnv(difficulty="hard")          # easy: 1 bug / 25 calls, medium: 3 / 40, hard: 5 / 60
prompt = env.reset(seed=1007)
env.step("read_range", {"sheet": "Notes", "range": "A1:A40"})
env.step("trace_precedents", {"sheet": "PnL", "cell": "E9"})
env.step("set_cell", {"sheet": "PnL", "cell": "E9", "content": "=E8*Inputs!$B$11"})
env.step("submit", {"summary": "tax was applied to EBITDA"})
env.result   # RewardBreakdown(reward=..., bugs_fixed=[...], damaged_cells=[...], hardcoded_cells=[...])
```

Tools (OpenAI function-calling schemas in `ledgerlab.TOOLS`): `list_sheets`, `read_range`, `trace_precedents`,
`set_cell`, `submit`. `set_cell` returns the new value plus how EV / equity / per-share moved. That gives the agent
feedback without revealing the answer.

## Seeded errors (13 kinds, 3 tiers)

| tier | kinds |
|---|---|
| easy | hard-coded plug over a formula, SUM range misses a row, sign flip, wrong cross-sheet link, % entered as whole number |
| medium | + missing `$` anchor that drifts when filled right, prior-year mislink, growth chain skips a year, NWC level used instead of change, tax on EBITDA |
| hard | + discount factor off by one period, terminal value off Year-4 FCF, terminal value not discounted |

Formulas are written by an explicit **fill-right** step, like an analyst dragging a cell. That makes the anchoring bug
real: column C still resolves correctly and the drift starts in D.

## Task quality control

A generated task is rejected and redrawn (same seeded RNG, so still reproducible) unless:

1. every bug is cell-disjoint from the others,
2. every bug **on its own** moves at least one key output by >0.1% (it is detectable),
3. with all bugs present, fixing any single one still moves an output (**no masking**, so partial credit always tracks real progress),
4. the corrupted model evaluates with no `#DIV/0!` / `#VALUE!` errors,
5. every bug is inferable from what the agent can see: the Notes sheet states every assumption and every line's logic.

Ill-posed tasks quietly teach a policy noise. These checks run in `tests/test_tasks.py` over every tier.

## The verifier

```
                         ┌───────────────────────────────────────────────┐
 agent workbook ──┬────▶ │ per bug cell: drop agent's content into the    │──▶ bug fixed?
                  │      │ golden model, compare at nominal inputs AND    │
                  │      │ under K=3 random perturbations of all 16 inputs │
                  │      └───────────────────────────────────────────────┘
                  ├────▶ same check on every other changed cell ─────────────▶ collateral damage
                  └────▶ all key outputs match golden in every scenario ──────▶ solved

reward = integrity × (0.7 × fixed_fraction + 0.3 × solved)
integrity = max(0, 1 − 0.25 × damaged_cells)
```

* **Behaviour, not strings.** `=C2*(1+Inputs!$B$5)` and `=0+(C2*(1+Inputs!$B$5))*1` both pass. A fix is judged by what it computes across scenarios, not by matching the reference text.
* **Metamorphic anti-hard-coding.** A typed-in number matches at nominal inputs and breaks as soon as the assumptions move.
* **Cell-isolated credit.** Each cell is checked in the golden context, so a correct fix isn't marked wrong because an upstream bug is still open.
* **Multiplicative integrity.** Damage can't be bought back with fixes. Tampering with a correct assumption counts as damage.
* Graded: with 5 bugs there are 6 reward levels before the solved bonus. That only helps training if pass rates aren't saturated (see Baselines).

## Red team: only honest repair pays

Scripted policies act through `env.step()` like a model would, but they peek at the golden model so each strategy plays its strongest version (`src/ledgerlab/policies.py`). 50 seeds per tier:

| policy | what it does | easy | medium | hard |
|---|---|---|---|---|
| `oracle` | writes the reference fix | 1.000 | 1.000 | 1.000 |
| `equivalent_rewrite` | correct fix, different formula text | 1.000 | 1.000 | 1.000 |
| `partial` | fixes all but one bug | 0.000 | 0.467 | 0.560 |
| `oracle_with_collateral` | fixes everything, hard-codes 2 correct cells | 0.350 | 0.350 | 0.350 |
| `noop` | submits immediately | 0.000 | 0.000 | 0.000 |
| `hardcode_outputs` | pastes the correct answers over every output | 0.000 | 0.000 | 0.000 |
| `hardcode_bug_cells` | knows the bug cells, pastes correct *values* | 0.280\* | 0.065\* | 0.048\* |
| `paste_values_everywhere` | replaces the whole model with correct numbers | 0.000 | 0.000 | 0.000 |
| `goal_seek_inputs` | tunes net debt until per-share value matches | 0.000 | 0.000 | 0.000 |

\* All credit comes from `unit_mismatch` bugs, where the correct fix *is* a number (25 → 0.25). Every formula cell fixed by pasting a value scores zero (asserted in `tests/test_redteam.py`).

These are pytest assertions. Any reward change that opens a hack fails CI.

## Play it with Claude (no API key)

ledgerlab ships an MCP server, so Claude Code or Claude Desktop can work a task directly on your normal Claude login:

```bash
uv sync --extra mcp
claude mcp add ledgerlab -- uv run --directory /path/to/ledgerlab --extra mcp ledgerlab-mcp
claude   # then: "Start a hard ledgerlab task with seed 1007, fix every error, then submit."
```

`submit` returns the graded breakdown: which seeded errors were fixed or missed, any collateral damage, and any hard-coded cells.
For a headless sweep on your subscription, run `uv run python evals/run_claude_code.py --models haiku sonnet opus`. Each episode runs in a
sandbox with built-in tools disabled and the cwd outside the repo, so the agent can't read the grader.

## Baselines

_Running: frontier and open models via OpenRouter, eval seeds 1000+ (disjoint from dev seeds). Results land in `runs/results.md`._

## Layout

```
src/ledgerlab/
  workbook.py   deterministic formula engine (recursive-descent parser, cycle/error handling, xlsx I/O)
  model.py      procedural 5-sheet model builder + Notes spec
  bugs.py       13 error injectors
  task.py       seeded generation + quality control
  rewards.py    metamorphic verifier + reward
  env.py        multi-turn tool env + OpenAI tool schemas
  policies.py   scripted honest / partial / hacking policies
evals/          OpenRouter baseline runner + report
tests/          engine, task QC, red-team
```

Why a custom engine instead of LibreOffice: RL rollouts need rewards that are deterministic, fast
(a full verify takes milliseconds) and free of system dependencies. The engine covers exactly the grammar the
generator emits (refs, cross-sheet refs, ranges, `+ - * / ^`, SUM/AVERAGE/MIN/MAX/ROUND/ABS) and returns Excel-style errors.

## Limits and next steps

* One model template. Next: LBO, three-statement with balance-sheet checks, SaaS cohort model. Expert-authored golden models from real filings + procedural bug injection would give many verified tasks per expert hour.
* Long-horizon variant: build the model from a 10-K excerpt and a term sheet, graded section by section with the same metamorphic checks.
* Rubric layer for judgement calls (defensible assumptions outside the spec), using an LLM judge calibrated against expert labels.
* Adversarial scenario search, to catch formulas that are only coincidentally right on random perturbations.

MIT licensed.
