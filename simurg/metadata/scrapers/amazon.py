"""Amazon cover images via the legacy ISBN image URL (no key, no scraping).

Pattern ``https://images.amazon.com/images/P/{ISBN10}.01.LZZZZZZZ.jpg``
serves the edition cover directly; unknown ISBNs return a tiny image that
blank/size validation rejects. Direct Amazon page scraping stays off-limits
(captcha bot-wall).
"""

from __future__ import annotations

import re
import tempfile

import requests

from simurg.constants import SCRAPER_TIMEOUT

# images.amazon.com 403s bare/library UAs — browser headers required.
_AMAZON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://www.amazon.com/",
}


def isbn_to_isbn10(isbn: str | None) -> str | None:
    """Normalize an ISBN-10/13 to ISBN-10. Returns None when impossible."""
    if not isbn:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", str(isbn)).upper()
    if len(cleaned) == 10:
        return cleaned
    if len(cleaned) == 13 and cleaned.startswith("978"):
        body = cleaned[3:12]
        total = sum((10 - i) * int(d) for i, d in enumerate(body))
        check = (11 - (total % 11)) % 11
        return body + ("X" if check == 10 else str(check))
    return None


def amazon_cover_url(isbn: str | None) -> str | None:
    """Return the legacy Amazon cover URL for an ISBN, or None."""
    isbn10 = isbn_to_isbn10(isbn)
    if not isbn10:
        return None
    return f"https://images.amazon.com/images/P/{isbn10}.01.LZZZZZZZ.jpg"


def fetch_amazon_cover(
    title: str, authors: list[str], isbn: str | None, session: requests.Session | None = None
) -> str | None:
    """Download the Amazon cover for an ISBN to temp, return path or None."""
    del title, authors  # ISBN-deterministic; no text search involved
    url = amazon_cover_url(isbn)
    if not url:
        return None
    session = session or requests.Session()
    try:
        resp = session.get(url, headers=_AMAZON_HEADERS, timeout=SCRAPER_TIMEOUT)
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("Content-Type", "")
        if ctype and not ctype.startswith("image/"):
            return None
        from simurg.images.validate import MIN_COVER_BYTES, is_valid_cover

        if len(resp.content) < MIN_COVER_BYTES:
            return None
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp.write(resp.content)
        tmp.close()
        valid, _ = is_valid_cover(tmp.name)
        if not valid:
            import os

            os.unlink(tmp.name)
            return None
        return tmp.name
    except Exception:
        return None
