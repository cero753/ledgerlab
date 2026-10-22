"""MCP server: lets Claude Code / Claude Desktop (or any MCP client) play ledgerlab.

    claude mcp add ledgerlab -- uv run --directory /path/to/ledgerlab --extra mcp ledgerlab-mcp

Then ask: "Start a hard ledgerlab task with seed 1007 and fix every error."
Each submitted episode is appended to runs/mcp_results.jsonl (label via LEDGERLAB_MODEL).
"""

from __future__ import annotations

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


@mcp.tool()
def new_task(seed: int = 1000, difficulty: str = "medium") -> str:
    """Start a new financial-model repair task. difficulty: easy | medium | hard."""
    env = LedgerEnv(difficulty=difficulty)
    prompt = env.reset(seed)
    _state.update(env=env, t0=time.time())
    return f"{SYSTEM_PROMPT}\n\n{prompt}"


@mcp.tool()
def list_sheets() -> str:
    """List sheets with their used ranges."""
    return _env().step("list_sheets", {})


@mcp.tool()
def read_range(sheet: str, range: str) -> str:
    """Read cells in a range such as 'A1:G10'. Shows formulas and computed values (max 80 cells)."""
    return _env().step("read_range", {"sheet": sheet, "range": range})


@mcp.tool()
def trace_precedents(sheet: str, cell: str) -> str:
    """Show the cells a formula reads, with their values."""
    return _env().step("trace_precedents", {"sheet": sheet, "cell": cell})


@mcp.tool()
def set_cell(sheet: str, cell: str, content: str) -> str:
    """Write a formula ('=...') or number into a cell. Returns new value and change in valuation outputs."""
    return _env().step("set_cell", {"sheet": sheet, "cell": cell, "content": content})


@mcp.tool()
def submit(summary: str) -> str:
    """Finish the episode and get graded."""
    env = _env()
    obs = env.step("submit", {"summary": summary})
    r = env.result
    rec = {"model": os.environ.get("LEDGERLAB_MODEL", "mcp-client"), "difficulty": env.difficulty,
           "seed": env.task.seed, **r.to_dict(), "bugs": [b.kind for b in env.task.bugs],
           "tool_calls": env.steps, "seconds": round(time.time() - _state["t0"], 1), "cost": 0.0,
           "error": None}
    out = ROOT / "runs" / "mcp_results.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("a") as f:
        f.write(json.dumps(rec) + "\n")
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
