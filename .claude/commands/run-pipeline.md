Run the pipeline observer agent. Accepts optional `--from YYYY-MM-DD` and `--db PATH` arguments.

## Steps

1. Parse any arguments the user passed to the slash command (e.g. `/run-pipeline --from 2026-04-01 --db data/test.db`).
2. Run `python pipeline_agent.py` with those arguments via Bash, streaming output.
3. When complete, tell the user the path to the generated report and summarize the status line (total time, papers classified, errors).
