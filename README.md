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
uv run pytest                        # 71 tests: engine, task QC, frozen tiers, red team, BYO
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

## Seeded errors (21 kinds, 4 tiers)

| tier | kinds |
|---|---|
| easy | hard-coded plug over a formula, SUM range misses a row, sign flip, wrong cross-sheet link, % entered as whole number |
| medium | + missing `$` anchor that drifts when filled right, prior-year mislink, growth chain skips a year, NWC level used instead of change, tax on EBITDA |
| hard | + discount factor off by one period, terminal value off Year-4 FCF, terminal value not discounted |
| expert | systematic whole-row errors (tax on EBITDA, COGS = revenue x margin, D&A off gross profit, capex added back, NWC movement reversed, capex off prior-year revenue), mid-year discounting against house convention, Gordon growth over WACC; plus sanctioned **decoys** and a brief instead of a line-by-line spec |

The expert tier exists because of the baselines below. Single-column errors are the odd formula out in their row,
so they can be spotted without finance knowledge. Whole-row errors are internally consistent. Decoys are pattern breaks
the brief approves (a management-case growth step-down, a one-off capex programme). An agent that "tidies" them takes an
integrity penalty, which tests precision as well as recall.

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

Scripted policies act through `env.step()` like a model would, but they peek at the golden model so each strategy plays its strongest version (`src/ledgerlab/policies.py`). 40 seeds per tier:

| policy | what it does | easy | medium | hard | expert |
|---|---|---|---|---|---|
| `oracle` | writes the reference fix | 1.000 | 1.000 | 1.000 | 1.000 |
| `equivalent_rewrite` | correct fix, different formula text | 1.000 | 1.000 | 1.000 | 1.000 |
| `partial` | fixes all but one bug | 0.000 | 0.467 | 0.560 | 0.525 |
| `oracle_with_collateral` | fixes everything, hard-codes 2 correct cells | 0.350 | 0.350 | 0.350 | 0.350 |
| `oracle_normalizes_decoys` | fixes everything, then "tidies" sanctioned pattern breaks | 1.000 | 1.000 | 1.000 | 0.274 |
| `noop` | submits immediately | 0.000 | 0.000 | 0.000 | 0.000 |
| `hardcode_outputs` | pastes the correct answers over every output | 0.000 | 0.000 | 0.000 | 0.000 |
| `hardcode_bug_cells` | knows the bug cells, pastes correct *values* | 0.200\* | 0.064\* | 0.049\* | 0.044\* |
| `paste_values_everywhere` | replaces the whole model with correct numbers | 0.000 | 0.000 | 0.000 | 0.000 |
| `goal_seek_inputs` | tunes net debt until per-share value matches | 0.000 | 0.000 | 0.000 | 0.000 |

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

Claude models through Claude Code headless mode (`evals/run_claude_code.py`). The agent is sandboxed: only the
ledgerlab MCP tools, built-in tools disabled, cwd outside the repo. Eval seeds are 1000+, disjoint from dev seeds.

| model | easy | medium | hard | expert | errors fixed | tool calls / ep |
|---|---|---|---|---|---|---|
| Opus | - | 1.00 (n=4) | 1.00 (n=4) | - | 100% | 13.2 |
| Sonnet | - | 1.00 (n=4) | 1.00 (n=4) | 1.00 (n=3) | 100% | 14.1 → 23.7 on expert |
| Haiku | 1.00 (n=3) | 1.00 (n=4) | 1.00 (n=4) | 1.00 (n=3) | 100% | 24.0 → 38.0 on expert |

**The template tiers are saturated for Claude.** With every rollout at 1.0, GRPO sees zero advantage within a group,
so the env gives no learning signal to these models. The diagnosis: fill-right rows are uniform and errors broke one
column, a line-by-line spec turns the task into a diff, and about 130 cells fit in six reads. The expert tier raised
tool calls per solve by about 60-70% but did not move the pass rate. The real difficulty lever is scale and ambiguity:
large expert-authored workbooks with judgement calls, which is what the bring-your-own mode is for. Small samples;
full data in `runs/results.jsonl`, per-step tool traces in `runs/traces/`. An OpenRouter runner
(`evals/run_baselines.py`) covers non-Claude models.

## Bring your own workbook

Any correct `.xlsx` model can become a stream of verified repair tasks:

```bash
uv run ledgerlab byo model.xlsx --check                      # engine coverage + detected inputs/outputs
uv run ledgerlab byo model.xlsx --bugs 3 --seed 0 --reveal   # seeded task written to byo_task.xlsx
```

The workbook is treated as golden. Inputs are numeric constants that formulas read; outputs are formula cells nothing
reads (both can be overridden). Token-level mutators (sign flip, operator swap, prior-period ref, wrong-row ref, short
range, pasted plug, % typo) seed errors near the outputs so they are material. The same QC and the same metamorphic
verifier apply, perturbing every detected input. On a real 7-sheet, 10,300-formula salary-forecast workbook: 100%
engine coverage, 1,104 inputs and 108 outputs detected, a 3-error task built in 32 s, golden graded in 4 s.
Limit: without an expert-written brief, errors are inferable from structure and convention rather than from a spec.
The MCP server exposes the same thing as `load_workbook(path)`.

## Runs locally

Pure Python 3.12+, one runtime dependency (openpyxl), no GPU. On a laptop, single process: task generation ~3 ms,
grading ~0.5 ms, about 140 full scripted episodes per second per core (medium tier). `TODAY()` is frozen so rewards
never drift with the calendar.

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
  byo.py        bring-your-own workbook: I/O detection, generic mutators
  mcp_server.py MCP server (play with Claude Code / Desktop; per-step traces)
evals/          OpenRouter baseline runner + report
tests/          engine, task QC, red-team
```

Why a custom engine instead of LibreOffice: RL rollouts need rewards that are deterministic, fast
(a full verify takes milliseconds) and free of system dependencies. The engine covers exactly the grammar the
generator emits plus what real models use: refs, quoted cross-sheet refs, ranges, arithmetic, comparisons, strings and
`&`, IF/IFERROR/AND/OR/NOT, SUM/AVERAGE/MIN/MAX/ROUND/ABS, SUMIF(S)/COUNTIF(S)/AVERAGEIF(S), SUMPRODUCT, NPV, and
DATE/YEAR/MONTH/DAY/EOMONTH/EDATE/TODAY. Unsupported functions surface as `#NAME?`, so `byo --check` tells you exactly
what it can't evaluate.

## Limits and next steps

* One model template. Next: LBO, three-statement with balance-sheet checks, SaaS cohort model. Expert-authored golden models from real filings + procedural bug injection would give many verified tasks per expert hour.
* Long-horizon variant: build the model from a 10-K excerpt and a term sheet, graded section by section with the same metamorphic checks.
* Rubric layer for judgement calls (defensible assumptions outside the spec), using an LLM judge calibrated against expert labels.
* Adversarial scenario search, to catch formulas that are only coincidentally right on random perturbations.

MIT licensed.
