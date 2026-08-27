"""Compile Gazelle payload for Simurg per §2 - actual Simurg upload.php fields.

FIELD NAMES ARE VERIFIED against the live form (https://simurg.world/upload.php,
<form name="torrent">, checked 2026-08-25). Simurg runs a Gazelle (music-tracker)
fork, so the POST field names are Gazelle-legacy names, NOT the on-page labels:

    on-page label        POST name
    -------------------  --------------------
    "Publisher:"        -> record_label       (legacy music "Record Label" field)
    "ISBN:"             -> catalogue_number   (legacy music catalogue number field)
    "Canonical title:"  -> book_title
    "First published:"  -> original_year
    "Release title:"    -> title
    "Release year:"     -> year
    "Synopsis:"         -> book_desc
    "Release notes:"    -> album_desc
    "Source:"           -> bitrate            (legacy music bitrate field)

Contributors are sent as repeated `artists[]` + `importance[]` pairs (one per
role), where importance = 1 author, 3 translator, 4 editor, 6 illustrator.
There is no `authors[]`, `publisher`, `isbn`, `groupid`, or `remaster_*` field
on the form. Keeping the semantic metadata key names (`publisher`, `isbn`,
`title`, `year`, ...) everywhere else in the pipeline; only THIS module maps
them onto the tracker's wire format.
"""

from __future__ import annotations

import re

# Map type string to numeric value from <select name="type">: 2=E-Books
TYPE_MAP = {
    "E-Book": "2",
    "E-Books": "2",
    "Audiobooks": "3",
    "Comics & Manga": "6",
    "Magazines": "7",
    2: "2",
    "2": "2",
}

# Importance mapping for artists[] per upload.php: 1=author, 3=translator, 4=editor, 6=illustrator
IMPORTANCE_MAP = {
    "authors": "1",
    "author": "1",
    "translators": "3",
    "translator": "3",
    "editors": "4",
    "editor": "4",
    "illustrators": "6",
    "illustrator": "6",
}


def _magazine_issue_date_for_payload(raw: str | None) -> str:
    """Normalize magazine issue date to YYYY-MM-DD for Simurg's upload.php.

    Scraper/filename code stores month-precision as ``YYYY-MM`` and year-precision
    as ``YYYY`` (see ``normalize_issue_date``), but the live tracker validates
    strictly with ``YYYY-MM-DD`` (``.failed/Penthouse*.json:30`` — ``2002-02``
    was rejected with ``Enter a valid issue date in YYYY-MM-DD format.``).
    Pad with ``-01`` / ``-01-01`` so the tracker accepts it; the companion
    ``magazine_issue_date_precision`` field still tells the tracker the real
    precision (``month``/``year``/``day``).

    Already-full ``YYYY-MM-DD`` values pass through unchanged.
    """
    if not raw:
        return ""
    s = str(raw).strip()

    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    if re.match(r"^\d{4}-\d{2}$", s):
        return f"{s}-01"
    if re.match(r"^\d{4}$", s):
        return f"{s}-01-01"
    return s


def _ensure_magazine_synopsis(metadata: dict) -> str:
    """Ensure magazine synopsis (book_desc) is >=10 chars for Simurg.

    Defensive fallback at the payload layer so even if build_magazine_metadata
    is bypassed or the user clears the synopsis in review, we never POST an
    empty or trivially-short ``book_desc`` (which Simurg rejects with
    "The canonical Publication synopsis must be at least 10 characters." —
    see .failed/Penthouse Russia - November 2004*.json).
    Mirrors the fallback in build_magazine_metadata but operates on the final
    merged metadata dict so it also covers manual edits.
    """
    raw = (metadata.get("book_desc") or metadata.get("description") or "").strip()
    if raw and len(raw) >= 10:
        return raw
    # Synthesize from available magazine metadata
    title = (metadata.get("title") or metadata.get("canonical_title") or "Magazine issue").strip()
    # Reuse issue_label helper for a nice label
    try:
        from simurg.metadata.scrapers.util import issue_label as _il

        label = _il(
            metadata.get("issue_date"),
            metadata.get("issue_date_precision"),
            metadata.get("volume"),
            metadata.get("issue_number"),
        )
    except Exception:
        label = ""
    if title and label:
        fallback = f"{title} — {label}"
    else:
        fallback = title or "Magazine issue"
    year = metadata.get("year")
    if year:
        fallback += f" ({year})"
    publisher = (metadata.get("publisher") or "").strip()
    if publisher:
        fallback += f" - Published by {publisher}"
    country = (metadata.get("country") or "").strip()
    if country:
        fallback += f" - {country}"
    issn_any = (metadata.get("print_issn") or metadata.get("electronic_issn") or "").strip()
    if issn_any:
        fallback += f" - ISSN {issn_any}"
    fallback += "."
    tags = (metadata.get("tags") or "").strip()
    if tags and tags != "magazine":
        fallback += f" Tags: {tags}."
    else:
        fallback += " Tags: magazine."
    if len(fallback) < 50:
        fallback += " Uploaded via smoked-simurg. No synopsis available from scrapers."
    if len(fallback) < 10:
        fallback = "No synopsis available. " + fallback
    if len(fallback) > 2000:
        fallback = fallback[:2000].strip()
    return fallback.strip()


def _build_artists_importance(metadata: dict):
    """Return (artists[], importance[]) lists for Simurg's 4 contributor roles."""
    artists = []
    importance = []
    # Order: authors (1), translators (3), editors (4), illustrators (6)
    for key, imp in [
        ("authors", "1"),
        ("translators", "3"),
        ("editors", "4"),
        ("illustrators", "6"),
    ]:
        vals = metadata.get(key) or metadata.get(key.rstrip("s")) or []
        # also handle legacy "authors[]" style
        if not vals:
            vals = metadata.get(f"{key}[]") or []
        if isinstance(vals, str):
            vals = [vals]
        for v in vals:
            v = str(v).strip()
            if v:
                artists.append(v)
                importance.append(imp)
    # Also handle generic "artists" already provided with importance?
    # If no authors but metadata has single "artist" key
    if not artists and metadata.get("artist"):
        artists = [str(metadata["artist"])]
        importance = ["1"]
    return artists, importance


def compile_data_new_publication(
    metadata: dict, cover_url: str | None, source_urls: list[str] | None = None, request_id=None
) -> dict:
    """New Publication payload - matches actual Simurg upload.php names."""
    artists, importance = _build_artists_importance(metadata)

    # Type: numeric 2 for E-Books
    raw_type = metadata.get("type", "E-Book")
    type_val = TYPE_MAP.get(raw_type, "2")

    data: dict = {
        "submit": True,
        "type": type_val,
        # Canonical — verified field names (see module docstring)
        "book_title": metadata.get("title")
        or metadata.get("canonical_title")
        or metadata.get("book_title")
        or "Unknown",
        "original_year": str(metadata.get("year") or metadata.get("original_year") or ""),
        # Release
        "title": metadata.get("remaster_title")
        or metadata.get("title")
        or metadata.get("release_title")
        or "Unknown",
        "year": str(metadata.get("remaster_year") or metadata.get("year") or ""),
        "tags": metadata.get("tags") or "",
        "image": cover_url or metadata.get("image") or "",
        "language": metadata.get("language", "English"),
        # Publisher maps to Gazelle's legacy "record_label" field (label says "Publisher:")
        "record_label": metadata.get("publisher") or metadata.get("record_label") or "",
        # ISBN maps to Gazelle's legacy "catalogue_number" field (label says "ISBN:")
        "catalogue_number": metadata.get("isbn") or metadata.get("catalogue_number") or "",
        "page_count": str(metadata.get("page_count") or ""),
        # Canonical synopsis -> book_desc; release notes -> album_desc
        "book_desc": metadata.get("album_desc")
        or metadata.get("book_desc")
        or metadata.get("synopsis")
        or metadata.get("description")
        or "",
        "album_desc": metadata.get("release_notes") or metadata.get("album_desc") or "",
        "release_desc": metadata.get("release_desc") or "",
        "format": metadata.get("format", "MOBI"),
        # Source (Retail/Scan/OCR/Convert/Other) maps to legacy "bitrate" field
        "bitrate": metadata.get("source")
        or metadata.get("media")
        or metadata.get("bitrate")
        or "Retail",
    }
    # Contributors
    if artists:
        data["artists[]"] = artists
        data["importance[]"] = importance
    # Legacy aliases for API compatibility (some trackers accept both)
    # Keep authors[] for backwards compat if API checks it, but primary is artists[]
    if artists and artists[0]:
        # also set authors[] as alias to artists[] for API fallback
        data["authors[]"] = artists  # not used by HTML but harmless

    if request_id:
        data["requestid"] = str(request_id)

    return data


def compile_data_existing_publication(
    group_id: int | str, metadata: dict, cover_url: str | None, request_id=None
) -> dict:
    """Existing Publication - only mutable fields per Simurg form."""
    # For existing, canonical fields are read-only; only release-specific fields are sent
    # But we still need to send type/format/bitrate etc.
    raw_type = metadata.get("type", "E-Book")
    type_val = TYPE_MAP.get(raw_type, "2")

    data: dict = {
        "submit": True,
        "type": type_val,
        # Verified form field is `publicationid` (NOT `groupid`). The legacy
        # `groupid` alias must NOT be posted to Simurg: Simurg resolves `groupid`
        # as a torrent *group* id (a different id space from publications), and
        # sending it alongside `publicationid` triggers
        # "The selected torrent group does not exist." (see .failed/ evidence).
        "publicationid": str(group_id),
        "title": metadata.get("remaster_title") or metadata.get("title") or "",
        "year": str(metadata.get("remaster_year") or metadata.get("year") or ""),
        "format": metadata.get("format", "MOBI"),
        # Source -> legacy "bitrate" field
        "bitrate": metadata.get("source") or metadata.get("media") or "Other",
        "album_desc": metadata.get("release_notes") or "",
        "release_desc": metadata.get("release_desc") or "",
        "image": cover_url or metadata.get("image") or "",
        "tags": metadata.get("tags") or "",
        # Publisher -> legacy "record_label"; ISBN -> legacy "catalogue_number"
        "record_label": metadata.get("publisher") or "",
        "catalogue_number": metadata.get("isbn") or "",
        "page_count": str(metadata.get("page_count") or ""),
        "language": metadata.get("language", "English"),
    }
    # Even for existing, some sites require artists to be resent? Simurg says canonical fields read-only,
    # but to be safe we can send artists as well if present (will be ignored if read-only)
    artists, importance = _build_artists_importance(metadata)
    if artists:
        data["artists[]"] = artists
        data["importance[]"] = importance

    if request_id:
        data["requestid"] = str(request_id)
    # Keep legacy source/media alias
    data["source"] = data["bitrate"]
    data["media"] = data["bitrate"]
    return data


def compile_data_new_magazine(
    metadata: dict, cover_url: str | None, source_urls: list[str] | None = None, request_id=None
) -> dict:
    """New Magazine Publication payload - matches the live magazine upload form."""
    raw_type = metadata.get("type", "Magazines")
    type_val = TYPE_MAP.get(raw_type, "7")

    data: dict = {
        "submit": True,
        "type": type_val,
        # Canonical publication identity
        "book_title": metadata.get("title") or metadata.get("canonical_title") or "Unknown",
        "original_year": str(metadata.get("original_year") or ""),
        "magazine_print_issn": metadata.get("print_issn") or "",
        "magazine_electronic_issn": metadata.get("electronic_issn") or "",
        "magazine_publisher": metadata.get("publisher") or "",
        "magazine_country": metadata.get("country") or "",
        "magazine_frequency": metadata.get("frequency") or "",
        # Release / issue identity
        "title": metadata.get("release_title") or metadata.get("title") or "",
        "year": str(metadata.get("year") or ""),
        "magazine_release_type": metadata.get("release_type") or "Individual Issue",
        "magazine_issue_date": _magazine_issue_date_for_payload(metadata.get("issue_date")),
        "magazine_issue_date_precision": metadata.get("issue_date_precision") or "",
        "magazine_volume": str(metadata.get("volume") or ""),
        "magazine_issue_number": str(metadata.get("issue_number") or ""),
        # Shared fields
        "tags": metadata.get("tags") or "magazine",
        "image": cover_url or metadata.get("image") or "",
        "language": metadata.get("language", "English"),
        "page_count": str(metadata.get("page_count") or ""),
        "book_desc": _ensure_magazine_synopsis(metadata),
        "album_desc": metadata.get("release_notes") or metadata.get("album_desc") or "",
        "release_desc": metadata.get("release_desc") or "",
        "format": metadata.get("format", "PDF"),
        # Source (Retail/Scan/OCR/Convert/Other) -> legacy "bitrate" field
        "bitrate": metadata.get("source") or "Other",
    }
    # Magazines have no author/illustrator roles on the form - do not send artists[].
    if request_id:
        data["requestid"] = str(request_id)
    return data


def compile_data_existing_magazine(
    group_id: int | str, metadata: dict, cover_url: str | None, request_id=None
) -> dict:
    """Existing Magazine Publication - only mutable (issue) fields per Simurg form.

    NOTE: do NOT send a `groupid` alias (see compile_data_existing_publication) -
    Simurg resolves `groupid` as a torrent group id and rejects it.
    """
    raw_type = metadata.get("type", "Magazines")
    type_val = TYPE_MAP.get(raw_type, "7")

    data: dict = {
        "submit": True,
        "type": type_val,
        # Only the publication id is posted (hidden field is `publicationid`).
        "publicationid": str(group_id),
        "title": metadata.get("release_title") or metadata.get("title") or "",
        "year": str(metadata.get("year") or ""),
        "magazine_release_type": metadata.get("release_type") or "Individual Issue",
        "magazine_issue_date": _magazine_issue_date_for_payload(metadata.get("issue_date")),
        "magazine_issue_date_precision": metadata.get("issue_date_precision") or "",
        "magazine_volume": str(metadata.get("volume") or ""),
        "magazine_issue_number": str(metadata.get("issue_number") or ""),
        "format": metadata.get("format", "PDF"),
        "bitrate": metadata.get("source") or "Other",
        "album_desc": metadata.get("release_notes") or "",
        "release_desc": metadata.get("release_desc") or "",
        "image": cover_url or metadata.get("image") or "",
        "tags": metadata.get("tags") or "magazine",
        # Publication-level fields (ignored if read-only when existing selected)
        "magazine_publisher": metadata.get("publisher") or "",
        "magazine_country": metadata.get("country") or "",
        "magazine_frequency": metadata.get("frequency") or "",
        "magazine_print_issn": metadata.get("print_issn") or "",
        "magazine_electronic_issn": metadata.get("electronic_issn") or "",
        "page_count": str(metadata.get("page_count") or ""),
        "language": metadata.get("language", "English"),
        "book_desc": _ensure_magazine_synopsis(metadata),
    }
    if request_id:
        data["requestid"] = str(request_id)
    return data


def build_release_desc(metadata: dict, source_urls: list[str] | None = None) -> str:
    """Build release_desc BBCode per I33."""
    lines = []
    lines.append(f"[b]Source:[/b] {metadata.get('source', 'Other')}")
    lines.append(f"[b]Format:[/b] {metadata.get('format', '')}")
    if metadata.get("page_count"):
        lines.append(f"[b]Page count:[/b] {metadata['page_count']}")
    if metadata.get("publisher"):
        lines.append(f"[b]Publisher:[/b] {metadata['publisher']}")
    if metadata.get("isbn"):
        lines.append(f"[b]ISBN:[/b] {metadata['isbn']}")
    if metadata.get("language"):
        lines.append(f"[b]Language:[/b] {metadata['language']}")
    if source_urls:
        links = " ".join(f"[url={u}]{u}[/url]" for u in source_urls if u)
        if links:
            lines.append(f"[b]More info:[/b] {links}")
        # also include source_urls from metadata
    if metadata.get("source_urls"):
        extra = [u for u in metadata["source_urls"] if u not in (source_urls or [])]
        if extra:
            lines.append("[b]More info:[/b] " + " ".join(f"[url={u}]{u}[/url]" for u in extra))
    if metadata.get("release_notes"):
        lines.append(f"\n{metadata['release_notes']}")
    # Add auto note
    lines.append("\n[i]Uploaded with smoked-simurg v0.1-mlnl[/i]")
    return "\n".join(lines)
