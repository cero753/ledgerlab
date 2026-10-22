"""Baselines through Claude Code headless mode (no API key; uses your Claude login).

    uv run python evals/run_claude_code.py --models haiku sonnet opus --difficulty easy medium hard --n 5

Each episode is a fresh `claude -p` process with only the ledgerlab MCP server
available (built-in tools disabled, cwd outside the repo), so the agent cannot
read the generator or grader source. Results append to runs/results.jsonl in
the same schema as run_baselines.py; evals/report.py summarises both.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import pathlib
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
LOCK = threading.Lock()

PROMPT = ("Use the ledgerlab MCP tools. Call new_task with seed {seed} and difficulty {difficulty}, "
          "follow its instructions, find and fix every error, then call submit.")


def episode(model: str, difficulty: str, seed: int, timeout: int) -> dict | None:
    label = f"claude-code/{model}"
    sandbox = pathlib.Path(tempfile.mkdtemp(prefix="ledgerlab_"))
    cfg = sandbox / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"ledgerlab": {
        "command": "uv", "args": ["run", "--directory", str(ROOT), "--extra", "mcp", "ledgerlab-mcp"],
        "env": {"LEDGERLAB_MODEL": label}}}}))
    mcp_log = RUNS / "mcp_results.jsonl"
    before = mcp_log.read_text().count("\n") if mcp_log.exists() else 0
    cmd = [shutil.which("claude") or "claude", "-p", PROMPT.format(seed=seed, difficulty=difficulty), "--model", model,
           "--mcp-config", str(cfg), "--strict-mcp-config", "--tools", "",
           "--allowedTools", "mcp__ledgerlab__*", "--output-format", "json"]
    try:
        p = subprocess.run(cmd, cwd=sandbox, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", env={**os.environ, "LEDGERLAB_MODEL": label})
        meta = json.loads(p.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        meta = {"error": type(e).__name__}
    # find this episode's graded record written by the MCP server
    with LOCK:
        lines = mcp_log.read_text().splitlines() if mcp_log.exists() else []
    recs = [json.loads(l) for l in lines[before:]]
    mine = [r for r in recs if r["model"] == label and r["seed"] == seed and r["difficulty"] == difficulty]
    if mine:
        rec = mine[-1]
    else:  # never submitted: grade as untouched? no - record as error so it is visible
        rec = {"model": label, "difficulty": difficulty, "seed": seed, "error": meta.get("error") or "no submit"}
    rec["turns"] = meta.get("num_turns")
    rec["subscription_cost_equiv"] = meta.get("total_cost_usd")
    trace = RUNS / "traces" / label.replace("/", "__")
    trace.mkdir(parents=True, exist_ok=True)
    (trace / f"{difficulty}_{seed}.json").write_text(json.dumps({"record": rec, "final": meta.get("result")}, indent=1))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["haiku", "sonnet"])
    ap.add_argument("--difficulty", nargs="+", default=["easy", "medium", "hard"])
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--seed-offset", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=1200)
    a = ap.parse_args()
    out = RUNS / "results.jsonl"
    RUNS.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        for l in out.read_text().splitlines():
            r = json.loads(l)
            if not r.get("error"):
                done.add((r["model"], r["difficulty"], r["seed"]))
    jobs = [(m, d, a.seed_offset + s) for m in a.models for d in a.difficulty for s in range(a.n)
            if (f"claude-code/{m}", d, a.seed_offset + s) not in done]
    print(f"{len(jobs)} episodes", flush=True)

    def work(job):
        rec = episode(*job, a.timeout)
        with LOCK:
            with out.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        print(f"{rec['model']:22s} {rec['difficulty']:6s} seed={rec['seed']} "
              + (f"ERROR {rec['error']}" if rec.get("error") else
                 f"reward={rec['reward']:.2f} fixed={rec['bugs_fixed']} missed={rec['bugs_missed']} calls={rec['tool_calls']}"),
              flush=True)

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(work, jobs))


if __name__ == "__main__":
    main()
