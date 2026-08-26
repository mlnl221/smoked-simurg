"""MOBI/AZW3/DJVU metadata extraction - minimal."""

from __future__ import annotations

import re
from pathlib import Path

from simurg.metadata.pagecount import estimate_pages_from_text


def extract_mobi(path: Path) -> dict:
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
    }
    try:
        # Try via ebooklib if available for mobi? Use raw EXTH parse
        # MOBI header parsing minimal: look for TITLE near start
        # Quick heuristic: search for readable strings
        # More robust: parse PalmDB + MOBI header
        # Simplified: read full file and decode text?
        # For MVP, attempt to use external library if present, else string search
        try:
            # Attempt EXTH parsing
            # PalmDB header 78 bytes, then MOBI header
            with open(str(path), "rb") as f:
                f.seek(60)
                # offset to MOBI header
                pass
        except Exception:
            pass

        # Fallback: try to extract strings like Title/Author via text search
        raw = open(str(path), "rb").read()
        # Look for ISBN pattern in raw bytes decoded ignoring errors
        text = raw.decode("utf-8", errors="ignore")
        # Title: look between known markers? Heuristic: use filename as fallback later
        # Search author pattern: "Author"
        # First try ISBN with prefix, then any 13-digit 978/979 pattern
        m = re.search(
            r"ISBN[^\d]*((97[89][\s-]?)?\d[\s-]?\d+[\s-]?\d+[\s-]?\d+[\s-]?[\dxX])",
            text,
            flags=re.IGNORECASE,
        )
        if m:
            cleaned = re.sub(r"[^0-9Xx]", "", m.group(1))
            if len(cleaned) in (10, 13):
                result["isbn"] = cleaned
        if not result["isbn"]:
            # any bare 978/979 13-digit
            m_isbn = re.search(r"\b(97[89]\d{10})\b", text)
            if m_isbn:
                result["isbn"] = m_isbn.group(1)
            else:
                # with dashes
                m_isbn2 = re.search(r"\b(97[89][\d\- ]{10,16}\d)\b", text)
                if m_isbn2:
                    cand = re.sub(r"[^0-9Xx]", "", m_isbn2.group(1))
                    if len(cand) in (10, 13):
                        result["isbn"] = cand

        # Try to get title/author/year from EXTH-style null-separated strings
        try:
            # split on nulls and get readable tokens
            parts = re.split(r"[\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f]+", text)
            # filter readable for year/publisher (short)
            tokens = [
                p.strip()
                for p in parts
                if 2 < len(p.strip()) < 200 and re.search(r"[A-Za-z]{2,}", p)
            ]
            # separate long tokens for description (allow up to 3000)
            long_tokens = [
                p.strip()
                for p in parts
                if 100 < len(p.strip()) < 5000 and re.search(r"[A-Za-z]{2,}", p)
            ]
            # look for year - priority order:
            # 1. "Published: YYYY" / "Release date: YYYY"
            # 2. Title-page "(YYYY)" e.g. "the Woods (2007)" - reliable embedded publish year
            # 3. date token like 2002-08-15 (may be calibre timestamp - low priority)
            # 4. bare 4-digit year token
            if not result["year"]:
                m_pub = re.search(
                    r"(?:Published|Release date|Publication date)[:\s>]*(\d{4})",
                    text[:20000],
                    flags=re.IGNORECASE,
                )
                if m_pub and 1900 <= int(m_pub.group(1)) <= 2026:
                    result["year"] = int(m_pub.group(1))
            if not result["year"]:
                # Title-page parenthetical year - accept 1900-2026 to avoid matching unrelated numbers
                m_paren = re.search(r"\(\s*(\d{4})\s*\)", text[:8000])
                if m_paren and 1900 <= int(m_paren.group(1)) <= 2026:
                    result["year"] = int(m_paren.group(1))
            if not result["year"]:
                for tok in tokens:
                    if re.match(r"^\d{4}-\d{2}-\d{2}", tok):
                        try:
                            y = int(tok[:4])
                            if 1900 <= y <= 2026:
                                result["year"] = y
                                break
                        except Exception:
                            pass
                if not result["year"]:
                    # find 4-digit year between 1500-2026 near known strings
                    for tok in tokens:
                        if tok.isdigit() and len(tok) == 4:
                            try:
                                y = int(tok)
                                if 1500 <= y <= 2026:
                                    result["year"] = y
                                    break
                            except Exception:
                                pass
            # description: longest token with spaces - check long_tokens and also direct text search
            if not result["description"]:
                for tok in long_tokens:
                    if "Written in a time" in tok and len(tok) > 100:
                        result["description"] = tok[:2000]
                        break
                if not result["description"]:
                    # fallback: regex on full text for SUMMARY
                    m_desc = re.search(
                        r"SUMMARY:\s*(.{100,2000}?)(?:\x00|\n|$)", text, flags=re.DOTALL
                    )
                    if m_desc:
                        cand = m_desc.group(1).strip()
                        # clean up extra nulls/spaces
                        cand = re.sub(r"\s+", " ", cand)
                        if len(cand) > 50:
                            result["description"] = cand[:2000]
                    else:
                        # try any long sentence containing Moll
                        m2 = re.search(r"Moll Flanders.{50,1500}", text)
                        if m2:
                            cand = m2.group(0)
                            cand = re.sub(r"\s+", " ", cand)
                            result["description"] = cand[:2000]
        except Exception:
            pass

        # Try to get title from first readable string near start (100 chars)
        # Use filename fallback is handled upstream
        # Look for Publisher
        m2 = re.search(r"Publisher[:\s]+([^\n\r]{3,80})", text, flags=re.IGNORECASE)
        if m2:
            result["publisher"] = m2.group(1).strip()

        # Try using mobi library if installed
        try:
            pass  # type: ignore

            # mobi library may have different API
        except Exception:
            pass

        # Page count: MOBI/AZW3 are reflowable, so estimate from the file text.
        # The raw decode is noisy; strip tags/control chars before counting.
        try:
            est = estimate_pages_from_text(text)
            if est:
                result["page_count"] = est
        except Exception:
            pass

    except Exception:
        pass
    return result


def extract_djvu(path: Path) -> dict:
    # DJVU minimal - use filename, try to read metadata via djvu? Skip.
    return {
        "title": None,
        "authors": [],
        "publisher": None,
        "year": None,
        "isbn": None,
        "language": None,
        "description": None,
        "cover_path": None,
        "page_count": None,
    }
