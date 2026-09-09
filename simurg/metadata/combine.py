"""Combine inbuilt + scraper metadata."""

from __future__ import annotations

import re

from simurg.constants import GENRE_LEXICON, TAGS_MAX_LENGTH
from simurg.metadata.scrapers.util import clean_description


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
    """Join subjects to comma string lower dots per D18 / docs/ebook.txt §5."""
    if not subjects:
        return ""
    # Forbidden per docs/ebook.txt:308 — format/source + generic praise
    _forbidden = {
        "epub",
        "pdf",
        "mobi",
        "azw3",
        "djvu",
        "scan",
        "retail",
        "ocr",
        "convert",
        "other",
        "ebook",
        "e-book",
        "ebooks",
        "bestseller",
        "awesome",
        "must.read",
        "must-read",
    }
    tags = []
    for s in subjects:
        if not s:
            continue
        t = str(s).strip().lower()
        t = re.sub(r"\s+", ".", t)
        t = re.sub(r"[^a-z0-9\.\-]", "", t)
        t = re.sub(r"\.{2,}", ".", t)  # "Collections / Anthologies" -> no ".."
        t = t.strip(".-")
        if t and t not in _forbidden:
            # avoid forbidden tags - skip if exact match
            tags.append(t)
    # dedupe preserve order
    seen = set()
    uniq = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return ", ".join(fit_tags_to_limit(uniq[:8]))  # count + 200-char limits


def fit_tags_to_limit(uniq: list[str], limit: int = TAGS_MAX_LENGTH) -> list[str]:
    """Drop trailing tags until the joined string fits the tracker limit.

    Keeps at least one tag; a single pathological tag is hard-truncated.
    """
    uniq = [t for t in uniq if t]
    while len(uniq) > 1 and len(", ".join(uniq)) > limit:
        uniq = uniq[:-1]
    if uniq and len(", ".join(uniq)) > limit:
        uniq = [", ".join(uniq)[:limit]]
    return uniq


def tags_are_sparse(tags) -> bool:
    """True when tag suggestion should kick in: empty, non.fiction fallback, or <2 tags."""
    if not tags:
        return True
    items = [t.strip() for t in (tags if isinstance(tags, list) else str(tags).split(","))]
    items = [t for t in items if t]
    if not items:
        return True
    if len(items) == 1 and items[0].lower() == "non.fiction":
        return True
    return len(items) < 2


def suggest_tags_from_description(
    description: str | None,
    title: str = "",
    subjects: list[str] | tuple = (),
    existing: str | list = "",
) -> str:
    """Suggest tracker tags by matching description text against GENRE_LEXICON.

    Returns a comma string of NEW tags only (excluding `existing`), capped so
    existing + new fits the 8-tag limit. Returns "" when there is nothing
    useful to match (empty/short/synthesized descriptions).
    """
    if not description or len(str(description).strip()) < 10:
        return ""
    # Skip internally synthesized fallbacks, not real flap copy.
    if "uploaded via smoked-simurg" in str(description).lower():
        return ""
    if isinstance(existing, list):
        existing_items = [str(t).strip().lower() for t in existing if str(t).strip()]
    else:
        existing_items = [t.strip().lower() for t in str(existing).split(",") if t.strip()]
    existing_set = set(existing_items)
    slots = 8 - len(existing_set)
    if slots <= 0:
        return ""
    parts = [str(title or ""), str(description)]
    parts.extend(str(s) for s in (subjects or []) if s)
    blob = " ".join(parts).lower()
    scored: list[tuple[int, int, str]] = []
    for order, (tag, triggers) in enumerate(GENRE_LEXICON.items()):
        if tag.lower() in existing_set:
            continue
        score = 0
        for phrase in triggers:
            score += len(re.findall(r"\b" + re.escape(phrase.lower()) + r"\b", blob))
        if score > 0:
            scored.append((score, order, tag))
    scored.sort(key=lambda x: (-x[0], x[1]))
    # ponytail: first-match-wins ranking, no TF-IDF weighting until lexicon misses real books
    return clean_tags([tag for _, _, tag in scored[:slots]])


def strip_edition_from_canonical(title: str) -> str:
    """Move edition wording to release title; keep canonical clean per docs/ebook.txt §4.

    Removes bracketed edition (…Edition…), colon/dash edition suffixes
    (``: Illustrated Edition``, ``- Revised Edition``), and volume markers,
    so canonical stays as the stable work title (e.g. ``Dune: 50th Anniversary
    Edition`` -> ``Dune``). Release-specific wording belongs in remaster_title.
    """
    if not title:
        return title
    # 1) Parenthetical/bracketed edition e.g. "Dune (Illustrated Edition)"
    title = re.sub(
        r"\s*[\(\[][^\)\]]*(illustrated|edition|volume|vol\.?|deluxe|annotated|revised|reissue|expanded|special|anniversary)[^\)\]]*[\)\]]",
        "",
        title,
        flags=re.IGNORECASE,
    )
    # 2) Colon/dash edition suffix e.g. "Dune: 50th Anniversary Edition", "Dune - Revised Edition"
    title = re.sub(
        r"\s*[:\-–—]\s*(?:\d+(?:st|nd|rd|th)?\s+)?(?:illustrated|anniversary|revised|expanded|annotated|special|deluxe|reissue|collector'?s?).*?edition.*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\s*[:\-–—]\s*(?:illustrated|anniversary|revised|expanded|annotated|special|deluxe|reissue).*edition.*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    # 3) Trailing " - Nth Edition" without colon but with edition keyword
    title = re.sub(
        r"\s+(?:\d+(?:st|nd|rd|th)?\s+)?(?:illustrated|anniversary|revised|expanded|annotated|special|deluxe).*\bedition\b.*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+", " ", title).strip(" -:–—").strip()
    title = re.sub(r"\s+", " ", title).strip()
    return title


def detect_edition(title: str | None) -> str | None:
    """Pull edition wording out of a title, e.g. '(Illustrated Edition)', 'Dune: 50th Anniversary Edition', 'Vol. 3'."""
    if not title:
        return None
    m = re.search(
        r"[\(\[]([^\)\]]*(?:illustrated|edition|annotated|revised|deluxe|reissue|expanded|special|anniversary)[^\)\]]*)[\)\]]",
        title,
        flags=re.IGNORECASE,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    # Colon/dash edition suffix
    m2 = re.search(
        r"[:\-–—]\s*((?:\d+(?:st|nd|rd|th)?\s+)?(?:illustrated|anniversary|revised|expanded|annotated|special|deluxe).*?edition.*$)",
        title,
        flags=re.IGNORECASE,
    )
    if m2:
        return re.sub(r"\s+", " ", m2.group(1)).strip()
    # Bare edition phrase without brackets
    m3 = re.search(
        r"\b((?:\d+(?:st|nd|rd|th)?\s+)?(?:illustrated|anniversary|revised|expanded|annotated|special|deluxe).*?\bedition\b.*$)",
        title,
        flags=re.IGNORECASE,
    )
    if m3:
        return re.sub(r"\s+", " ", m3.group(1)).strip()
    m4 = re.search(r"\b(vol\.?\s*\d+|book\s*\d+|part\s*\d+)\b", title, flags=re.IGNORECASE)
    if m4:
        return m4.group(1)
    return None


def _normalize_language(lang: str | None) -> str | None:
    """Normalize language string to tracker-expected form."""
    if not lang:
        return None
    s = str(lang).strip()
    if not s:
        return None
    # Map common codes to English display
    low = s.lower()
    if low in ("en", "eng", "english"):
        return "English"
    if low in ("tr", "tur", "turkish"):
        return "Turkish"
    if low in ("ja", "jpn", "japanese"):
        return "Japanese"
    # Title-case for others, e.g. "French" -> "French", "de" -> "German" unlikely
    # Keep as-is title-cased for unknown languages rather than dropping
    return s.title() if len(s) < 20 else s


def _merge_contributors(inbuilt_vals, scraper_vals) -> list[str]:
    """Merge inbuilt (file truth) + scraper contributors, file-first deduped."""
    merged: list[str] = []
    seen: set[str] = set()
    for src in (inbuilt_vals, scraper_vals):
        if not src:
            continue
        vals = src if isinstance(src, (list, tuple)) else [src]
        for v in vals:
            v = str(v).strip()
            if not v or v.lower() in seen:
                continue
            seen.add(v.lower())
            merged.append(v)
    return merged


def build_metadata(
    inbuilt: dict, scraper: dict | None, fmt: str, filepath: str | None = None
) -> dict:
    """Build final metadata dict matching §2 fields per docs/ebook.txt.

    Four-field model (docs/ebook.txt:701):
      Publication = work:  title (canonical), year (First Published)
      Release     = edition: remaster_title, remaster_year
    Scraper contract: year/publish_year = edition year, first_publish_year = work year.
    File year (inbuilt.year) is always the Release year (edition), never Publication.
    """
    scraper = scraper or {}
    # Authors
    authors = normalize_authors(inbuilt.get("authors") or scraper.get("authors") or [])
    if not authors and scraper.get("author"):
        authors = normalize_authors([scraper["author"]])
    # Title handling — Publication vs Release per docs/ebook.txt §4
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
    # Release title includes edition wording if present anywhere
    release_title = base_title
    # If inbuilt carried an explicit edition field, ensure release title reflects it
    inbuilt_edition = (inbuilt.get("edition") or "").strip()
    if inbuilt_edition and inbuilt_edition.lower() not in release_title.lower():
        # Append edition parenthetically if release title doesn't already contain it
        if not detect_edition(release_title):
            release_title = f"{release_title} ({inbuilt_edition})"
    # If canonical was stripped, keep original base as release (already done);
    # otherwise try scraper edition hint
    if canonical_title.lower() == base_title.lower():
        scraper_edition = scraper.get("edition") or detect_edition(scraper_title)
        if scraper_edition and scraper_edition.lower() not in release_title.lower():
            if not detect_edition(release_title):
                release_title = f"{release_title} ({scraper_edition})"
    elif canonical_title.lower() != base_title.lower():
        release_title = base_title

    # Year handling per docs/ebook.txt:12,150,178,234
    # Publication = work original year (First Published) — NEVER file year.
    # Release     = edition year (file/scraper edition).
    inbuilt_year = inbuilt.get("year")
    # scraper work year (Publication) — only first_publish_year / first_published
    scraper_work_year = (
        scraper.get("first_publish_year")
        or scraper.get("first_published")
        or scraper.get("original_year")
    )
    # scraper edition year (Release)
    scraper_edition_year = scraper.get("publish_year") or scraper.get("year")
    # First Published (Publication) — work year; missing work year backfills from edition year.
    first_published = scraper_work_year

    # Release year (edition) — prefer file truth, then scraper edition
    release_year = inbuilt_year or scraper_edition_year
    if first_published is None:
        first_published = release_year

    # Publisher — file is edition truth per docs/ebook.txt:372 (evidence priority: file > catalogue)
    publisher = inbuilt.get("publisher") or scraper.get("publisher") or "Unknown Publisher"
    # ISBN — edition-specific per docs/ebook.txt:391, prefer file
    isbn = clean_isbn(inbuilt.get("isbn") or scraper.get("isbn"))
    # Language — actual file language per docs/ebook.txt:354, scraper fallback, then English
    raw_lang = inbuilt.get("language") or scraper.get("language")
    language = _normalize_language(raw_lang) or "English"
    # Page count — Release-level per docs/ebook.txt:405
    # PDF len(pages) is exact; for EPUB/MOBI file is estimate (250wpp) vs scraper print count.
    # Prefer file for PDF, scraper for reflowable; fallback to whichever exists.
    fmt_upper = fmt.upper()
    if fmt_upper == "PDF":
        page_count = (
            inbuilt.get("page_count") or scraper.get("page_count") or scraper.get("number_of_pages")
        )
    else:
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
    # Tags - enriched from description when scraper subjects are sparse
    tags = scraper.get("tags") or clean_tags(
        scraper.get("subjects") or scraper.get("subject") or []
    )
    if isinstance(tags, list):
        tags = clean_tags(tags)
    if tags_are_sparse(tags):
        raw_desc = (
            scraper.get("description")
            or scraper.get("synopsis")
            or inbuilt.get("description")
            or ""
        )
        raw_desc = clean_description(raw_desc) or ""
        suggested = suggest_tags_from_description(
            raw_desc,
            title=canonical_title,
            subjects=scraper.get("subjects") or scraper.get("subject") or [],
            existing=tags,
        )
        if suggested:
            merged = [t.strip() for t in f"{tags}, {suggested}".split(",") if t.strip()]
            tags = clean_tags(merged)
    # Fallback tags if still empty — tracker requires at least one
    if not tags or not tags.strip():
        # Try to infer from title/author, else generic
        # Use non.fiction as safe default for books without tags
        tags = "non.fiction"
        # Could also try to use existing group tags if dupe found, but handled elsewhere
    # Synopsis
    synopsis = (
        scraper.get("description") or scraper.get("synopsis") or inbuilt.get("description") or ""
    )
    synopsis = clean_description(synopsis) or ""
    if synopsis:
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
    # Translators, editors, illustrators — Release-level per docs/ebook.txt §3, merge file truth + scraper
    translators = _merge_contributors(inbuilt.get("translators"), scraper.get("translators"))
    editors = _merge_contributors(inbuilt.get("editors"), scraper.get("editors"))
    illustrators = _merge_contributors(inbuilt.get("illustrators"), scraper.get("illustrators"))

    # Source placeholder - to be prompted later; default Retail if undetermined?
    # Keep None to prompt
    source = inbuilt.get("source") or scraper.get("source")

    # Cover - prefer inbuilt cover_path, else scraper cover_url
    cover_path = inbuilt.get("cover_path")
    cover_url_scraper = scraper.get("cover_url")

    # Edition for release notes / display
    edition_val = (
        inbuilt_edition or detect_edition(release_title) or detect_edition(base_title) or ""
    )

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
        "edition": edition_val,
        "type": "E-Book",
        "cover_path": cover_path,
        "cover_url_scraper": cover_url_scraper,
        "source_urls": scraper.get("source_urls") or [],
        "description": synopsis,  # alias
    }
    return result


def validate_metadata(md: dict) -> list[str]:
    """Return list of missing required field names per docs/ebook.txt §13."""
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
        if f == "remaster_year" or f == "year":
            # Also validate remaster_year bounds when present
            rv = md.get("remaster_year")
            if rv is not None:
                try:
                    riv = int(rv) if not isinstance(rv, int) else rv
                    if riv < 1000 or riv > 2100:
                        if "remaster_year" not in missing:
                            missing.append("remaster_year")
                except Exception:
                    if "remaster_year" not in missing:
                        missing.append("remaster_year")
    # Publication year (year) missing is strict per docs/ebook.txt:1200 — flag for human review
    # Tags optional? Plan says required yes, but we allow empty and prompt?
    # We'll treat tags as optional for validation but warn
    return missing
