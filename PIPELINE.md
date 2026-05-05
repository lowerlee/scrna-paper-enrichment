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
        → filters items client-side by category field
  → cross-category deduplication via seen_dois set
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
        INSERT INTO papers (doi, title, abstract, authors, category, fetch_date, status='pending')
        → append to new_papers
con.commit()
```

> **Known issue:** `fetcher.fetch()` returns key `"date"` but the INSERT reads `p["fetch_date"]`. This raises a `KeyError` at runtime. The `fetch_date` column in SQLite will not be populated until this is resolved.

### 5. Classify

```
for paper in new_papers:
    classifier.classify(title, abstract)
      → _prompt()
            glob prompts/classifier_v*.txt → sort → take last (highest version)
            cache result in module-level _PROMPT_CACHE (process lifetime)
            returns (prompt_text, version_string)
      → client.messages.create(
            model  = "claude-haiku-4-5-20251001",
            max_tokens = 256,
            system = [{ text: prompt_text, cache_control: { type: "ephemeral" } }],
            messages = [{ role: "user", content: "Title: ...\n\nAbstract: ..." }]
        )
      → _parse_response(raw_text)
            json.loads → validate verdict ∈ {RELEVANT, NOT_RELEVANT}
                       → validate confidence ∈ {HIGH, MEDIUM, LOW}
                       → validate reason is str
            retries once on JSONDecodeError or ValueError
      → returns classifier_result

    UPDATE papers SET verdict, confidence, reason, prompt_version, run_id, status='classified'
        WHERE doi = ?
    con.commit()
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
        SELECT doi, title, abstract, authors, category, fetch_date,
               verdict, confidence, reason
        FROM papers
        WHERE run_id = ? AND verdict = 'RELEVANT'
        ORDER BY confidence DESC, title ASC
  → split rows: main = [HIGH, MEDIUM], borderline = [LOW]
  → write digests/YYYY-MM-DD.md
        ## Papers          ← main section
        ## Borderline      ← borderline section (omitted if empty)
  → write digests/YYYY-MM-DD.csv
        fields: doi, title, authors, category, fetch_date, verdict, confidence, reason
  → returns (md_path, csv_path)
```

### 7. Log Run

```
INSERT INTO runs (run_id, run_date, fetched, deduped, relevant, not_relevant, errors)
con.close()
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
| fetch_date     | TEXT | ISO date — currently unpopulated (see §4) |
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
├── validate.py               # Runs classifier against test-data/ ground truth
├── pipeline/
│   ├── fetcher.py            # bioRxiv API client, pagination, cross-category dedup
│   ├── classifier.py         # Haiku 4.5 wrapper, prompt loader, JSON parser
│   └── output.py             # Markdown + CSV digest writer
├── prompts/
│   └── classifier_v*.txt     # Versioned classifier system prompts
├── data/
│   └── pipeline.db           # SQLite — papers, verdicts, run log
├── digests/                  # Daily output: YYYY-MM-DD.md + YYYY-MM-DD.csv
├── logs/
│   └── pipeline.log
└── test-data/                # Validation PDFs + ground_truth.json
```
