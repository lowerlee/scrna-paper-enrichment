# scRNA-seq Methods Paper Discovery

A daily automated digest that surfaces methodology-focused papers for single-cell RNA sequencing and related omics technologies from preprint and journal sources.

## Goal

Surface papers that **develop, optimize, benchmark, or build computational tools for** scRNA-seq and omics technologies — not papers that merely *apply* these technologies to answer biology questions.

**Include:**
- New algorithms, tools, or software packages for scRNA-seq/omics analysis
- Optimizations of existing computational methods (speed, accuracy, scalability)
- Benchmarking studies comparing tools or methods
- New experimental protocols or sequencing technologies
- New statistical models designed for omics data
- New atlas omics datasets

**Exclude:**
- Papers using scRNA-seq as a measurement tool to study a gene, disease, or cell type
- Papers where the biology question is the primary contribution
- Clinical papers that happen to use sequencing data
- Papers completely unrelated to scRNA-seq or any omic technology

The hard boundary is between *develops a method* and *applies a method*. The modal paper on bioRxiv that mentions scRNA-seq is an application paper ("we performed scRNA-seq on tissue X and identified population Y"), and rejecting that class correctly is the central job of this pipeline.

## System Overview

This is **not** a trained classifier. It is a deterministic pipeline whose final output is a binary RELEVANT / NOT_RELEVANT verdict for each candidate paper. The decision logic is a single **Haiku 4.5 API call** per paper, with a versioned prompt that encodes the include/exclude criteria above.

No model weights are fitted from data. Labeled examples are used only to regression-test the prompt and measure end-to-end precision and recall.

## Pipeline Stages

End-to-end flow for a single daily run:

1. **Fetch.** Hit the bioRxiv API for papers posted since the last successful run, across configured categories (bioinformatics, genomics, systems biology, cell biology). Returns metadata only: title, abstract, authors, DOI, category, date.
2. **Deduplicate.** For each paper, check DOI against `data/pipeline.db`. Drop papers already seen (handles bioRxiv revisions and reposts). Insert new papers with status `pending`.
3. **Classify.** For each new paper, call Haiku 4.5 with a versioned prompt requesting structured JSON: `{verdict, confidence, reason}`. Parse, validate against schema, retry once on malformed JSON, then write the result to SQLite alongside the prompt version used.
4. **Write digest.** Query SQLite for papers classified `RELEVANT` in this run. Split by confidence: HIGH/MEDIUM in the main section, LOW in the borderline section. Write `digests/YYYY-MM-DD.md` and `.csv`.
5. **Log.** Counts at each stage (fetched, deduplicated, classified RELEVANT/NOT_RELEVANT), API errors, and JSON parse failures go to `logs/pipeline.log`.

A typical run: 50 papers fetched → 12 already seen → 38 Haiku calls → 4 relevant (3 HIGH, 1 LOW), 34 not relevant. The digest shows 4 papers; SQLite gains 38 rows.

## Haiku Classification

The classifier is a single Haiku 4.5 call per deduplicated paper. The prompt:

- States the include/exclude criteria from this README verbatim.
- Includes four worked examples (easy positive, hard atlas positive, hard application negative, obvious off-topic negative) probing the actual decision boundary.
- Encodes the decision rule: *would this paper be cited primarily for the tool/method, or primarily for the biological finding?* (cf. Luecken et al. 2022, *Nature Methods*, scIB benchmark; Saelens et al. 2019, *Nature Biotechnology*, on trajectory inference benchmarking).
- Requests JSON output: `{"verdict": "RELEVANT"|"NOT_RELEVANT", "confidence": "HIGH"|"MEDIUM"|"LOW", "reason": "<one sentence, max 25 words>"}`.

The system prompt is sent with `cache_control: ephemeral`, so only the first call in a 5-minute window pays full input rate; subsequent calls pay 10%. At ~50 papers/day the total cost is roughly $1–3/month.

**Prompt versioning.** The prompt lives in `prompts/classifier_v*.txt`. Every classification result in SQLite records the prompt version used. When the prompt changes, the version increments — this lets us measure whether a prompt revision actually improved decisions without re-running everything. Always re-run validation after editing the prompt; never edit the prompt and the test set in the same change.

**Confidence calibration is not assumed.** LLM-reported HIGH/MEDIUM/LOW confidence is known to be poorly calibrated (Tian et al. 2023, "Just Ask for Calibration"). The borderline-section design is a hedge, not a trusted signal. Once the validation set exists, the empirical precision of each confidence bucket is checked; if LOW is not actually less precise than HIGH, the confidence field is replaced with a two-call agreement signal (run the classifier twice at `temperature=0.7`, treat disagreement as the uncertainty marker).

## Output

Each daily digest contains, per paper:

- Title and source link
- Category, authors, date
- One-sentence reason for inclusion (from Claude)
- Confidence level (HIGH / MEDIUM / LOW)
- A separate "borderline" section for LOW-confidence results to review manually

CSV mirrors the markdown for downstream tooling.

## Validation

`validate.py` runs the classify stage against a labeled test set in `test-data/` and compares verdicts against `ground_truth.json`. Two quantities are measured:

1. **End-to-end precision and recall** — verdicts vs. ground truth labels.
2. **Per-confidence-bucket precision** — to check whether HIGH/MEDIUM/LOW is actually informative.

### Test set composition

The original five-paper test set was insufficient — four "obvious" negatives (VSV structural biology, neoantigen vaccines, immunopeptidomics, engineered cell entry) and one positive. It probed the easy boundary, not the actual decision boundary.

The target test set is **~30 positives + ~30 hard negatives**:

**Positives (~30)** spanning the include categories:
- Algorithms / software packages (~8)
- Benchmarking studies (~6)
- Experimental protocols and new sequencing technologies (~6)
- Statistical models for omics data (~5)
- Atlas datasets (~5)

**Hard negatives (~30)** — application papers that *use* scRNA-seq prominently as a measurement tool:
- "We performed scRNA-seq on tissue X in condition Y and identified population Z" (~15)
- Clinical/disease papers where scRNA-seq is one of several methods (~10)
- Application papers that borrow methodology vocabulary in the abstract (~5)

Plus a small number (~5) of obvious off-topic negatives for sanity.

### Ground truth schema

`test-data/ground_truth.json` is the labeled set `validate.py` runs against. Each entry carries enough information to compute the metrics above and to make failure logs diagnosable.

```json
{
  "schema_version": 1,
  "created": "2026-05-02",
  "notes": "Hard negatives drawn from bioRxiv bioinformatics + cell biology, Jan-Mar 2026.",
  "papers": [
    {
      "id": "10.1101/2024.03.15.585234",
      "title": "scIntegrate: a scalable benchmark for single-cell data integration methods",
      "source_path": "test-data/positives/benchmarking/scintegrate.pdf",
      "label": "RELEVANT",
      "category": "benchmarking",
      "difficulty": "easy",
      "rationale": "Explicit benchmark of integration tools with new evaluation metric."
    },
    {
      "id": "10.1101/2024.04.02.587891",
      "title": "Single-cell atlas of microglial heterogeneity in Alzheimer's disease",
      "source_path": "test-data/hard_negatives/applications/microglia_ad.pdf",
      "label": "NOT_RELEVANT",
      "category": "application_tissue_finding",
      "difficulty": "hard",
      "rationale": "Uses scRNA-seq as a measurement tool; contribution is biological."
    }
  ]
}
```

Field reference:

- `id` — paper identifier matching what the pipeline emits (DOI for bioRxiv: `10.1101/...`; prefixed for other sources: `arxiv:2403.12345`, `pmid:38765432`). String equality with pipeline output is the matching rule.
- `title` — redundant with the PDF, included so failure logs are human-readable ("FAIL: scIntegrate" vs. "FAIL: 10.1101/2024.03.15.585234").
- `source_path` — relative path to the PDF. `validate.py` extracts the abstract from this file using the same extraction code the production pipeline uses, so changes to extraction are also tested.
- `label` — `"RELEVANT"` or `"NOT_RELEVANT"`, matching pipeline output strings exactly.
- `category` — one of `algorithms`, `benchmarking`, `protocol`, `statistical_model`, `atlas` (positives); `application_tissue_finding`, `application_clinical`, `application_methodology_vocabulary`, `obvious_offtopic` (negatives). Enables per-category breakdowns — "0/5 atlas papers correct" is more actionable than "23/30 positives correct."
- `difficulty` — `easy` or `hard`. Recall conditional on difficulty distinguishes a pipeline that's improving on the cases that matter from one that's only winning on easy cases.
- `rationale` — one-sentence explanation of the label. Forces consistency at labeling time and documents borderline calls for future review.

Top-level `schema_version`, `created`, and `notes` track provenance. Bump `schema_version` when fields change so a stale `validate.py` against a new file fails loudly rather than silently.

**Labeling rule.** Apply the operational criterion from the classifier prompt: *would this paper be cited primarily for the tool/method, or primarily for the biological finding?* Hand-labeling by gut feel produces inconsistencies that defeat the test set's purpose.

**Do not store extracted abstracts in ground truth.** Always extract from the PDF the same way the pipeline would. Storing pre-extracted text means changes to extraction code are no longer tested.

The daily timer is not enabled until the test set is built and the pipeline reports ≥95% recall on labeled positives.

## Project Structure

```
scrna-paper-enrichment/
├── shell.nix                 # Nix environment (python3, requests, anthropic)
├── run_pipeline.py           # Daily entry point
├── validate.py               # Test set validation
├── pipeline/
│   ├── fetcher.py            # bioRxiv API client + pagination
│   ├── classifier.py         # Haiku 4.5 wrapper, JSON parser, prompt versioning
│   └── output.py             # Markdown + CSV writer
├── prompts/
│   └── classifier_v*.txt     # Versioned prompt files
├── data/pipeline.db          # SQLite: papers, verdicts, prompt versions, run log
├── digests/                  # Daily output files (YYYY-MM-DD.md + .csv)
├── logs/pipeline.log
└── test-data/                # Validation PDFs + ground_truth.json
```

### SQLite schema (decisions, not just IDs)

The `papers` table stores, per paper: DOI, title, abstract, fetch date, verdict, confidence, reason, prompt version, run ID. This makes it cheap to ask retrospective questions like "how many papers flipped between prompt v3 and v4?" and "what's the confidence distribution for NOT_RELEVANT papers?" without re-running the pipeline.

## Scheduling

Runs daily at 07:00 via a systemd user timer with `Persistent=true` so missed runs are caught up on after reboots.

## Future Work

- **arXiv as a near-term source, not a future one.** A substantial fraction of computational scRNA-seq methodology — particularly ML-flavored work like foundation models for single-cell (scGPT, Geneformer, scFoundation) — appears first on arXiv under `cs.LG`, `q-bio.QM`, or `stat.ML`. bioRxiv-only coverage is systematically biased toward wet-lab and traditional bioinformatics methods over deep-learning ones. This is being promoted from "future" to a phase-two priority.
- **Other sources.** Europe PMC, PubMed for peer-reviewed methods papers, eLife.
- **Abstract vs. full text.** Benchmark whether abstract-only classification is meaningfully worse than fetching the full PDF. Methods papers usually announce the contribution explicitly in the abstract ("we present X"), so abstract-only is probably nearly as good and much cheaper — but worth confirming with data.
- **Escalation tier.** If Haiku's error rate on hard cases turns out to be unacceptable, add Sonnet as a fallback for LOW-confidence verdicts. At current volume this adds ~$0.01/day. Add it only after measuring Haiku's actual error rate, not preemptively.
- **Embedding pre-filter.** After ~30 days of confirmed-relevant abstracts accumulate, layer in a SPECTER2-based pre-filter (Singh et al. 2023) that scores by cosine similarity to the centroid of confirmed positives. Captures semantic similarity rather than lexical overlap, so methods papers with unusual phrasing score correctly.