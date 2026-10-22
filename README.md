# ledgerlab

An RL environment for **financial-model repair agents**. Each episode hands the agent a procedurally generated
five-sheet operating model + DCF with seeded analyst errors. The agent inspects and edits cells through tools.
The reward pays for honest repair and resists reward hacking.

Work in progress. Full write-up, baselines and design notes coming.

```bash
uv sync
uv run pytest
uv run ledgerlab redteam
uv run ledgerlab export --seed 7 --difficulty hard --out task.xlsx --reveal
```
