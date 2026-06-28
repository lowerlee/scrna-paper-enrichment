# Plan: Replace bioRxiv fetcher with PubMed/NCBI E-utilities
Date: 2026-05-08
Status: COMPLETE

## What this changes
Replaces the bioRxiv API fetch logic in `pipeline/fetcher.py` with a PubMed/NCBI
E-utilities fetch, using the MeSH term `"Single-Cell Gene Expression Analysis"` filtered
to research articles only (publication type: Journal Article). The public `fetch(start, end)`
interface and `paper_dict` shape are preserved, so the classifier, output, and DB stages
are untouched.

## Scope assessment

- `pipeline/fetcher.py` — complete rewrite: remove bioRxiv constants/logic, add NCBI
  E-utilities search+fetch+XML-parse logic; keep same `fetch()` signature and `paper_dict` shape
- `run_pipeline.py` — minor: update any log strings or comments that reference "bioRxiv"
- `PIPELINE.md` — update fetch stage description, data shape notes, and flow diagram to
  reflect PubMed as the source

## Implementation steps

- [ ] Step 1: Rewrite `pipeline/fetcher.py`
  - Remove `BIORXIV_API` and `CATEGORIES` constants
  - Add `PUBMED_ESEARCH`, `PUBMED_EFETCH` URL constants and `QUERY` constant:
    `'"Single-Cell Gene Expression Analysis"[MeSH Terms] AND Journal Article[pt]'`
  - Add `_esearch(start, end)` helper:
    - POST/GET to esearch with `db=pubmed`, `usehistory=y`, `datetype=pdat`,
      `mindate=YYYY/MM/DD`, `maxdate=YYYY/MM/DD`, `retmax=0`
    - Returns `(web_env, query_key, total_count)`
  - Add `_efetch_batch(web_env, query_key, retstart, retmax)` helper:
    - GET efetch with `rettype=xml&retmode=xml`, `retstart`, `retmax`
    - Returns raw XML bytes
  - Add `_parse_article(article_elem)` helper:
    - Extracts: PMID, DOI (from ArticleId[@IdType='doi']), title (ArticleTitle),
      abstract (all AbstractText elements concatenated), authors (Last + First joined,
      CollectiveName fallback), journal (MedlineTA), publication date (PubDate Year/Month/Day
      or MedlineDate first 4 chars for year)
    - Returns `paper_dict` or `None` if title or PMID missing
    - Uses `pmid:{pmid}` as `doi` fallback when no DOI present
  - Rewrite `fetch(start, end)`:
    - Call `_esearch` to get WebEnv/query_key/count
    - Paginate `_efetch_batch` in batches of 200, adding `time.sleep(0.4)` between
      requests to respect NCBI rate limit (3 req/sec without API key)
    - Parse each batch with `xml.etree.ElementTree`, call `_parse_article` per article
    - Deduplicate by `doi` field using `seen_dois` set (same pattern as current code)
    - Return `list[paper_dict]`

- [ ] Step 2: Update `run_pipeline.py`
  - Replace any log message text that says "bioRxiv" with "PubMed"
  - No logic changes expected

- [ ] Step 3: Update `PIPELINE.md`
  - Update fetch stage: URL, pagination approach, field mapping
  - Update the `paper_dict` `category` field note (now = journal abbreviation, e.g. "Nat Methods")
  - Update flow diagram label ("from PubMed API")
  - Update File I/O table fetch row

## Risks

- **PubMed XML complexity**: `AbstractText` elements can have `Label` attributes
  (Background, Methods, etc.) for structured abstracts — concatenation must handle this
  cleanly or the classifier sees garbled text. `_parse_article` must strip/join correctly.
- **Papers without DOIs**: older or some publisher records lack a DOI in PubMed. The
  `pmid:{pmid}` fallback ensures they're stored, but they won't deduplicate against a
  bioRxiv DOI for the same paper (minor — the bioRxiv DB is being replaced anyway).
- **NCBI rate limits**: without an API key, NCBI allows 3 req/sec. The 0.4s sleep between
  efetch batches covers this. Large date ranges (e.g., first run on a fresh DB) could be slow.
- **Date range semantics**: `datetype=pdat` uses the journal publication date. If a paper's
  PubMed record is created after its publication date, it may not appear in the expected
  date window. This is consistent with how bioRxiv's `date` field worked.
- **`category` field content change**: digest output currently shows bioRxiv category names
  (e.g., "bioinformatics"). After this change it will show journal abbreviations (e.g.,
  "Nat Methods", "Genome Biol"). Cosmetic only — no logic depends on specific category values.

## Out of scope

- NCBI API key support (can be added later to raise rate limit to 10 req/sec)
- Retaining bioRxiv as a fallback or dual source
- Any change to `classifier.py`, `output.py`, or the classifier prompt
- DB schema changes (no new columns)
- Migrating existing papers in `data/pipeline.db` (existing rows remain; new runs fetch from PubMed)
