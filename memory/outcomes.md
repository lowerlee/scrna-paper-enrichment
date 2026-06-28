# Outcomes

Each entry records what was implemented, what was skipped, and the observed result. Append only.

---

## 2026-05-08 — Replace bioRxiv fetcher with PubMed/NCBI E-utilities
Scope: LARGE
Implemented: Step 1 (pipeline/fetcher.py fully rewritten — esearch+efetch+XML parse, BATCH_SIZE=200, 0.4s rate-limit sleep, pmid: DOI fallback), Step 2 (run_pipeline.py — no bioRxiv log strings found, no edits needed), Step 3 (PIPELINE.md updated by doc agent)
Skipped: None
Feasibility flags: pmid: fallback produces malformed doi.org links in digest (absorbed LOW) — fixed by fixer agent (LOGIC-003, output.py:57)
Lint fixes applied: TYPE-001 (classifier.py:91 — ContentBlock .text guard), LOGIC-003 (output.py:57 — pmid: link exclusion), DEAD-002 (run_pipeline.py:145 — csv_path → _)
Result: All 3 lint issues fixed, 0 skipped. PIPELINE.md updated to reflect PubMed fetch and corrected pre-existing doc inaccuracies (super_category/sub_category fields, CSV row behavior).
