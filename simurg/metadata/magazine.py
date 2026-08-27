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

# Upload form currently only exposes these magazine languages (docs/magazine.txt §10).
# Non-matching languages must abort, never silently map to English.
ALLOWED_MAGAZINE_LANGUAGES = {"english", "turkish", "japanese"}

# Normalize common language codes/aliases to the canonical form values above.
_LANGUAGE_ALIASES = {
    "en": "English",
    "eng": "English",
    "english": "English",
    "tr": "Turkish",
    "tur": "Turkish",
    "turkish": "Turkish",
    "turkiye": "Turkish",
    "türkçe": "Turkish",
    "ja": "Japanese",
    "jp": "Japanese",
    "jpn": "Japanese",
    "japanese": "Japanese",
    "japan": "Japanese",
}


def _normalize_magazine_language(raw: str | None) -> str:
    """Map raw language string to canonical English/Turkish/Japanese or ''/original.

    Returns '' for empty input, the canonical cased value for known aliases,
    and the original stripped value for unknown languages (so validation can
    correctly abort on Russian etc. instead of silently returning '').
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    low = s.lower()
    if low in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[low]
    # Also handle values like "en-US", "en_GB"
    base = re.split(r"[-_\s]+", low)[0]
    if base in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[base]
    return s


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
    # Language: do NOT default to English. Leave empty when unknown so CLI
    # can abort per docs/magazine.txt §10 (form only has English/Turkish/Japanese;
    # Russian etc. must not silently become English). Normalize known aliases.
    raw_lang = scraper.get("language") or inbuilt.get("language") or ""
    language = _normalize_magazine_language(raw_lang) if raw_lang else ""
    page_count = inbuilt.get("page_count") or scraper.get("page_count")
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
    # Release title is the issue identity only, never "Canonical - Issue"
    # (docs/magazine.txt §6: "May be translated or release-specific; it never
    # renames the Publication." + §3 incorrect example). Canonical stays clean.
    release_title = label.strip() if label else ""

    tags = scraper.get("tags") or clean_tags(scraper.get("subjects") or []) or "magazine"
    if isinstance(tags, list):
        tags = clean_tags(tags)
    if not tags or not str(tags).strip():
        tags = "magazine"

    description = (
        scraper.get("description")
        or scraper.get("book_desc")
        or scraper.get("synopsis")
        or scraper.get("album_desc")
        or ""
    )
    description = str(description).strip()
    # Tracker requires synopsis >=10 chars (see .failed/Penthouse Russia... — empty
    # book_desc was rejected with "The canonical Publication synopsis must be at least
    # 10 characters."). Magazines rarely have scraper descriptions (ISSN sources
    # don't provide them), so synthesize a minimal synopsis from available metadata
    # so validation and the live upload never send an empty book_desc.
    # Mirrors build_metadata() fallback for ebooks (combine.py:225) but magazine-specific.
    if not description or len(description) < 10:
        label = (
            _issue_label(issue_date, precision, volume, issue_number)
            if (issue_date or volume or issue_number)
            else ""
        )
        # Base: canonical title + issue label (e.g. "Penthouse — November 2004")
        if canonical and label:
            fallback = f"{canonical} — {label}"
        elif canonical:
            fallback = canonical
        else:
            fallback = "Magazine issue"
        if year:
            fallback += f" ({year})"
        if publisher:
            fallback += f" - Published by {publisher}"
        if country:
            fallback += f" - {country}"
        issn_any = print_issn or electronic_issn
        if issn_any:
            fallback += f" - ISSN {issn_any}"
        fallback += "."
        if tags and tags != "magazine":
            fallback += f" Tags: {tags}."
        else:
            fallback += " Tags: magazine."
        if len(fallback) < 50:
            fallback += " Uploaded via smoked-simurg. No synopsis available from scrapers."
        fallback = fallback.strip()
        if len(fallback) < 10:
            fallback = "No synopsis available. " + fallback
        # Truncate at 2000 chars like ebook path
        if len(fallback) > 2000:
            fallback = fallback[:2000].strip()
        description = fallback

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
    """Return missing required magazine fields (Simurg magazine form).

    Also enforces docs/magazine.txt §10: the live form only exposes
    English/Turkish/Japanese. Any other language must be flagged as missing
    so the caller can abort (never silently map Russian etc. to English).
    """
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
    # Language must be one of the allowed upload-form values; otherwise abort.
    lang = (md.get("language") or "").strip()
    if not lang or lang.lower() not in ALLOWED_MAGAZINE_LANGUAGES:
        missing.append("language")
    if not md.get("release_title"):
        missing.append("release_title")
    return missing


def magazine_issue_label(md: dict) -> str:
    """Convenience label for staging/release titles."""
    return _issue_label(
        md.get("issue_date"),
        md.get("issue_date_precision"),
        md.get("volume"),
        md.get("issue_number"),
    )
