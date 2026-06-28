# Architecture Decisions

Each entry records a key decision, the date it was made, and the reasoning behind it. Append only — never remove or revise past entries.

---

## 2026-05-08 — Replace bioRxiv with PubMed as sole fetch source
Decision: Use NCBI E-utilities API (PubMed) exclusively, replacing the bioRxiv API entirely.
Why: PubMed is more comprehensive — it includes published versions of papers that also appear as bioRxiv preprints, so fetching from both would create duplicates without adding coverage. User preferred the single authoritative source over dual-source complexity.
