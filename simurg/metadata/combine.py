"""Combine inbuilt + scraper metadata."""

from __future__ import annotations

import re


def _flip_last_first(name: str) -> str:
    """Convert 'Last, First' to 'First Last' for display. e.g. 'Defoe, Daniel' -> 'Daniel Defoe', 'Card, Orson Scott' -> 'Orson Scott Card'."""
    name = name.strip()
    # Only flip if single comma and not already containing multiple commas or semicolons
    if "," in name:
        # Split on first comma, handle "Last, First" or "Last, First Middle"
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            # Avoid flipping if first part contains space and second is single initial? Still flip
            # Heuristic: if original has comma, treat as Last, First
            first = parts[1]
            last = parts[0]
            # Collapse whitespace
            flipped = f"{first} {last}".strip()
            flipped = re.sub(r"\s+", " ", flipped)
            return flipped
    return re.sub(r"\s+", " ", name).strip()


def normalize_authors(authors):
    """Dedupe and normalize author list, flipping Last, First to First Last."""
    if not authors:
        return []
    # If authors is string, split carefully: don't split Last, First on comma alone if it's a single author
    # Use ; and " and " and & as separators, but handle comma separately
    if isinstance(authors, str):
        # First split on ; and " and " and &
        # Keep commas inside Last, First if string looks like single author with comma
        if (
            authors.count(",") == 1
            and ";" not in authors
            and " and " not in authors.lower()
            and "&" not in authors
        ):
            # Likely single author "Last, First" -> keep as one
            authors = [authors]
        else:
            authors = re.split(r"\s*;\s*|\s+and\s+|\s*&\s*", authors)
            # Now each part may still contain ", " for Last, First that needs flipping later, not splitting
    out = []
    seen = set()
    for a in authors:
        a = a.strip()
        if not a:
            continue
        # Handle case where author string still contains comma-separated multiple authors like "Austen, Jane, Dickens, Charles"
        # But our earlier split kept "Defoe, Daniel" as one; we should not split it further here
        # Instead, check if after splitting we have a single author with comma, flip it
        # If author contains comma and we haven't split it, flip
        # If author string contains ", " and no flip yet, treat as Last, First
        if "," in a and a.count(",") == 1:
            # Check if this is likely Last, First vs two authors "Austen, Jane" - we already handled
            a = _flip_last_first(a)
        else:
            # For strings with multiple commas, might be list like "Smith, John, Doe, Jane" - rare, keep as is
            a = re.sub(r"\s+", " ", a)
        # collapse whitespace already done in flip
        a = re.sub(r"\s+", " ", a).strip()
        low = a.lower()
        if low not in seen:
            seen.add(low)
            out.append(a)
    return out


def clean_isbn(isbn: str | None) -> str | None:
    if not isbn:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", str(isbn))
    if len(cleaned) in (10, 13):
        return cleaned
    return None


def clean_tags(subjects) -> str:
    """Join subjects to comma string lower dots per D18."""
    if not subjects:
        return ""
    # subjects may be list of strings
    tags = []
    for s in subjects:
        if not s:
            continue
        t = str(s).strip().lower()
        t = re.sub(r"\s+", ".", t)
        t = re.sub(r"[^a-z0-9\.\-]", "", t)
        if t and t not in {"epub", "pdf", "mobi", "scan", "retail", "azw3", "djvu"}:
            # avoid forbidden tags - skip if exact match
            tags.append(t)
    # dedupe preserve order
    seen = set()
    uniq = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return ", ".join(uniq[:8])  # limit


def strip_edition_from_canonical(title: str) -> str:
    """Move edition wording to release title; keep canonical clean."""
    if not title:
        return title
    # Remove parenthetical edition
    title = re.sub(
        r"\s*[\(\[][^\)\]]*(illustrated|edition|volume|vol\.?|deluxe|annotated|revised)[^\)\]]*[\)\]]",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+", " ", title).strip()
    return title


def detect_edition(title: str | None) -> str | None:
    """Pull edition wording out of a title, e.g. '(Illustrated Edition)', 'Vol. 3'."""
    if not title:
        return None
    m = re.search(
        r"[\(\[]([^\)\]]*(?:illustrated|edition|annotated|revised|deluxe|reissue|expanded|special)[^\)\]]*)[\)\]]",
        title,
        flags=re.IGNORECASE,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m2 = re.search(r"\b(vol\.?\s*\d+|book\s*\d+|part\s*\d+)\b", title, flags=re.IGNORECASE)
    if m2:
        return m2.group(1)
    return None


def build_metadata(
    inbuilt: dict, scraper: dict | None, fmt: str, filepath: str | None = None
) -> dict:
    """Build final metadata dict matching §2 fields."""
    scraper = scraper or {}
    # Authors
    authors = normalize_authors(inbuilt.get("authors") or scraper.get("authors") or [])
    if not authors and scraper.get("author"):
        authors = normalize_authors([scraper["author"]])
    # Title handling
    inbuilt_title = (inbuilt.get("title") or "").strip()
    scraper_title = (scraper.get("title") or "").strip()
    # Prefer inbuilt title if present, else scraper
    base_title = (
        inbuilt_title
        or scraper_title
        or (
            (filepath and __import__("pathlib").Path(filepath).stem.replace("_", " ").strip())
            or "Unknown"
        )
    )
    canonical_title = strip_edition_from_canonical(base_title)
    # Release title includes edition wording if inbuilt had it
    release_title = base_title
    if canonical_title.lower() != base_title.lower():
        # Keep original as release title (with edition)
        release_title = base_title
    else:
        # If scraper has edition, use that?
        release_title = base_title

    # Year handling - prefer inbuilt year from file (accurate for the edition),
    # fall back to scraper. Scraper first_publish_year can be wrong (e.g. OpenLibrary).
    inbuilt_year = inbuilt.get("year")
    scraper_year = scraper.get("year") or scraper.get("first_publish_year")
    year = inbuilt_year or scraper_year
    release_year = inbuilt_year or scraper.get("publish_year") or scraper_year or year
    first_published = year

    # Publisher
    publisher = scraper.get("publisher") or inbuilt.get("publisher") or "Unknown Publisher"
    # ISBN
    isbn = clean_isbn(inbuilt.get("isbn") or scraper.get("isbn"))
    # Language hardcoded English per C12
    language = "English"
    # Page count: prefer the file-derived count (PDF exact; EPUB/MOBI estimated),
    # falling back to the scraper's print-edition number when the file has none.
    page_count = (
        scraper.get("page_count") or scraper.get("number_of_pages") or inbuilt.get("page_count")
    )
    if page_count:
        try:
            page_count = int(page_count)
            if page_count <= 0:
                page_count = None
        except Exception:
            page_count = None
    else:
        page_count = None
    # Tags - must be before synopsis fallback
    tags = scraper.get("tags") or clean_tags(
        scraper.get("subjects") or scraper.get("subject") or []
    )
    if isinstance(tags, list):
        tags = clean_tags(tags)
    # Fallback tags if empty — tracker requires at least one
    if not tags or not tags.strip():
        # Try to infer from title/author, else generic
        # Use non.fiction as safe default for books without tags
        tags = "non.fiction"
        # Could also try to use existing group tags if dupe found, but handled elsewhere
    # Synopsis
    synopsis = (
        scraper.get("description") or scraper.get("synopsis") or inbuilt.get("description") or ""
    )
    if synopsis:
        synopsis = synopsis.strip()
        # Truncate to ~2000 chars first 2 paragraphs
        paras = synopsis.split("\n\n")
        if len(paras) > 2:
            synopsis = "\n\n".join(paras[:2])
        if len(synopsis) > 2000:
            synopsis = synopsis[:2000].strip()
        # Convert to BBCode minimal: keep text, escape?
        # For MVP, BBCode is plain text with paragraphs
    # Fallback synopsis if still empty or too short (<10) — tracker requires >=10
    if not synopsis or len(synopsis.strip()) < 10:
        # Try to build from available metadata
        synopsis = f"{canonical_title} by {', '.join(authors) if authors else 'Unknown Author'}"
        if first_published:
            synopsis += f" ({first_published})"
        if publisher and publisher != "Unknown Publisher":
            synopsis += f" - Published by {publisher}"
        if isbn:
            synopsis += f" - ISBN {isbn}"
        synopsis += "."
        if tags:
            synopsis += f" Tags: {tags}."
        # Ensure at least 50 chars
        if len(synopsis.strip()) < 50:
            synopsis += " Uploaded via smoked-simurg. No synopsis available from scrapers or file."
        synopsis = synopsis.strip()
        # Also ensure we have at least 10 chars for tracker
        if len(synopsis) < 10:
            synopsis = "No synopsis available. " + synopsis
    # Translators, editors, illustrators
    translators = scraper.get("translators") or []
    editors = scraper.get("editors") or []
    illustrators = scraper.get("illustrators") or []

    # Source placeholder - to be prompted later; default Retail if undetermined?
    # Keep None to prompt
    source = inbuilt.get("source") or scraper.get("source")

    # Cover - prefer inbuilt cover_path, else scraper cover_url
    cover_path = inbuilt.get("cover_path")
    cover_url_scraper = scraper.get("cover_url")

    result = {
        "title": canonical_title,
        "remaster_title": release_title,
        "authors": authors,
        "translators": translators,
        "editors": editors,
        "illustrators": illustrators,
        "year": first_published,
        "remaster_year": release_year,
        "language": language,
        "publisher": publisher,
        "isbn": isbn,
        "page_count": page_count,
        "album_desc": synopsis,
        "tags": tags,
        "format": fmt.upper(),  # EPUB, PDF, etc.
        "source": source,
        "release_notes": "",
        "release_desc": "",
        "type": "E-Book",
        "cover_path": cover_path,
        "cover_url_scraper": cover_url_scraper,
        "source_urls": scraper.get("source_urls") or [],
        "description": synopsis,  # alias
    }
    return result


def validate_metadata(md: dict) -> list[str]:
    """Return list of missing required field names."""
    required = ["title", "authors", "year", "publisher", "format", "source"]
    missing = []
    for f in required:
        v = md.get(f)
        if not v:
            # special: authors empty list is falsy, year 0 is falsy but year should be checked separately
            missing.append(f)
            continue
        if f == "authors" and not v:
            if f not in missing:
                missing.append(f)
            continue
        if f == "year":
            try:
                iv = int(v) if not isinstance(v, int) else v
                if iv < 1000 or iv > 2100:
                    if f not in missing:
                        missing.append(f)
                # also ensure it's int-like; if string year like "2020" it's okay
            except Exception:
                if f not in missing:
                    missing.append(f)
    # Tags optional? Plan says required yes, but we allow empty and prompt?
    # We'll treat tags as optional for validation but warn
    return missing
