import logging
import requests
from datetime import date

log = logging.getLogger(__name__)

BIORXIV_API = "https://api.biorxiv.org/details/biorxiv"
CATEGORIES = {"bioinformatics", "genomics", "systems-biology", "cell-biology"}


def _fetch_all(start: date, end: date) -> list[dict]:
    """Paginate through the full bioRxiv date range once and return all items."""
    items = []
    cursor = 0
    start_str = start.isoformat()
    end_str = end.isoformat()

    log.info("Fetching bioRxiv %s → %s", start_str, end_str)

    while True:
        url = f"{BIORXIV_API}/{start_str}/{end_str}/{cursor}/json"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        collection = data.get("collection", [])
        if not collection:
            break

        messages = data.get("messages", [])
        total = int(messages[0].get("total", 0)) if messages else 0
        items.extend(collection)
        cursor += len(collection)
        log.info("  page %d–%d / %d fetched", cursor - len(collection) + 1, cursor, total)
        if cursor >= total:
            break

    log.info("Fetch complete — %d total items", len(items))
    return items


def fetch(start: date, end: date | None = None) -> list[dict]:
    """
    Fetch papers from bioRxiv for all configured categories between start and end dates.
    end defaults to today.
    """
    if end is None:
        end = date.today()

    all_items = _fetch_all(start, end)

    seen_dois: set[str] = set()
    papers = []

    for item in all_items:
        category = item.get("category", "").lower().replace(" ", "-")
        if category not in CATEGORIES:
            continue
        doi = item.get("doi", "")
        if not doi or doi in seen_dois:
            continue
        seen_dois.add(doi)
        papers.append({
            "doi": doi,
            "title": item.get("title", ""),
            "abstract": item.get("abstract", ""),
            "authors": item.get("authors", ""),
            "category": category,
            "date": item.get("date", ""),
        })

    log.info("Kept %d papers across %d categories", len(papers), len(CATEGORIES))
    return papers
