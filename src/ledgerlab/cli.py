"""ledgerlab CLI: export tasks, run the red-team suite."""

from __future__ import annotations

import argparse
import statistics

from .bugs import TIERS
from .policies import HACKS, HONEST, POLICIES, run
from .task import generate


def cmd_redteam(args):
    print(f"| policy | kind | " + " | ".join(TIERS) + " |")
    print("|---|---|" + "---|" * len(TIERS))
    for p in POLICIES:
        kind = "honest" if p in HONEST else "hack" if p in HACKS else "partial"
        cells = []
        for d in TIERS:
            rs = [run(p, s, d).reward for s in range(args.n)]
            cells.append(f"{statistics.mean(rs):.3f}")
        print(f"| `{p}` | {kind} | " + " | ".join(cells) + " |")


def cmd_export(args):
    t = generate(args.seed, args.difficulty)
    t.workbook.to_xlsx(args.out)
    print(f"wrote {args.out}: {t.spec.company}, {len(t.bugs)} bug(s)")
    if args.reveal:
        for b in t.bugs:
            print(f"  - {b.kind}: {b.description}")


def cmd_show(args):
    t = generate(args.seed, args.difficulty)
    for s, cells in t.workbook.sheets.items():
        print(f"== {s}")
        for a, c in cells.items():
            v = t.workbook.value(s, a)
            print(f"  {a}: {c}" + (f"  -> {v}" if str(c).startswith("=") else ""))


def main():
    ap = argparse.ArgumentParser(prog="ledgerlab")
    sub = ap.add_subparsers(required=True)
    r = sub.add_parser("redteam", help="score scripted honest/hacking policies")
    r.add_argument("-n", type=int, default=50)
    r.set_defaults(fn=cmd_redteam)
    for name, fn in (("export", cmd_export), ("show", cmd_show)):
        e = sub.add_parser(name)
        e.add_argument("--seed", type=int, default=0)
        e.add_argument("--difficulty", default="medium", choices=list(TIERS))
        if name == "export":
            e.add_argument("--out", default="task.xlsx")
            e.add_argument("--reveal", action="store_true")
        e.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
