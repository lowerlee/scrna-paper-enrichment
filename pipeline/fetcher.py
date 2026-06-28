import logging
import time
import xml.etree.ElementTree as ET
from datetime import date

import requests

log = logging.getLogger(__name__)

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
QUERY = '"Single-Cell Gene Expression Analysis"[MeSH Terms] AND Journal Article[pt]'

BATCH_SIZE = 200


def _esearch(start: date, end: date) -> tuple[str, str, int]:
    """
    Search PubMed for papers in the given date range.
    Returns (web_env, query_key, total_count).
    """
    params = {
        "db": "pubmed",
        "term": QUERY,
        "usehistory": "y",
        "datetype": "pdat",
        "mindate": start.strftime("%Y/%m/%d"),
        "maxdate": end.strftime("%Y/%m/%d"),
        "retmax": 0,
        "retmode": "xml",
    }
    log.info("Searching PubMed %s → %s", start.isoformat(), end.isoformat())
    resp = requests.get(PUBMED_ESEARCH, params=params, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    web_env = root.findtext("WebEnv") or ""
    query_key = root.findtext("QueryKey") or ""
    count_text = root.findtext("Count") or "0"
    total_count = int(count_text)
    log.info("PubMed search found %d records", total_count)
    return web_env, query_key, total_count


def _efetch_batch(web_env: str, query_key: str, retstart: int, retmax: int) -> bytes:
    """
    Fetch a batch of PubMed records in XML format.
    Returns raw XML bytes.
    """
    params = {
        "db": "pubmed",
        "query_key": query_key,
        "WebEnv": web_env,
        "retstart": retstart,
        "retmax": retmax,
        "rettype": "xml",
        "retmode": "xml",
    }
    resp = requests.get(PUBMED_EFETCH, params=params, timeout=60)
    resp.raise_for_status()
    return resp.content


def _parse_article(article_elem: ET.Element) -> dict | None:
    """
    Parse a PubMedArticle XML element into a paper_dict.
    Returns None if title or PMID is missing.
    """
    medline = article_elem.find("MedlineCitation")
    if medline is None:
        return None

    pmid_elem = medline.find("PMID")
    if pmid_elem is None or not (pmid_elem.text or "").strip():
        return None
    pmid = pmid_elem.text.strip()

    article = medline.find("Article")
    if article is None:
        return None

    # Title
    title_elem = article.find("ArticleTitle")
    title = "".join(title_elem.itertext()).strip() if title_elem is not None else ""
    if not title:
        return None

    # Abstract — concatenate all AbstractText elements (handles structured abstracts)
    abstract_parts = []
    abstract_elem = article.find("Abstract")
    if abstract_elem is not None:
        for at in abstract_elem.findall("AbstractText"):
            label = at.get("Label")
            text = "".join(at.itertext()).strip()
            if label and text:
                abstract_parts.append(f"{label}: {text}")
            elif text:
                abstract_parts.append(text)
    abstract = " ".join(abstract_parts)

    # Authors
    author_list_elem = article.find("AuthorList")
    authors_parts = []
    if author_list_elem is not None:
        for author in author_list_elem.findall("Author"):
            last = author.findtext("LastName") or ""
            first = author.findtext("ForeName") or ""
            collective = author.findtext("CollectiveName")
            if collective:
                authors_parts.append(collective.strip())
            elif last:
                name = f"{last} {first}".strip()
                authors_parts.append(name)
    authors = ", ".join(authors_parts)

    # Journal abbreviation (used as category)
    journal = article.findtext("Journal/MedlineTA") or ""
    if not journal:
        journal_elem = medline.find("MedlineJournalInfo")
        if journal_elem is not None:
            journal = journal_elem.findtext("MedlineTA") or ""

    # Publication date
    pub_date_elem = article.find("Journal/JournalIssue/PubDate")
    pub_date = ""
    if pub_date_elem is not None:
        year = pub_date_elem.findtext("Year") or ""
        month = pub_date_elem.findtext("Month") or ""
        day = pub_date_elem.findtext("Day") or ""
        if year and month and day:
            pub_date = f"{year}-{month.zfill(2) if month.isdigit() else month}-{day.zfill(2)}"
        elif year and month:
            pub_date = f"{year}-{month.zfill(2) if month.isdigit() else month}"
        elif year:
            pub_date = year
        else:
            medline_date = pub_date_elem.findtext("MedlineDate") or ""
            if medline_date:
                pub_date = medline_date[:4]

    # DOI — look in PubmedData/ArticleIdList
    doi = ""
    article_id_list = article_elem.find("PubmedData/ArticleIdList")
    if article_id_list is not None:
        for aid in article_id_list.findall("ArticleId"):
            if aid.get("IdType") == "doi":
                doi = (aid.text or "").strip()
                break
    if not doi:
        doi = f"pmid:{pmid}"

    return {
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "category": journal,
        "date": pub_date,
    }


def fetch(start: date, end: date | None = None) -> list[dict]:
    """
    Fetch papers from PubMed for the MeSH term 'Single-Cell Gene Expression Analysis'
    filtered to Journal Articles, between start and end dates.
    end defaults to today.
    """
    if end is None:
        end = date.today()

    web_env, query_key, total_count = _esearch(start, end)
    if total_count == 0:
        log.info("No records found in PubMed for date range %s → %s", start, end)
        return []

    seen_dois: set[str] = set()
    papers = []
    retstart = 0

    while retstart < total_count:
        retmax = min(BATCH_SIZE, total_count - retstart)
        log.info("Fetching batch %d–%d / %d", retstart + 1, retstart + retmax, total_count)
        xml_bytes = _efetch_batch(web_env, query_key, retstart, retmax)
        retstart += retmax

        root = ET.fromstring(xml_bytes)
        for article_elem in root.findall("PubmedArticle"):
            paper = _parse_article(article_elem)
            if paper is None:
                continue
            doi = paper["doi"]
            if doi in seen_dois:
                continue
            seen_dois.add(doi)
            papers.append(paper)

        if retstart < total_count:
            time.sleep(0.4)

    log.info("Fetch complete — %d papers returned", len(papers))
    return papers
