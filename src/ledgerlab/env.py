"""Framework-agnostic multi-turn environment.

    env = LedgerEnv(difficulty="medium")
    prompt = env.reset(seed=7)
    obs = env.step("read_range", {"sheet": "PnL", "range": "A1:G10"})
    ...
    obs = env.step("submit", {"summary": "fixed ..."})
    env.result  # RewardBreakdown

Tools are exposed as OpenAI-style JSON schemas (TOOLS) so any tool-calling
model or trainer can drive the env.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rewards import RewardBreakdown, Verifier
from .task import Task, generate
from .workbook import CellError, Workbook, expand_range, is_formula, references, split_addr

MAX_CELLS_PER_READ = 80
STEP_BUDGET = {"easy": 25, "medium": 40, "hard": 60, "expert": 60}

SYSTEM_PROMPT = """You are a meticulous financial-model reviewer.
You are given a five-sheet operating model and DCF (Inputs, Revenue, PnL, CashFlow, DCF) plus a
Notes sheet that is the authoritative specification: every assumption value and every line's logic.
A junior analyst introduced one or more errors. Your job: find every error and fix it in place.

Rules
- Use the tools. Formulas start with '='. Supported: + - * / ^, parentheses, cell refs (A1, $A$1,
  Sheet!A1), and SUM/AVERAGE/MIN/MAX/ROUND/ABS over ranges.
- Fix errors with formulas that implement the spec. Never paste computed numbers over formulas:
  the grader re-runs your model under changed assumptions, so hard-coded values fail.
- Do not change correct cells or assumptions that already match the Notes. Collateral edits are penalised.
- When you are confident every error is fixed, call submit with a short summary.
You have a limited number of tool calls."""

TOOLS = [
    {"type": "function", "function": {
        "name": "list_sheets", "description": "List sheets with their used ranges.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "read_range",
        "description": f"Read cells in a range, e.g. 'A1:G10'. Shows formula and computed value. Max {MAX_CELLS_PER_READ} non-empty cells.",
        "parameters": {"type": "object", "properties": {
            "sheet": {"type": "string"}, "range": {"type": "string"}}, "required": ["sheet", "range"]}}},
    {"type": "function", "function": {
        "name": "trace_precedents", "description": "Show the cells a formula cell reads, with their values.",
        "parameters": {"type": "object", "properties": {
            "sheet": {"type": "string"}, "cell": {"type": "string"}}, "required": ["sheet", "cell"]}}},
    {"type": "function", "function": {
        "name": "set_cell",
        "description": "Write a formula ('=...') or a number into a cell. Returns the new value and the change in key outputs.",
        "parameters": {"type": "object", "properties": {
            "sheet": {"type": "string"}, "cell": {"type": "string"},
            "content": {"type": "string", "description": "Formula starting with '=' or a number"}},
            "required": ["sheet", "cell", "content"]}}},
    {"type": "function", "function": {
        "name": "submit", "description": "Finish the episode.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]


def fmt(v) -> str:
    if v is None:
        return "(empty)"
    if isinstance(v, CellError):
        return str(v)
    if isinstance(v, str):
        return repr(v)
    return f"{v:,.6g}" if abs(v) < 1 else f"{v:,.2f}"


@dataclass
class LedgerEnv:
    difficulty: str = "medium"
    max_steps: int | None = None
    task: Task | None = None
    wb: Workbook | None = None
    steps: int = 0
    done: bool = False
    result: RewardBreakdown | None = None
    log: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def reset(self, seed: int) -> str:
        return self.load(generate(seed, self.difficulty))

    def load(self, task: Task) -> str:
        """Start an episode on an existing task (e.g. a bring-your-own workbook)."""
        self.task, self.difficulty = task, task.difficulty
        self.wb = self.task.workbook.copy()
        self.steps, self.done, self.result, self.log = 0, False, None, []
        self.budget = self.max_steps or STEP_BUDGET.get(self.difficulty, 60)
        self._verifier = Verifier(self.task)
        if self.task.brief:
            return f"{self.task.brief}\nYou have {self.budget} tool calls."
        return (f"Review the model for {self.task.name}. Start by reading the Notes sheet. "
                f"You have {self.budget} tool calls.")

    def step(self, name: str, args: dict | None = None) -> str:
        if self.done:
            return "Episode is over."
        args = args or {}
        self.steps += 1
        try:
            obs = self._dispatch(name, args)
        except Exception as e:  # tool errors are observations, not crashes
            obs = f"Error: {e}"
        if not self.done and self.steps >= self.budget:
            self._finish()
            obs += "\n[tool budget exhausted - episode submitted automatically]"
        self.log.append({"tool": name, "args": args, "obs": obs})
        return obs

    def _finish(self) -> None:
        self.done = True
        self.result = self._verifier.score(self.wb)

    # ------------------------------------------------------------------ #
    def _dispatch(self, name, args):
        if name == "list_sheets":
            out = []
            for s, cells in self.wb.sheets.items():
                if cells:
                    addrs = [split_addr(a) for a in cells]
                    out.append(f"{s}: {len(cells)} cells, rows 1-{max(r for _, r in addrs)}")
            return "\n".join(out)
        if name == "read_range":
            return self._read(args["sheet"], args["range"])
        if name == "trace_precedents":
            s, a = args["sheet"], args["cell"].upper().replace("$", "")
            c = self.wb.get(s, a)
            if not is_formula(c):
                return f"{s}!{a} is not a formula (content: {fmt(c)})"
            lines = [f"{s}!{a} = {c} -> {fmt(self.wb.value(s, a))}"]
            for ps, pa in references(c, s)[:MAX_CELLS_PER_READ]:
                pc = self.wb.get(ps, pa)
                f = f"{pc}  " if is_formula(pc) else ""
                lines.append(f"  {ps}!{pa}: {f}-> {fmt(self.wb.value(ps, pa))}")
            return "\n".join(lines)
        if name == "set_cell":
            return self._set(args["sheet"], args["cell"], args["content"])
        if name == "submit":
            self._finish()
            return "Submitted."
        return f"Unknown tool {name!r}"

    def _read(self, sheet, rng):
        if sheet not in self.wb.sheets:
            return f"No sheet {sheet!r}. Sheets: {', '.join(self.wb.sheets)}"
        rng = rng.upper().replace("$", "")
        addrs = expand_range(*rng.split(":")) if ":" in rng else [rng]
        lines, n = [], 0
        for a in addrs:
            c = self.wb.get(sheet, a)
            if c is None:
                continue
            n += 1
            if n > MAX_CELLS_PER_READ:
                lines.append(f"... truncated at {MAX_CELLS_PER_READ} cells; read a smaller range")
                break
            if is_formula(c):
                lines.append(f"{a}: {c} -> {fmt(self.wb.value(sheet, a))}")
            elif isinstance(c, str):
                lines.append(f"{a}: {c}")
            else:
                lines.append(f"{a}: {fmt(c)}")
        return "\n".join(lines) or "(range is empty)"

    def _set(self, sheet, cell, content):
        if sheet not in self.wb.sheets:
            return f"No sheet {sheet!r}."
        if sheet == "Notes":
            return "Notes is read-only."
        content = content.strip() if isinstance(content, str) else content
        if isinstance(content, str) and not content.startswith("="):
            try:
                content = float(content.replace(",", ""))
            except ValueError:
                pass
        watch = self.task.key_outputs[-3:]
        before = {k: self.wb.value(*k) for k in watch}
        self.wb.set(sheet, cell, content)
        a = cell.upper().replace("$", "")
        after = {k: self.wb.value(*k) for k in watch}
        delta = ", ".join(f"{s}!{x} {fmt(before[(s, x)])} -> {fmt(after[(s, x)])}"
                          for s, x in after if before[(s, x)] != after[(s, x)])
        return f"{sheet}!{a} = {fmt(self.wb.value(sheet, a))}" + (f" | {delta}" if delta else "")
