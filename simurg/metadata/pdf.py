"""PDF metadata extraction via pypdf."""

from __future__ import annotations

import re
from pathlib import Path


def extract_pdf(path: Path) -> dict:
    result: dict = {
        "title": None,
        "authors": [],
        "publisher": None,
        "year": None,
        "isbn": None,
        "language": None,
        "description": None,
        "cover_path": None,
        "page_count": None,
        "is_encrypted": False,
    }
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            result["is_encrypted"] = True
            return result
        try:
            result["page_count"] = len(reader.pages)
        except Exception:
            pass
        info = reader.metadata
        if info:
            # info is DocumentInformation with attributes
            title = (
                getattr(info, "title", None) or info.get("/Title", None)
                if hasattr(info, "get")
                else None
            )
            author = (
                getattr(info, "author", None) or info.get("/Author", None)
                if hasattr(info, "get")
                else None
            )
            subject = (
                getattr(info, "subject", None) or info.get("/Subject", None)
                if hasattr(info, "get")
                else None
            )
            creator = (
                getattr(info, "creator", None) or info.get("/Creator", None)
                if hasattr(info, "get")
                else None
            )
            creation_date = (
                getattr(info, "creation_date", None) or info.get("/CreationDate", None)
                if hasattr(info, "get")
                else None
            )

            if title:
                title = str(title).strip()
                if title:
                    result["title"] = title
            if author:
                author = str(author).strip()
                if author:
                    # split authors
                    result["authors"] = _split_authors(author)
            if subject:
                subject = str(subject).strip()
                if subject:
                    result["description"] = subject
            if creator:
                creator = str(creator).strip()
                # producer/creator hint for source; store publisher if available
                # weak heuristic: if creator looks like publisher, keep
                pass
            if creation_date:
                try:
                    # creation_date may be datetime
                    import datetime

                    if isinstance(creation_date, datetime.datetime):
                        result["year"] = creation_date.year
                    else:
                        m = re.search(r"(\d{4})", str(creation_date))
                        if m:
                            result["year"] = int(m.group(1))
                except Exception:
                    m = re.search(r"(\d{4})", str(creation_date))
                    if m:
                        result["year"] = int(m.group(1))
        # also try to extract ISBN from text of first few pages?
        # lightweight: read first page text if available
        try:
            if reader.pages:
                text = ""
                for i in range(min(2, len(reader.pages))):
                    try:
                        text += reader.pages[i].extract_text() or ""
                    except Exception:
                        pass
                # search ISBN
                m = re.search(
                    r"ISBN[^\d]*((97[89][\s-]?)?\d[\s-]?\d+[\s-]?\d+[\s-]?\d+[\s-]?[\dxX])",
                    text,
                    flags=re.IGNORECASE,
                )
                if m:
                    cleaned = re.sub(r"[^0-9Xx]", "", m.group(1))
                    if len(cleaned) in (10, 13):
                        result["isbn"] = cleaned
        except Exception:
            pass

    except Exception as e:
        # if pypdf not installed or fails, return minimal
        result["error"] = str(e)
    return result


def _split_authors(s: str):
    # split on common separators
    parts = re.split(r"\s*;\s*|\s*,\s*|\s+and\s+|\s*&\s*", s)
    # Heuristic: if comma split results in single words like "Herbert, Frank" -> join?
    # For now naive; combine will handle dedup.
    authors = [p.strip() for p in parts if p.strip()]
    # If we have "Last, First" pattern with 2 parts and authors length 2 and each single word? Keep as "First Last"?
    # Leave as is; single author "Herbert, Frank" will be one string with comma; we split above incorrectly.
    # Detect: if original contains "," and len(authors)==2 and all(" " not in a for a in authors):
    # then join reversed.
    if "," in s and len(authors) == 2 and all(" " not in a for a in authors):
        authors = [f"{authors[1]} {authors[0]}"]
    return authors
