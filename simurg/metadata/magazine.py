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

# Release types per docs/rules.txt:129 (Simurg magazines)
MAGAZINE_RELEASE_TYPES = {
    "Individual Issue",
    "Year Pack",
    "Decade Pack",
    "Complete Run",
    "Custom Range",
}

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
        # No dash separator. Filenames like "Penthouse 2002-02", legacy
        # "National Geographic 1888-01 Oct 001-1", or "National Geographic 2020 01 US"
        # embed the date mid-stem (after the title, often followed by volume/issue
        # junk). Scan the WHOLE stem (not just end-anchored) so these decode too.
        m = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
        if m:
            issue_date, precision = normalize_issue_date(m.group(1))
            title_part = stem[: m.start()].strip()
        if not issue_date:
            # YYYY-MM or YYYY MM (space-separated) — catches "2000-01" and "2020 01"
            m = re.search(r"(\d{4}[ -]\d{1,2})", stem)
            if m:
                issue_date, precision = normalize_issue_date(m.group(1).replace(" ", "-"))
                title_part = stem[: m.start()].strip()
        if not issue_date:
            m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{4})", stem)
            if m:
                mo = MONTH_WORDS.get(m.group(1).lower())
                if mo:
                    issue_date, precision = f"{m.group(2)}-{mo:02d}", "month"
                    title_part = stem[: m.start()].strip()
        if not issue_date:
            # Bare 4-digit year anywhere in the stem
            m = re.search(r"(\d{4})", stem)
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
    # Language: default to English when unknown (user can override via editor).
    # Previous behaviour aborted on empty per docs/magazine.txt §10, but for
    # batch magazine collections (e.g. National Geographic) the scraper rarely
    # provides language and the collection is known English. Allow silent default
    # to English while still validating explicit non-allowed values.
    raw_lang = scraper.get("language") or inbuilt.get("language") or ""
    language = _normalize_magazine_language(raw_lang) if raw_lang else "English"
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


# ---------------------------------------------------------------------------
# Pack / collection helpers (Year Pack, Decade Pack) per docs/rules.txt:129
# ---------------------------------------------------------------------------

# Expected issue count for a monthly magazine year-pack — used to decide
# "Complete Year" vs partial "(10/12)" concise labelling. Keep as constant
# so tests and pack builder share the expectation.
EXPECTED_ISSUES_PER_YEAR = 12
EXPECTED_ISSUES_PER_DECADE = 120  # 10 years * 12, monthly assumption


def _pack_title_and_precision(
    pack_type: str, year_or_decade: int, available: int, expected: int
) -> tuple[str, str, str]:
    """Return (release_title, issue_date, precision) for a pack.

    Year Pack: "1995 Complete Year" when available==expected else "1995 (10/12)"
    Decade Pack: "2000-2009 Complete Decade" else "2000-2009 (118/120)"
    Custom Range / Complete Run handled similarly but not yet used.
    """
    if pack_type == "Year Pack":
        if available >= expected:
            release_title = f"{year_or_decade} Complete Year"
        else:
            release_title = f"{year_or_decade} ({available}/{expected})"
        issue_date = f"{year_or_decade}-01-01"
        precision = "year"
    elif pack_type == "Decade Pack":
        start = int(year_or_decade)
        end = start + 9
        if available >= expected:
            release_title = f"{start}-{end} Complete Decade"
        else:
            release_title = f"{start}-{end} ({available}/{expected})"
        issue_date = f"{start}-01-01"
        precision = "year"
    elif pack_type == "Complete Run":
        release_title = "Complete Run"
        # Use earliest year if known, else no date
        issue_date = f"{year_or_decade}-01-01" if year_or_decade else ""
        precision = "year" if issue_date else ""
    else:  # Custom Range
        release_title = str(year_or_decade)
        issue_date = ""
        precision = ""
    return release_title, issue_date, precision


def _pack_manifest(files: list, expected: int) -> str:
    """Build concise manifest for album_desc per docs/rules.txt:131."""
    # files is list of Path or list of (Path, pages, issue_label)
    lines: list[str] = []
    total = len(files)
    lines.append(
        f"Coverage: {total}/{expected} issues" if expected else f"Coverage: {total} issues"
    )
    # Per-issue entries — concise one line per file
    for entry in files:
        if isinstance(entry, (list, tuple)):
            # (path, pages, label)
            p, pages, label = entry
            name = getattr(p, "name", str(p))
            if pages:
                lines.append(f"- {name} — {label} — {pages}p")
            else:
                lines.append(f"- {name} — {label}")
        else:
            # Path only
            name = getattr(entry, "name", str(entry))
            lines.append(f"- {name}")
    return "\n".join(lines)


def _normalize_coverage_date(d: str | None) -> str | None:
    """Normalize an issue_date (YYYY / YYYY-MM / YYYY-MM-DD) to a YYYY-MM-DD string
    for the pack coverage_start / coverage_end form fields (input type=date)."""
    if not d:
        return None
    parts = str(d).split("-")
    try:
        if len(parts) == 1:
            return f"{int(parts[0]):04d}-01-01"
        if len(parts) == 2:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-01"
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except Exception:
        return None


def build_magazine_pack_metadata(
    canonical: str,
    pack_type: str,
    pack_key: int,
    files: list,
    scraper: dict | None,
    fmt: str,
    source: str | None = None,
    file_infos: list | None = None,
) -> dict:
    """Build pack-level magazine metadata for Year/Decade packs.

    * ``canonical`` — Publication title (e.g. "National Geographic")
    * ``pack_type`` — "Year Pack" / "Decade Pack" / "Complete Run"
    * ``pack_key`` — year (1995) or decade start (2000)
    * ``files`` — list of Path objects in the pack (sorted)
    * ``scraper`` — Publication-level scraper result (ISSN/publisher/etc)
    * ``fmt`` — uniform format (PDF)
    * ``source`` — uniform source label or None (falls back to scraper/inbuilt)
    * ``file_infos`` — optional list of (Path, page_count, issue_label) for manifest

    Returns a dict compatible with build_magazine_metadata payload (same keys)
    but with pack-specific release_title/year/issue_date and page_count = sum,
    and album_desc prefilled with the coverage manifest.
    """
    scraper = scraper or {}
    canonical = (canonical or scraper.get("title") or "Unknown").strip() or "Unknown"

    # Determine expected count
    if pack_type == "Year Pack":
        expected = EXPECTED_ISSUES_PER_YEAR
    elif pack_type == "Decade Pack":
        expected = EXPECTED_ISSUES_PER_DECADE
    else:
        expected = len(files)

    available = len(files)

    # Pack coverage start/end = actual included issue range (docs/rules.txt:131).
    # The tracker's pack form requires valid coverage_start / coverage_end dates.
    _cov_dates = []
    for fp in files:
        try:
            _md = decode_magazine_filename(fp)
            _cd = _normalize_coverage_date(_md.get("issue_date"))
            if _cd:
                _cov_dates.append(_cd)
        except Exception:
            pass
    _cov_dates.sort()
    coverage_start = _cov_dates[0] if _cov_dates else None
    coverage_end = _cov_dates[-1] if _cov_dates else None
    is_complete = available >= expected

    release_title, issue_date, precision = _pack_title_and_precision(
        pack_type, pack_key, available, expected
    )

    # Year field: for Year Pack it's the pack year; for Decade it's decade start
    year = int(pack_key) if pack_key else None

    original_year = scraper.get("first_published") or scraper.get("first_publish_year")

    publisher = scraper.get("publisher") or ""
    print_issn = clean_issn(scraper.get("print_issn"))
    electronic_issn = clean_issn(scraper.get("electronic_issn"))
    country = scraper.get("country")
    frequency = scraper.get("frequency")

    raw_lang = scraper.get("language") or ""
    language = _normalize_magazine_language(raw_lang) if raw_lang else "English"

    # Page count — sum of actual PDF pages for the pack
    page_count = None
    if file_infos:
        try:
            total = sum(int(p or 0) for _, p, _ in file_infos)  # type: ignore
            page_count = total if total > 0 else None
        except Exception:
            page_count = None
    if page_count is None:
        # Fallback: try to sum from scraper or leave None
        page_count = scraper.get("page_count")

    # Tags / description — same as individual path
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
    if not description or len(description) < 10:
        fallback = canonical
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
        if len(fallback) > 2000:
            fallback = fallback[:2000].strip()
        description = fallback

    # Manifest in album_desc per docs/rules.txt:131
    # Use file_infos if provided for richer per-issue detail, else plain filenames
    if file_infos:
        # Build rich manifest with issue labels
        manifest_entries = file_infos
    else:
        # Plain Path list
        manifest_entries = [(p, None, p.stem) for p in files]  # type: ignore
    manifest = _pack_manifest(manifest_entries, expected)  # type: ignore
    # Dedicated structured manifest for the tracker's pack form field
    # (one included issue per line, per docs/rules.txt:131).
    issue_manifest_lines = []
    for entry in manifest_entries:
        if isinstance(entry, (list, tuple)):
            p, pages, label = entry
            name = getattr(p, "name", str(p))
            if pages:
                issue_manifest_lines.append(f"- {name} — {label} — {pages}p")
            else:
                issue_manifest_lines.append(f"- {name} — {label}")
        else:
            issue_manifest_lines.append(f"- {getattr(entry, 'name', str(entry))}")
    issue_manifest = "\n".join(issue_manifest_lines)
    # Honest completeness declaration
    if available >= expected:
        manifest += "\nCompleteness: Complete"
    else:
        manifest += (
            f"\nCompleteness: Partial ({available}/{expected}) — missing issues not included"
        )

    fmt_upper = fmt.upper() if fmt else "PDF"
    # Source: explicit uniform source if provided, else scraper/inbuilt
    pack_source = source or scraper.get("source") or "Other"

    return {
        "title": canonical,
        "canonical_title": canonical,
        "release_title": release_title,
        "year": year,
        "original_year": original_year,
        "issue_date": issue_date,
        "issue_date_precision": precision,
        "issue_number": None,
        "volume": None,
        "release_type": pack_type,
        "publisher": publisher,
        "print_issn": print_issn,
        "electronic_issn": electronic_issn,
        "country": country,
        "frequency": frequency,
        "language": language,
        "page_count": page_count,
        "format": fmt_upper,
        "source": pack_source,
        "tags": tags,
        "book_desc": description,
        "album_desc": manifest,
        "release_desc": "",
        "type": MAGAZINE_TYPE,
        "cover_url_scraper": scraper.get("cover_url"),
        "source_urls": scraper.get("source_urls") or [],
        # Keep file list for staging/torrent helpers
        "pack_files": files,
        "pack_expected": expected,
        "pack_available": available,
        "pack_coverage_start": coverage_start,
        "pack_coverage_end": coverage_end,
        "pack_issue_count": available,
        "pack_is_complete": is_complete,
        "pack_issue_manifest": issue_manifest,
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
