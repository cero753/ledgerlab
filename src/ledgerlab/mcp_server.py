"""MCP server: lets Claude Code / Claude Desktop (or any MCP client) play ledgerlab.

    claude mcp add ledgerlab -- uv run --directory /path/to/ledgerlab --extra mcp ledgerlab-mcp

Then ask: "Start a hard ledgerlab task with seed 1007 and fix every error."
Each submitted episode is appended to runs/mcp_results.jsonl (label via LEDGERLAB_MODEL).
"""

from __future__ import annotations

import atexit
import json
import os
import pathlib
import time

from mcp.server.mcpserver import MCPServer

from .env import SYSTEM_PROMPT, LedgerEnv

ROOT = pathlib.Path(__file__).resolve().parents[2]
mcp = MCPServer("ledgerlab", instructions=SYSTEM_PROMPT)
_state: dict = {"env": None, "t0": 0.0}


def _env() -> LedgerEnv:
    if _state["env"] is None:
        raise ValueError("No active task. Call new_task first.")
    return _state["env"]


def _label() -> str:
    return os.environ.get("LEDGERLAB_MODEL", "mcp-client")


def _trace_path(env: LedgerEnv) -> pathlib.Path:
    tdir = ROOT / "runs" / "traces" / "tool_logs"
    tdir.mkdir(parents=True, exist_ok=True)
    return tdir / f"{_label().replace('/', '__')}_{env.difficulty}_{env.task.seed}.json"


def _persist(env: LedgerEnv, record: dict | None = None) -> None:
    """Write the tool log after every call, so traces exist even if the client never submits."""
    _trace_path(env).write_text(json.dumps(
        {"record": record, "bugs": [b.description for b in env.task.bugs], "log": env.log}, indent=1))


def _record(env: LedgerEnv, submitted: bool) -> dict:
    if env.result is None:  # client walked away: grade the workbook as it stands
        env._finish()
    r = env.result
    rec = {"model": _label(), "difficulty": env.difficulty, "seed": env.task.seed, **r.to_dict(),
           "bugs": [b.kind for b in env.task.bugs], "tool_calls": env.steps, "submitted": submitted,
           "seconds": round(time.time() - _state["t0"], 1), "cost": 0.0, "error": None}
    out = ROOT / "runs" / "mcp_results.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    _persist(env, rec)
    _state["recorded"] = True
    return rec


@atexit.register
def _record_unsubmitted() -> None:
    env = _state.get("env")
    if env is not None and not _state.get("recorded"):
        _record(env, submitted=False)


def _step(name: str, args: dict) -> str:
    env = _env()
    obs = env.step(name, args)
    _persist(env)
    return obs


@mcp.tool()
def new_task(seed: int = 1000, difficulty: str = "medium") -> str:
    """Start a new financial-model repair task. difficulty: easy | medium | hard | expert."""
    _record_unsubmitted()
    env = LedgerEnv(difficulty=difficulty)
    prompt = env.reset(seed)
    _state.update(env=env, t0=time.time(), recorded=False)
    return f"{SYSTEM_PROMPT}\n\n{prompt}"


@mcp.tool()
def load_workbook(path: str, n_bugs: int = 3, seed: int = 0) -> str:
    """Start a task from your own correct .xlsx model: errors are seeded into it and graded the same way."""
    from .byo import make_task

    _record_unsubmitted()
    env = LedgerEnv()
    prompt = env.load(make_task(path, n_bugs=n_bugs, seed=seed))
    _state.update(env=env, t0=time.time(), recorded=False)
    return f"{SYSTEM_PROMPT}\n\n{prompt}\nSheets: {', '.join(env.wb.sheets)}"


@mcp.tool()
def list_sheets() -> str:
    """List sheets with their used ranges."""
    return _step("list_sheets", {})


@mcp.tool()
def read_range(sheet: str, range: str) -> str:
    """Read cells in a range such as 'A1:G10'. Shows formulas and computed values (max 80 cells)."""
    return _step("read_range", {"sheet": sheet, "range": range})


@mcp.tool()
def trace_precedents(sheet: str, cell: str) -> str:
    """Show the cells a formula reads, with their values."""
    return _step("trace_precedents", {"sheet": sheet, "cell": cell})


@mcp.tool()
def set_cell(sheet: str, cell: str, content: str) -> str:
    """Write a formula ('=...') or number into a cell. Returns new value and change in valuation outputs."""
    return _step("set_cell", {"sheet": sheet, "cell": cell, "content": content})


@mcp.tool()
def submit(summary: str) -> str:
    """Finish the episode and get graded."""
    env = _env()
    obs = env.step("submit", {"summary": summary})
    if _state.get("recorded"):
        return "Episode already graded."
    _record(env, submitted=True)
    r = env.result
    lines = [obs, "", f"REWARD {r.reward:.3f}  (fixed {len(r.bugs_fixed)}/{len(env.task.bugs)} bugs, "
             f"solved={r.solved}, integrity={r.integrity:.2f})", "", "Seeded errors:"]
    for b in env.task.bugs:
        mark = "FIXED " if b.kind in r.bugs_fixed else "MISSED"
        lines.append(f"  [{mark}] {b.kind}: {b.description}")
    if r.damaged_cells:
        lines.append(f"Collateral damage: {', '.join(r.damaged_cells)}")
    if r.hardcoded_cells:
        lines.append(f"Hard-coded cells (fail perturbation test): {', '.join(r.hardcoded_cells)}")
    if r.benign_rewrites:
        lines.append(f"Equivalent rewrites accepted: {r.benign_rewrites}")
    return "\n".join(lines)


def main():
    mcp.run("stdio")


if __name__ == "__main__":
    main()
