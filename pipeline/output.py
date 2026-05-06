import csv
import os
import sqlite3
from collections import defaultdict
from datetime import date

SUPER_CATEGORY_ORDER = ["computational", "experimental", "resource"]

SUPER_CATEGORY_LABELS = {
    "computational": "Computational",
    "experimental": "Experimental",
    "resource": "Resource",
}

SUB_CATEGORY_LABELS = {
    "new_algorithm": "New Algorithm",
    "software_tool": "Software Tool",
    "benchmark": "Benchmark",
    "foundation_model": "Foundation Model",
    "new_technology": "New Technology",
    "new_platform": "New Platform",
    "protocol_optimization": "Protocol Optimization",
    "atlas": "Atlas",
    "dataset": "Dataset",
    "infrastructure": "Infrastructure",
}

# Sub-category order within each super-category
SUB_CATEGORY_ORDER = {
    "computational": ["new_algorithm", "software_tool", "benchmark", "foundation_model"],
    "experimental": ["new_technology", "new_platform", "protocol_optimization"],
    "resource": ["atlas", "dataset", "infrastructure"],
}


def _rows_for_run(db_path: str, run_id: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT doi, title, abstract, authors, category, fetch_date,
               verdict, confidence, reason, super_category, sub_category
        FROM papers
        WHERE run_id = ?
        ORDER BY
            CASE verdict WHEN 'RELEVANT' THEN 0 ELSE 1 END,
            CASE confidence WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 WHEN 'LOW' THEN 2 ELSE 3 END,
            title ASC
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

    relevant = [r for r in rows if r["verdict"] == "RELEVANT"]
    main = [r for r in relevant if r["confidence"] in ("HIGH", "MEDIUM")]
    borderline = [r for r in relevant if r["confidence"] == "LOW"]

    with open(md_path, "w") as f:
        f.write(f"# scRNA-seq Methods Digest — {date_str}\n\n")
        f.write(f"**{len(relevant)} relevant paper{'s' if len(relevant) != 1 else ''}**")
        if borderline:
            f.write(f" ({len(borderline)} borderline)")
        f.write("\n\n")

        # Group main papers by super_category > sub_category
        by_super: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        uncategorized = []
        for p in main:
            sc = p.get("super_category")
            sub = p.get("sub_category")
            if sc and sub:
                by_super[sc][sub].append(p)
            else:
                uncategorized.append(p)

        for super_cat in SUPER_CATEGORY_ORDER:
            if super_cat not in by_super:
                continue
            f.write(f"## {SUPER_CATEGORY_LABELS[super_cat]}\n\n")
            for sub_cat in SUB_CATEGORY_ORDER[super_cat]:
                papers_in_sub = by_super[super_cat].get(sub_cat, [])
                if not papers_in_sub:
                    continue
                f.write(f"### {SUB_CATEGORY_LABELS[sub_cat]}\n\n")
                for p in papers_in_sub:
                    f.write(_paper_block(p) + "\n")

        if uncategorized:
            f.write("## Uncategorized\n\n")
            for p in uncategorized:
                f.write(_paper_block(p) + "\n")

        if borderline:
            f.write("## Borderline (LOW confidence — review manually)\n\n")
            for p in borderline:
                f.write(_paper_block(p) + "\n")

        if not relevant:
            f.write("_No relevant papers found in this run._\n")

    fieldnames = [
        "doi", "title", "authors", "category", "fetch_date",
        "verdict", "confidence", "super_category", "sub_category", "reason",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return md_path, csv_path
