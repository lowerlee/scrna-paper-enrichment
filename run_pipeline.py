#!/usr/bin/env python3
import logging
import os
import sqlite3
import uuid
from datetime import date, timedelta

from pipeline import classifier, fetcher, output

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "pipeline.db")
DIGESTS_DIR = os.path.join(os.path.dirname(__file__), "digests")
LOG_PATH = os.path.join(os.path.dirname(__file__), "logs", "pipeline.log")


def _setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )


def _init_db(con: sqlite3.Connection):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            doi          TEXT PRIMARY KEY,
            title        TEXT,
            abstract     TEXT,
            authors      TEXT,
            category     TEXT,
            fetch_date   TEXT,
            verdict      TEXT,
            confidence   TEXT,
            reason       TEXT,
            prompt_version TEXT,
            run_id       TEXT,
            status       TEXT DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id       TEXT PRIMARY KEY,
            run_date     TEXT,
            fetched      INTEGER,
            deduped      INTEGER,
            relevant     INTEGER,
            not_relevant INTEGER,
            errors       INTEGER
        );
    """)
    con.commit()


def _last_run_date(con: sqlite3.Connection) -> date:
    row = con.execute(
        "SELECT run_date FROM runs ORDER BY run_date DESC LIMIT 1"
    ).fetchone()
    if row:
        return date.fromisoformat(row[0])
    return date.today() - timedelta(days=1)


def run():
    _setup_logging()
    log = logging.getLogger(__name__)

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    _init_db(con)

    run_id = str(uuid.uuid4())
    today = date.today()
    start = _last_run_date(con) + timedelta(days=1)

    log.info("Run %s started. Fetching %s → %s", run_id, start, today)

    papers = fetcher.fetch(start, today)
    log.info("Fetched %d papers", len(papers))

    new_papers = []
    for p in papers:
        exists = con.execute("SELECT 1 FROM papers WHERE doi = ?", (p["doi"],)).fetchone()
        if not exists:
            new_papers.append(p)
            con.execute(
                "INSERT INTO papers (doi, title, abstract, authors, category, fetch_date, status) VALUES (?,?,?,?,?,?,?)",
                (p["doi"], p["title"], p["abstract"], p["authors"], p["category"], p["fetch_date"], "pending"),
            )
    con.commit()
    log.info("New (deduplicated): %d", len(new_papers))

    n_relevant = 0
    n_not_relevant = 0
    n_errors = 0

    for p in new_papers:
        try:
            result = classifier.classify(p["title"], p["abstract"])
            con.execute(
                """UPDATE papers SET verdict=?, confidence=?, reason=?, prompt_version=?, run_id=?, status='classified'
                   WHERE doi=?""",
                (result["verdict"], result["confidence"], result["reason"],
                 result["prompt_version"], run_id, p["doi"]),
            )
            con.commit()
            if result["verdict"] == "RELEVANT":
                n_relevant += 1
            else:
                n_not_relevant += 1
        except Exception as exc:
            log.error("Classification failed for %s: %s", p["doi"], exc)
            n_errors += 1

    log.info("Classified: %d relevant, %d not relevant, %d errors",
             n_relevant, n_not_relevant, n_errors)

    md_path, csv_path = output.write_digest(DB_PATH, run_id, DIGESTS_DIR, today)
    log.info("Digest written: %s", md_path)

    con.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
        (run_id, today.isoformat(), len(papers), len(new_papers),
         n_relevant, n_not_relevant, n_errors),
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    run()
