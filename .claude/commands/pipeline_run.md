Run the pipeline doc agent to sync `PIPELINE.md` with the current pipeline source.

## Agent

Spawn a single subagent with the contents of `.claude/pipeline-doc-agent.md` as its instructions. Pass the project root as its working directory.

## Final report

Relay the agent's change summary verbatim. If the agent reports no changes, say so explicitly.
