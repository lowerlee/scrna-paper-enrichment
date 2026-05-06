# Pipeline Execution Trace

Technical reference for the execution flow of `run_pipeline.py`. Describes inputs, outputs, and data shapes at each stage. For project goals and design rationale, see `README.md`.

---

## Entry Point

```
python run_pipeline.py
  └── run()
```

---

## Execution Order

### 1. Setup

```
_setup_logging()
  → logs/pipeline.log  (appended)
  → stdout

sqlite3.connect(data/pipeline.db)
_init_db(con)
  → CREATE TABLE IF NOT EXISTS papers
  → CREATE TABLE IF NOT EXISTS runs
```

### 2. Determine Date Range

```
_last_run_date(con)
  → SELECT run_date FROM runs ORDER BY run_date DESC LIMIT 1
  → returns date of last run, or (today - 1 day) if no runs exist

start = last_run_date + 1 day
end   = date.today()
```

### 3. Fetch

```
fetcher.fetch(start, end)
  → iterates CATEGORIES = ["bioinformatics", "genomics", "systems-biology", "cell-biology"]
      _fetch_category(category, start, end)
        → GET https://api.biorxiv.org/details/biorxiv/{start}/{end}/{cursor}/json
        → paginates: cursor advances by len(collection) until cursor >= messages[0].total
        → filters items client-side: item.category.lower().replace(" ", "-") == category
  → cross-category deduplication via seen_dois set, skipping empty DOIs
  → returns List[paper_dict]
```

**paper_dict shape:**
```python
{
    "doi":      str,   # e.g. "10.1101/2024.03.15.585234"
    "title":    str,
    "abstract": str,
    "authors":  str,
    "category": str,   # one of CATEGORIES
    "date":     str,   # ISO date string from API, e.g. "2024-03-15"
}
```

### 4. Deduplicate

```
for paper in papers:
    SELECT 1 FROM papers WHERE doi = ?
    if not exists:
        INSERT INTO papers (doi, title, abstract, authors, category, fetch_date, status)
            VALUES (p["doi"], p["title"], p["abstract"], p["authors"], p["category"], p["date"], "pending")
        → append to new_papers
con.commit()
```

The `fetch_date` column is populated from the `date` field returned by the bioRxiv API.

### 5. Classify

```
for paper in new_papers:
    try:
        classifier.classify(title, abstract)
          → _prompt()
                returns cached (prompt_text, version) from module-level _PROMPT_CACHE
                on first call, delegates to _load_prompt():
                    glob prompts/classifier_v*.txt → sort → take last (highest version)
                    version = filename minus "classifier_" prefix and ".txt" suffix (e.g. "v1")
          → _get_client() lazily instantiates module-level anthropic.Anthropic() client
          → client.messages.create(
                model  = "claude-haiku-4-5-20251001",
                max_tokens = 256,
                system = [{ type: "text", text: prompt_text,
                            cache_control: { type: "ephemeral" } }],
                messages = [{ role: "user", content: "Title: ...\n\nAbstract: ..." }]
            )
          → response.content[0].text.strip()
          → _parse_response(raw_text)
                json.loads → validate verdict ∈ {RELEVANT, NOT_RELEVANT}
                           → validate confidence ∈ {HIGH, MEDIUM, LOW}
                           → validate reason is str
                retries once on JSONDecodeError or ValueError
          → result["prompt_version"] = version
          → returns classifier_result

        UPDATE papers SET verdict, confidence, reason, prompt_version, run_id, status='classified'
            WHERE doi = ?
        con.commit()
        increment n_relevant or n_not_relevant based on verdict
    except Exception:
        log.error(...)
        increment n_errors
        (paper row is left in 'pending' state with no verdict)
```

**classifier_result shape:**
```python
{
    "verdict":        "RELEVANT" | "NOT_RELEVANT",
    "confidence":     "HIGH" | "MEDIUM" | "LOW",
    "reason":         str,   # ≤25 words
    "prompt_version": str,   # e.g. "v1"
}
```

### 6. Write Digest

```
output.write_digest(db_path, run_id, digests/, today)
  → _rows_for_run(db_path, run_id)
        opens its own sqlite3 connection (row_factory = sqlite3.Row)
        SELECT doi, title, abstract, authors, category, fetch_date,
               verdict, confidence, reason
        FROM papers
        WHERE run_id = ? AND verdict = 'RELEVANT'
        ORDER BY CASE confidence WHEN 'HIGH' THEN 0
                                 WHEN 'MEDIUM' THEN 1
                                 WHEN 'LOW' THEN 2
                                 ELSE 3 END,
                 title ASC
        closes the connection, returns list[dict]
  → split rows: main = [HIGH, MEDIUM], borderline = [LOW]
  → ensure out_dir exists (os.makedirs(..., exist_ok=True))
  → write digests/YYYY-MM-DD.md
        # scRNA-seq Methods Digest — YYYY-MM-DD
        **N relevant paper[s]** (M borderline)        ← borderline count shown only if any
        ## Papers                                      ← only if main is non-empty
            for each row: _paper_block(row)
        ## Borderline (LOW confidence — review manually)  ← only if borderline is non-empty
            for each row: _paper_block(row)
        _No relevant papers found in this run._        ← only if rows is empty
  → write digests/YYYY-MM-DD.csv
        csv.DictWriter with fieldnames =
            [doi, title, authors, category, fetch_date, verdict, confidence, reason]
        and extrasaction="ignore" (silently drops the abstract column from rows)
        writes header + every RELEVANT row (HIGH, MEDIUM, and LOW combined)
  → returns (md_path, csv_path)
```

**`_paper_block(row)` formatting:**
```
**[title](https://doi.org/{doi})**          ← link omitted if doi is empty
{category} · {authors[:80]}[…] · {fetch_date}
_{reason}_ `{confidence}`
```

### 7. Log Run

```
INSERT INTO runs (run_id, run_date, fetched, deduped, relevant, not_relevant, errors)
con.close()
```

---

## Pipeline Flow Diagram

```mermaid
flowchart TD
    Start([Run pipeline]) --> Fetch[Fetch new preprints<br/>from bioRxiv API]
    Fetch --> Dedup{Already in DB?}
    Dedup -->|Yes| Skip[Skip]
    Dedup -->|No| Save[Save as 'pending'<br/>in SQLite]
    Save --> Classify[Classify with<br/>Claude Haiku 4.5]
    Classify --> Verdict{Verdict?}
    Verdict -->|RELEVANT| Confidence{Confidence?}
    Verdict -->|NOT_RELEVANT| Store[(Update DB)]
    Confidence -->|HIGH / MEDIUM| Main[Main digest]
    Confidence -->|LOW| Borderline[Borderline section]
    Main --> Digest[/Write daily digest<br/>YYYY-MM-DD.md + .csv/]
    Borderline --> Digest
    Store --> Log[Log run stats]
    Digest --> Log
    Log --> End([Done])

    style Fetch fill:#e1f5ff
    style Classify fill:#fff4e1
    style Digest fill:#e8f5e9
```

---

## Data Store

**`data/pipeline.db`** — SQLite, two tables:

`papers`

| column         | type | notes                              |
|----------------|------|------------------------------------|
| doi            | TEXT | PRIMARY KEY                        |
| title          | TEXT |                                    |
| abstract       | TEXT |                                    |
| authors        | TEXT |                                    |
| category       | TEXT |                                    |
| fetch_date     | TEXT | ISO date — populated from bioRxiv `date` field |
| verdict        | TEXT | RELEVANT \| NOT_RELEVANT           |
| confidence     | TEXT | HIGH \| MEDIUM \| LOW              |
| reason         | TEXT | ≤25 words from classifier          |
| prompt_version | TEXT | e.g. v1                            |
| run_id         | TEXT | UUID, matches runs.run_id          |
| status         | TEXT | pending → classified               |

`runs`

| column       | type    | notes          |
|--------------|---------|----------------|
| run_id       | TEXT    | PRIMARY KEY    |
| run_date     | TEXT    | ISO date       |
| fetched      | INTEGER |                |
| deduped      | INTEGER | new papers only |
| relevant     | INTEGER |                |
| not_relevant | INTEGER |                |
| errors       | INTEGER |                |

---

## File I/O Summary

| stage      | reads                              | writes                          |
|------------|------------------------------------|---------------------------------|
| setup      | —                                  | logs/pipeline.log               |
| fetch      | bioRxiv API (HTTP)                 | —                               |
| deduplicate| data/pipeline.db                   | data/pipeline.db (papers rows)  |
| classify   | prompts/classifier_v*.txt, Anthropic API | data/pipeline.db (papers rows) |
| digest     | data/pipeline.db                   | digests/YYYY-MM-DD.md + .csv    |
| log run    | —                                  | data/pipeline.db (runs row)     |

---

## Project Structure

```
scrna-paper-enrichment/
├── run_pipeline.py           # Entry point — orchestrates all stages
├── validate.py               # Runs classifier against ground-truth fixtures
├── pipeline/
│   ├── __init__.py
│   ├── fetcher.py            # bioRxiv API client, pagination, cross-category dedup
│   ├── classifier.py         # Haiku 4.5 wrapper, prompt loader, JSON parser
│   ├── output.py             # Markdown + CSV digest writer
│   └── pdf.py                # PDF helper used by validate.py
├── prompts/
│   └── classifier_v*.txt     # Versioned classifier system prompts
├── data/
│   └── pipeline.db           # SQLite — papers, verdicts, run log (created at runtime)
├── digests/                  # Daily output: YYYY-MM-DD.md + YYYY-MM-DD.csv (created at runtime)
├── logs/
│   └── pipeline.log          # Created at runtime
├── .claude/                  # Agent definitions and slash commands
├── errors.json               # Validation error log
└── shell.nix                 # Nix dev shell
```
