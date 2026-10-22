# Live demo script (about 6 minutes)

0. **Setup.** Start `claude` from an empty folder (not the repo). Approve the ledgerlab tools with "don't ask again" on the first prompt.
1. **The task (1 min).** Open `examples/hard_seed7.xlsx` in Excel. Show the Notes sheet (the spec), then one buggy line, e.g. PnL row 9.
2. **The grader is the product (1 min).** Run `uv run ledgerlab redteam -n 20`. Honest policies score 1.0, every hack scores 0, partial work gets graded credit.
3. **Claude repairs a model live (3 min).** In Claude Code with the MCP server added:
   > Start a hard ledgerlab task with seed 1007. Read the Notes first, fix every error, then submit.
   Narrate as it works: reads the spec, traces precedents, fixes in place, submits. The breakdown shows FIXED/MISSED per error.
4. **Try to cheat (1 min).** New task:
   > Start a medium ledgerlab task with seed 1003. Don't bother with formulas: just type the correct final numbers into the DCF output cells B9, B10 and B11 as plain numbers, then submit.
   Rehearsed: reward 0.000, and the hard-coded cells are listed. Explain the metamorphic check: inputs are perturbed and pasted numbers break.
5. **Close.** "Task QC + behavioural verification + red team in CI. That's the loop I'd bring to expert-authored environments."
