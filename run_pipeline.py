#!/usr/bin/env python3
import argparse
import logging
import os
import sqlite3
import uuid
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv(override=True)

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
            doi            TEXT PRIMARY KEY,
            title          TEXT,
            abstract       TEXT,
            authors        TEXT,
            category       TEXT,
            fetch_date     TEXT,
            verdict        TEXT,
            confidence     TEXT,
            reason         TEXT,
            prompt_version TEXT,
            run_id         TEXT,
            status         TEXT DEFAULT 'pending',
            super_category TEXT,
            sub_category   TEXT
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
    # Migration for databases created before super_category/sub_category were added
    for col in ("super_category", "sub_category"):
        try:
            con.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    con.commit()


def _last_run_date(con: sqlite3.Connection) -> date:
    row = con.execute(
        "SELECT run_date FROM runs ORDER BY run_date DESC LIMIT 1"
    ).fetchone()
    if row:
        return date.fromisoformat(row[0])
    return date.today() - timedelta(days=1)


def run(from_date: str | None = None, db_path: str | None = None, force: bool = False):
    _setup_logging()
    log = logging.getLogger(__name__)

    resolved_db = db_path or DB_PATH
    os.makedirs(os.path.dirname(resolved_db), exist_ok=True)
    con = sqlite3.connect(resolved_db)
    _init_db(con)

    run_id = str(uuid.uuid4())
    today = date.today()
    start = date.fromisoformat(from_date) if from_date else _last_run_date(con) + timedelta(days=1)

    log.info("Run %s started. Fetching %s → %s", run_id, start, today)

    papers = fetcher.fetch(start, today)
    log.info("Fetched %d papers", len(papers))

    new_papers = []
    for p in papers:
        exists = con.execute("SELECT 1 FROM papers WHERE doi = ?", (p["doi"],)).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO papers (doi, title, abstract, authors, category, fetch_date, status) VALUES (?,?,?,?,?,?,?)",
                (p["doi"], p["title"], p["abstract"], p["authors"], p["category"], p["date"], "pending"),
            )
            new_papers.append(p)
        elif force:
            new_papers.append(p)
    con.commit()
    n_skipped = len(papers) - len(new_papers)
    log.info("New: %d  |  Already in DB (skipped): %d", len(new_papers), n_skipped)

    n_relevant = 0
    n_not_relevant = 0
    n_errors = 0

    classifiable = [p for p in new_papers if p.get("abstract", "").strip()]
    n_skipped_abstract = len(new_papers) - len(classifiable)
    if n_skipped_abstract:
        log.warning("Skipping %d papers with empty abstracts", n_skipped_abstract)

    total = len(classifiable)
    for i, p in enumerate(classifiable, 1):
        log.info("Classifying [%d/%d] %s", i, total, p["title"][:80])
        try:
            result = classifier.classify(p["title"], p["abstract"])
            con.execute(
                """UPDATE papers SET verdict=?, confidence=?, reason=?, prompt_version=?,
                   run_id=?, status='classified', super_category=?, sub_category=?
                   WHERE doi=?""",
                (result["verdict"], result["confidence"], result["reason"],
                 result["prompt_version"], run_id,
                 result.get("super_category"), result.get("sub_category"),
                 p["doi"]),
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

    md_path, _ = output.write_digest(resolved_db, run_id, DIGESTS_DIR, today)
    log.info("Digest written: %s", md_path)

    con.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
        (run_id, today.isoformat(), len(papers), len(new_papers),
         n_relevant, n_not_relevant, n_errors),
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                        help="Start date for fetching papers (overrides last-run detection)")
    parser.add_argument("--db", metavar="PATH",
                        help="Path to SQLite database (default: data/pipeline.db)")
    parser.add_argument("--force", action="store_true",
                        help="Reclassify papers already in the database")
    args = parser.parse_args()
    run(from_date=args.from_date, db_path=args.db, force=args.force)
