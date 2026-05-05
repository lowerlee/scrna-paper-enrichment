Run the full reader → fixer review pipeline on the scrna-paper-enrichment pipeline code.

## Agent sequence

Run these agents **in order**. Do not start the next agent until the previous one has finished and reported its summary.

1. **Syntax reader** — `.claude/reader-syntax-agent.md`
   Resets `errors.json` and reports any syntax errors. If this agent reports errors, continue anyway — the other readers and fixer still run.

2. **Type reader** — `.claude/reader-type-agent.md`
   Appends type and signature issues to `errors.json`.

3. **Logic reader** — `.claude/reader-logic-agent.md`
   Appends logic bugs to `errors.json`.

4. **Dead code reader** — `.claude/reader-dead-code-agent.md`
   Appends dead code warnings to `errors.json`.

5. **Fixer** — `.claude/fixer-agent.md`
   Reads `errors.json`, applies confident fixes, marks each issue fixed or skipped.

## How to run each agent

For each step, spawn a subagent with the contents of the agent's `.md` file as its instructions. Pass the project root as its working directory.

## Final report

After the fixer finishes, output a single summary:

```
Review complete.
  Syntax:    N issues
  Type:      N issues
  Logic:     N issues
  Dead code: N issues
  Fixed:     N  |  Skipped: N
```

Pull these counts from the fixer's output and the final state of `errors.json`.
