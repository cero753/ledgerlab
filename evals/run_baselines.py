"""Run tool-calling models against ledgerlab through OpenRouter.

    uv run --extra evals python evals/run_baselines.py \
        --models anthropic/claude-haiku-5.5 openai/gpt-6-luna --difficulty easy medium hard --n 10

Reads OPENROUTER_API_KEY from the environment or a local .env (gitignored).
Writes one JSON line per episode to runs/results.jsonl (resumable: finished
(model, difficulty, seed) triples are skipped) and full traces to runs/traces/.
Stops launching new episodes once --budget dollars have been spent.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

from ledgerlab import SYSTEM_PROMPT, TOOLS, LedgerEnv

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
LOCK = threading.Lock()
SPENT = [0.0]


def load_key() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("OPENROUTER_API_KEY="):
                os.environ.setdefault("OPENROUTER_API_KEY", line.split("=", 1)[1].strip().strip('"'))
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set (env var or .env)")
    return key


def episode(client: OpenAI, model: str, difficulty: str, seed: int, max_tokens: int) -> dict:
    env = LedgerEnv(difficulty=difficulty)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": env.reset(seed)}]
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    turns, t0, error = 0, time.time(), None
    while not env.done:
        turns += 1
        try:
            resp = None
            for attempt in range(4):
                try:
                    resp = client.chat.completions.create(
                        model=model, messages=messages, tools=TOOLS, tool_choice="auto",
                        max_tokens=max_tokens, temperature=0.0,
                        extra_body={"usage": {"include": True}})
                    if resp.choices:
                        break
                except Exception as e:  # transient provider errors
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt * 3)
            if resp is None or not resp.choices:
                raise RuntimeError("empty response")
        except Exception as e:
            error = f"{type(e).__name__}: {e}"[:300]
            break
        u = resp.usage
        if u:
            usage["prompt_tokens"] += u.prompt_tokens or 0
            usage["completion_tokens"] += u.completion_tokens or 0
            usage["cost"] += float(getattr(u, "cost", 0) or (u.model_extra or {}).get("cost", 0) or 0)
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        messages.append({"role": "assistant", "content": msg.content or "",
                         **({"tool_calls": [c.model_dump() for c in calls]} if calls else {})})
        if not calls:
            # A model that stops calling tools is treated as submitting.
            env.step("submit", {"summary": (msg.content or "")[:500]})
            break
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            obs = "Error: arguments were not valid JSON" if args is None else env.step(c.function.name, args)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": obs})
            if env.done:
                break
    if not env.done:
        env.step("submit", {"summary": "aborted"})
    r = env.result
    rec = {
        "model": model, "difficulty": difficulty, "seed": seed, "reward": r.reward,
        "fixed_fraction": r.fixed_fraction, "solved": r.solved, "integrity": r.integrity,
        "bugs": [b.kind for b in env.task.bugs], "bugs_fixed": r.bugs_fixed, "bugs_missed": r.bugs_missed,
        "damaged_cells": r.damaged_cells, "hardcoded_cells": r.hardcoded_cells,
        "tool_calls": env.steps, "turns": turns, "seconds": round(time.time() - t0, 1),
        "error": error, **usage,
    }
    trace_dir = RUNS / "traces" / model.replace("/", "__")
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / f"{difficulty}_{seed}.json").write_text(json.dumps(
        {"record": rec, "bugs": [b.description for b in env.task.bugs], "messages": messages}, indent=1))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--difficulty", nargs="+", default=["easy", "medium", "hard"])
    ap.add_argument("--n", type=int, default=10, help="seeds per difficulty")
    ap.add_argument("--seed-offset", type=int, default=1000, help="eval seeds are disjoint from dev seeds")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--budget", type=float, default=15.0, help="USD cap across this invocation")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--out", default=str(RUNS / "results.jsonl"))
    a = ap.parse_args()

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=load_key(),
                    default_headers={"X-Title": "ledgerlab"})
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if not r.get("error"):
                done.add((r["model"], r["difficulty"], r["seed"]))

    jobs = [(m, d, a.seed_offset + s) for m in a.models for d in a.difficulty for s in range(a.n)
            if (m, d, a.seed_offset + s) not in done]
    print(f"{len(jobs)} episodes to run ({len(done)} already done)")

    def work(job):
        if SPENT[0] >= a.budget:
            return
        m, d, s = job
        rec = episode(client, m, d, s, a.max_tokens)
        with LOCK:
            SPENT[0] += rec["cost"]
            with out.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"{m:38s} {d:6s} seed={s} reward={rec['reward']:.2f} fixed={rec['bugs_fixed']} "
                  f"calls={rec['tool_calls']} ${rec['cost']:.3f} total=${SPENT[0]:.2f}"
                  + (f" ERROR {rec['error']}" if rec["error"] else ""), flush=True)

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(work, jobs))
    print(f"done. spent ${SPENT[0]:.2f}")


if __name__ == "__main__":
    main()
