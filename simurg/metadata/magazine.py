"""Magazine metadata: filename decode + build/validate for Simurg magazines.

Magazines carry no reliable in-file metadata, so the canonical title and issue
identity (date / number / volume) are parsed from the filename. Scrapers then
fill publication-level metadata (ISSN, publisher, country, frequency, cover).
"""

from __future__ import annotations

import re

from simurg.metadata.combine import (  # noqa: F401 (detect_edition kept for parity)
    clean_tags,
    detect_edition,
)
from simurg.metadata.scrapers.util import (
    clean_issn,
    normalize_issue_date,
    year_from,
)
from simurg.metadata.scrapers.util import (
    issue_label as _issue_label,
)

MONTH_WORDS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

MAGAZINE_TYPE = "Magazines"


def decode_magazine_filename(filepath) -> dict:
    """Parse a magazine filename into an inbuilt-style metadata dict.

    Handles "Title - Month Year", "Title - Vol X Issue Y", "Title (Year)",
    "Title - Month Year Vol X Issue Y", etc. Returns keys: canonical_title,
    title, format, type, issue_date, issue_date_precision, issue_number,
    volume, year.
    """
    from pathlib import Path

    path = Path(filepath)
    stem = path.stem.strip()
    ext = path.suffix.lower()
    fmt = ext.lstrip(".").upper()

    # Split canonical title from the issue-identity portion, if a dash exists.
    title_part = stem
    issue_part = ""
    if " - " in stem:
        left, right = stem.split(" - ", 1)
        title_part = left.strip()
        issue_part = right.strip()
    elif " – " in stem:
        left, right = stem.split(" – ", 1)
        title_part = left.strip()
        issue_part = right.strip()

    issue_date = None
    precision = None
    issue_number = None
    volume = None
    year = None

    if issue_part:
        # Date: "June 2020", "June 15, 2020", "2020-06", "2020"
        m = re.search(r"(\d{4}-\d{2}(?:-\d{2})?)", issue_part)
        if m:
            issue_date, precision = normalize_issue_date(m.group(1))
        else:
            m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", issue_part)
            if m:
                mo = MONTH_WORDS.get(m.group(1).lower())
                if mo:
                    issue_date, precision = f"{m.group(3)}-{mo:02d}-{int(m.group(2)):02d}", "day"
            if not issue_date:
                m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{4})", issue_part)
                if m:
                    mo = MONTH_WORDS.get(m.group(1).lower())
                    if mo:
                        issue_date, precision = f"{m.group(2)}-{mo:02d}", "month"
        if not issue_date:
            m = re.search(r"(?:^|\s)(\d{4})(?:\s|$)", issue_part)
            if m:
                issue_date, precision = m.group(1), "year"
        # Volume
        m = re.search(r"(?:vol\.?|volume)\s*(\d+)", issue_part, re.IGNORECASE)
        if m:
            volume = m.group(1)
        # Issue number: "Issue 3", "No. 3", "#3"
        m = re.search(r"(?:issue|no\.?|#)\s*(\d+)", issue_part, re.IGNORECASE)
        if m:
            issue_number = m.group(1)
        # Fallback year
        if not year:
            y = year_from(issue_part)
            if y:
                year = y
    else:
        # No dash separator (e.g. "Penthouse 2002-02"): try trailing date patterns
        # YYYY-MM-DD, YYYY-MM, or YYYY at end of stem
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", stem)
        if m:
            issue_date, precision = normalize_issue_date(m.group(1))
            title_part = stem[: m.start()].strip()
        if not issue_date:
            m = re.search(r"(\d{4}-\d{2})\s*$", stem)
            if m:
                issue_date, precision = normalize_issue_date(m.group(1))
                title_part = stem[: m.start()].strip()
        if not issue_date:
            m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{4})\s*$", stem)
            if m:
                mo = MONTH_WORDS.get(m.group(1).lower())
                if mo:
                    issue_date, precision = f"{m.group(2)}-{mo:02d}", "month"
                    title_part = stem[: m.start()].strip()
        if not issue_date:
            m = re.search(r"(\d{4})\s*$", stem)
            if m:
                issue_date, precision = m.group(1), "year"
                title_part = stem[: m.start()].strip()
        if not year and issue_date:
            year = year_from(issue_date)

    if not year and issue_date:
        year = year_from(issue_date)

    return {
        "canonical_title": title_part or stem,
        "title": title_part or stem,
        "format": fmt,
        "type": MAGAZINE_TYPE,
        "issue_date": issue_date,
        "issue_date_precision": precision,
        "issue_number": issue_number,
        "volume": volume,
        "year": year,
    }


def build_magazine_metadata(
    inbuilt: dict, scraper: dict | None, fmt: str, filepath: str | None = None
) -> dict:
    """Merge filename inbuilt metadata with a chosen scraper result."""
    scraper = scraper or {}
    canonical = (
        inbuilt.get("canonical_title")
        or scraper.get("title")
        or (filepath and __import__("pathlib").Path(filepath).stem.replace("_", " ").strip())
        or "Unknown"
    )
    issue_date = inbuilt.get("issue_date") or normalize_issue_date(scraper.get("issue_date"))[0]
    precision = (
        inbuilt.get("issue_date_precision") or normalize_issue_date(scraper.get("issue_date"))[1]
    )
    issue_number = inbuilt.get("issue_number") or scraper.get("issue_number")
    volume = inbuilt.get("volume") or scraper.get("volume")
    year = inbuilt.get("year") or year_from(issue_date)
    # First-publication year of the periodical (rules.txt:133), distinct from the
    # issue/release year above. Comes from the scraper (ISSN Portal / LOC / Crossref).
    original_year = scraper.get("first_published") or inbuilt.get("original_year")

    publisher = scraper.get("publisher") or inbuilt.get("publisher") or ""
    print_issn = clean_issn(scraper.get("print_issn"))
    electronic_issn = clean_issn(scraper.get("electronic_issn"))
    country = scraper.get("country")
    frequency = scraper.get("frequency")
    language = scraper.get("language") or "English"
    page_count = scraper.get("page_count") or inbuilt.get("page_count")
    if page_count:
        try:
            page_count = int(page_count)
        except Exception:
            page_count = None
    source = inbuilt.get("source") or scraper.get("source")
    release_type = inbuilt.get("release_type") or "Individual Issue"

    label = (
        _issue_label(issue_date, precision, volume, issue_number)
        if (issue_date or volume or issue_number)
        else ""
    )
    release_title = f"{canonical} - {label}".strip(" -") if label else canonical

    tags = scraper.get("tags") or clean_tags(scraper.get("subjects") or []) or "magazine"
    if isinstance(tags, list):
        tags = clean_tags(tags)
    if not tags or not str(tags).strip():
        tags = "magazine"

    description = scraper.get("description") or ""

    return {
        "title": canonical,
        "canonical_title": canonical,
        "release_title": release_title,
        "year": year,
        "original_year": original_year,
        "issue_date": issue_date,
        "issue_date_precision": precision,
        "issue_number": issue_number,
        "volume": volume,
        "release_type": release_type,
        "publisher": publisher,
        "print_issn": print_issn,
        "electronic_issn": electronic_issn,
        "country": country,
        "frequency": frequency,
        "language": language,
        "page_count": page_count,
        "format": fmt.upper(),
        "source": source,
        "tags": tags,
        "book_desc": description,
        "album_desc": "",
        "release_desc": "",
        "type": MAGAZINE_TYPE,
        "cover_url_scraper": scraper.get("cover_url"),
        "source_urls": scraper.get("source_urls") or [],
    }


def validate_magazine_metadata(md: dict) -> list[str]:
    """Return missing required magazine fields (Simurg magazine form)."""
    missing = []
    if not md.get("title"):
        missing.append("title")
    if not md.get("year"):
        missing.append("year")
    if not (md.get("issue_date") or md.get("issue_number") or md.get("volume")):
        missing.append("issue_identity")
    if not md.get("format"):
        missing.append("format")
    if not md.get("source"):
        missing.append("source")
    return missing


def magazine_issue_label(md: dict) -> str:
    """Convenience label for staging/release titles."""
    return _issue_label(
        md.get("issue_date"),
        md.get("issue_date_precision"),
        md.get("volume"),
        md.get("issue_number"),
    )
