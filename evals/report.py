"""Summarise runs/results.jsonl into runs/results.md and charts in docs/.

    uv run --extra evals python evals/report.py
"""

from __future__ import annotations

import collections
import json
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[1]
TIERS = ["easy", "medium", "hard"]


def load(path):
    recs = [json.loads(l) for l in pathlib.Path(path).read_text().splitlines() if l.strip()]
    return [r for r in recs if not r.get("error")], [r for r in recs if r.get("error")]


def mean(xs):
    return statistics.mean(xs) if xs else float("nan")


def main(path=ROOT / "runs" / "results.jsonl"):
    recs, errs = load(path)
    by = collections.defaultdict(list)
    for r in recs:
        by[(r["model"], r["difficulty"])].append(r)
    models = sorted({r["model"] for r in recs},
                    key=lambda m: -mean([r["reward"] for r in recs if r["model"] == m]))

    lines = ["| model | " + " | ".join(f"{t} reward" for t in TIERS) + " | solved | bugs fixed | hard-coded cells / ep | collateral cells / ep | tool calls / ep |",
             "|---|" + "---|" * (len(TIERS) + 5)]
    for m in models:
        rs = [r for r in recs if r["model"] == m]
        tier = [f"{mean([r['reward'] for r in by[(m, t)]]):.2f} (n={len(by[(m, t)])})" if by[(m, t)] else "-"
                for t in TIERS]
        fixed = sum(len(r["bugs_fixed"]) for r in rs) / max(1, sum(len(r["bugs"]) for r in rs))
        lines.append(f"| `{m}` | " + " | ".join(tier)
                     + f" | {mean([r['solved'] for r in rs]):.0%} | {fixed:.0%}"
                     + f" | {mean([len(r['hardcoded_cells']) for r in rs]):.2f}"
                     + f" | {mean([len(r['damaged_cells']) for r in rs]):.2f}"
                     + f" | {mean([r['tool_calls'] for r in rs]):.1f} |")

    # per-bug-kind detection rate across all models
    seen, fixed = collections.Counter(), collections.Counter()
    for r in recs:
        seen.update(r["bugs"])
        fixed.update(r["bugs_fixed"])
    kinds = sorted(seen, key=lambda k: fixed[k] / seen[k])
    lines += ["", "| bug kind | fix rate (all models) | n |", "|---|---|---|"]
    lines += [f"| `{k}` | {fixed[k] / seen[k]:.0%} | {seen[k]} |" for k in kinds]
    if errs:
        lines += ["", f"{len(errs)} episode(s) excluded for provider/API errors."]
    md = "\n".join(lines)
    (ROOT / "runs" / "results.md").write_text(md + "\n")
    print(md)

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 4.2))
    w = 0.8 / len(TIERS)
    for i, t in enumerate(TIERS):
        ax.bar([j + i * w for j in range(len(models))],
               [mean([r["reward"] for r in by[(m, t)]]) if by[(m, t)] else 0 for m in models],
               w, label=t, color=["#7fb3d5", "#2e86c1", "#1b4f72"][i])
    ax.set_xticks([j + w for j in range(len(models))], [m.split("/")[-1] for m in models],
                  rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("mean reward")
    ax.set_ylim(0, 1)
    ax.set_title("ledgerlab: mean reward by model and difficulty")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(docs / "rewards_by_model.png", dpi=160)

    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.barh([k for k in kinds], [fixed[k] / seen[k] for k in kinds], color="#2e86c1")
    ax.set_xlim(0, 1)
    ax.set_xlabel("fix rate across all models")
    ax.set_title("Which analyst errors do agents catch?")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(docs / "fix_rate_by_bug.png", dpi=160)


if __name__ == "__main__":
    main()
