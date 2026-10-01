"""Bing image-search cover fallback (no key, plain HTML GET).

Extracts direct ``murl`` + hotlink-safe thumbnail ``turl`` URLs from the
result page. Amazon CDN hits and tse thumbnails are preferred; every
candidate passes blank-aware validation before use.
"""

from __future__ import annotations

import html as _html
import re
import tempfile

import requests

from simurg.constants import SCRAPER_TIMEOUT

_SEARCH_URL = "https://www.bing.com/images/search"

# First N candidates download-tested per query (blank-aware reject is cheap).
_MAX_CANDIDATES = 6


def extract_image_urls(page_html: str) -> list[str]:
    """Pull (murl/turl) image URLs from a Bing images result page, ordered.

    Amazon CDN originals first, then tse thumbnails, then the rest.
    """
    text = _html.unescape(page_html)
    murls = re.findall(r'"murl":"(https?://[^"]+)"', text)
    turls = re.findall(r'"turl":"(https?://[^"]+)"', text)
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        url = url.replace("\\/", "/")
        if url.startswith("http") and url not in seen:
            seen.add(url)
            ordered.append(url)

    for url in murls:
        if "media-amazon.com" in url or "ssl-images-amazon" in url:
            _add(url)
    for url in turls:
        _add(url)
    for url in murls:
        _add(url)
    return ordered


def fetch_bing_cover(
    title: str, authors: list[str], session: requests.Session | None = None
) -> str | None:
    """Query Bing images for a book cover, return first valid temp path."""
    if not title:
        return None
    query = f"{' '.join(authors[:1])} {title} book cover" if authors else f"{title} book cover"
    session = session or requests.Session()
    try:
        resp = session.get(
            _SEARCH_URL,
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=SCRAPER_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        from simurg.images.validate import MIN_COVER_BYTES, is_valid_cover

        for url in extract_image_urls(resp.text)[:_MAX_CANDIDATES]:
            try:
                img = session.get(
                    url, headers={"User-Agent": "Mozilla/5.0"}, timeout=SCRAPER_TIMEOUT
                )
                if img.status_code != 200:
                    continue
                ctype = img.headers.get("Content-Type", "")
                if ctype and not ctype.startswith("image/"):
                    continue
                if len(img.content) < MIN_COVER_BYTES:
                    continue
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tmp.write(img.content)
                tmp.close()
                valid, _ = is_valid_cover(tmp.name)
                if not valid:
                    import os

                    os.unlink(tmp.name)
                    continue
                return tmp.name
            except Exception:
                continue
    except Exception:
        pass
    return None
