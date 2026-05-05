import csv
import os
import sqlite3
from datetime import date


def _rows_for_run(db_path: str, run_id: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT doi, title, abstract, authors, category, fetch_date,
               verdict, confidence, reason
        FROM papers
        WHERE run_id = ? AND verdict = 'RELEVANT'
        ORDER BY CASE confidence WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 WHEN 'LOW' THEN 2 ELSE 3 END, title ASC
        """,
        (run_id,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def _paper_block(p: dict) -> str:
    doi_url = f"https://doi.org/{p['doi']}" if p["doi"] else ""
    title_line = f"**[{p['title']}]({doi_url})**" if doi_url else f"**{p['title']}**"
    meta = f"{p['category']} · {p['authors'][:80]}{'…' if len(p.get('authors','')) > 80 else ''} · {p['fetch_date']}"
    reason = p.get("reason", "")
    confidence = p.get("confidence", "")
    return f"{title_line}\n{meta}\n_{reason}_ `{confidence}`\n"


def write_digest(db_path: str, run_id: str, out_dir: str, run_date: date | None = None) -> tuple[str, str]:
    """
    Write markdown and CSV digests for a completed run.
    Returns (md_path, csv_path).
    """
    if run_date is None:
        run_date = date.today()

    rows = _rows_for_run(db_path, run_id)
    os.makedirs(out_dir, exist_ok=True)
    date_str = run_date.isoformat()
    md_path = os.path.join(out_dir, f"{date_str}.md")
    csv_path = os.path.join(out_dir, f"{date_str}.csv")

    main = [r for r in rows if r["confidence"] in ("HIGH", "MEDIUM")]
    borderline = [r for r in rows if r["confidence"] == "LOW"]

    with open(md_path, "w") as f:
        f.write(f"# scRNA-seq Methods Digest — {date_str}\n\n")
        f.write(f"**{len(rows)} relevant paper{'s' if len(rows) != 1 else ''}**")
        if borderline:
            f.write(f" ({len(borderline)} borderline)")
        f.write("\n\n")

        if main:
            f.write("## Papers\n\n")
            for p in main:
                f.write(_paper_block(p) + "\n")

        if borderline:
            f.write("## Borderline (LOW confidence — review manually)\n\n")
            for p in borderline:
                f.write(_paper_block(p) + "\n")

        if not rows:
            f.write("_No relevant papers found in this run._\n")

    fieldnames = ["doi", "title", "authors", "category", "fetch_date", "verdict", "confidence", "reason"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return md_path, csv_path
