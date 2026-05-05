"""PDF text extraction shared by validate.py and any future pipeline stage that needs full-text."""
import re


def extract_abstract(pdf_path: str) -> str:
    """
    Extract the abstract from a PDF. Returns the abstract text, or the first
    2000 characters of the document if no abstract section is detected.
    """
    text = _extract_text(pdf_path)
    return _find_abstract(text)


def _extract_text(pdf_path: str) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        return pdfminer_extract(pdf_path)
    except ImportError:
        pass
    raise RuntimeError(
        "No PDF library found. Install pypdf or pdfminer.six:\n"
        "  pip install pypdf\n  pip install pdfminer.six"
    )


def _find_abstract(text: str) -> str:
    # Look for an "Abstract" heading followed by text up to the next section heading
    match = re.search(
        r"\bAbstract\b[:\s]*\n?(.*?)(?=\n\s*\n\s*[A-Z][A-Za-z ]+\n|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        abstract = match.group(1).strip()
        if len(abstract) > 100:
            return abstract[:4000]
    return text[:2000]
