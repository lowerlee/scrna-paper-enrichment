import requests
from datetime import date, timedelta

BIORXIV_API = "https://api.biorxiv.org/details/biorxiv"
CATEGORIES = ["bioinformatics", "genomics", "systems-biology", "cell-biology"]


def _fetch_category(category: str, start: date, end: date) -> list[dict]:
    papers = []
    cursor = 0
    start_str = start.isoformat()
    end_str = end.isoformat()

    while True:
        url = f"{BIORXIV_API}/{start_str}/{end_str}/{cursor}/json"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        collection = data.get("collection", [])
        if not collection:
            break

        for item in collection:
            if item.get("category", "").lower().replace(" ", "-") != category:
                continue
            papers.append({
                "doi": item.get("doi", ""),
                "title": item.get("title", ""),
                "abstract": item.get("abstract", ""),
                "authors": item.get("authors", ""),
                "category": category,
                "date": item.get("date", ""),
            })

        messages = data.get("messages", [])
        total = int(messages[0].get("total", 0)) if messages else 0
        cursor += len(collection)
        if cursor >= total:
            break

    return papers


def fetch(start: date, end: date | None = None) -> list[dict]:
    """
    Fetch papers from bioRxiv for all configured categories between start and end dates.
    end defaults to today.
    """
    if end is None:
        end = date.today()

    seen_dois: set[str] = set()
    papers = []

    for category in CATEGORIES:
        for paper in _fetch_category(category, start, end):
            doi = paper["doi"]
            if doi and doi not in seen_dois:
                seen_dois.add(doi)
                papers.append(paper)

    return papers
