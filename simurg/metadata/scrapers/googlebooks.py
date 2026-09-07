"""Google Books scraper.

Primary path: the official volumes API (https://www.googleapis.com/books/v1/volumes),
which works without an API key (lower quota) and accepts a ``key`` from config when set.

Fallback path: when the API returns no usable item (or rate-limits us), query the
keyless Google Books "view API" (``jscmd=viewapi``) which returns a basic record and a
cover thumbnail without any key.
"""

from __future__ import annotations

import html
import json
import re
from difflib import SequenceMatcher

from simurg.constants import SCRAPER_TIMEOUT
from simurg.metadata.scrapers.base import BaseScraper

_API = "https://www.googleapis.com/books/v1/volumes"
_VIEWAPI = "https://books.google.com/books"


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _clean_isbn(value) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", str(value))
    return cleaned if len(cleaned) in (10, 13) else None


def _upgrade_cover(image_links: dict) -> str | None:
    """Return a higher-resolution cover URL from the imageLinks dict.

    Google's ``thumbnail`` defaults to ``zoom=1`` with curled page edges; bump the
    zoom level and drop the ``edge=curl`` cosmetic param so the cover rehoster gets
    a crisper image.
    """
    raw = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if not raw:
        return None
    url = raw.replace("http://", "https://")
    url = re.sub(r"zoom=\d+", "zoom=3", url)
    url = re.sub(r"&?edge=curl", "", url)
    return url


def _clean_description(desc) -> str | None:
    if not desc:
        return None
    text = str(desc)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)  # keep line breaks as spaces
    text = re.sub(r"<[^>]+>", "", text)  # strip any remaining HTML tags
    text = html.unescape(text)  # decode &amp; &quot; etc.
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


class GoogleBooksScraper(BaseScraper):
    name = "googlebooks"
    url_domains = {"books.google.com", "googleapis.com"}

    # -- shared helpers ------------------------------------------------------

    def _api_key(self) -> str:
        try:
            from simurg.config import get_config

            cfg = get_config()
            return cfg.metadata.get("googlebooks_key", "") or cfg.metadata.get(
                "google_books_key", ""
            )
        except Exception:
            return ""

    def _api_get_items(self, params: dict) -> list:
        """GET the volumes API; retry once on 429/5xx. Return the items list."""
        if self._api_key():
            params["key"] = self._api_key()
        for attempt in range(2):
            try:
                r = self.session.get(_API, params=params, timeout=SCRAPER_TIMEOUT)
            except Exception:
                return []
            if r.status_code == 200:
                return r.json().get("items", []) or []
            if r.status_code in (429, 500, 502, 503) and attempt == 0:
                continue
            return []
        return []

    # -- public API ----------------------------------------------------------

    def search_isbn(self, isbn: str) -> dict | None:
        cleaned = _clean_isbn(isbn)
        if not cleaned:
            return None
        vi = self._api_volume_by_isbn(cleaned)
        if vi is None:
            vi = self._viewapi_volume(cleaned)
        if not vi:
            return None
        return self._parse_volume(vi, preferred_isbn=cleaned)

    def search_title_author(self, title: str, authors: list[str]) -> dict | None:
        q = f"intitle:{title}"
        if authors:
            q += f" inauthor:{authors[0]}"
        items = self._api_get_items({"q": q, "maxResults": 5})
        vi = self._best_title_match(items, title)
        if vi is None:
            return None
        return self._parse_volume(vi)

    # -- API parsing ---------------------------------------------------------

    def _api_volume_by_isbn(self, cleaned: str):
        items = self._api_get_items({"q": f"isbn:{cleaned}"})
        if not items:
            return None
        # Prefer the volume whose identifiers actually include the query ISBN
        # (the API can return several editions for a given ISBN).
        for it in items:
            vi = it.get("volumeInfo", {})
            for ident in vi.get("industryIdentifiers", []) or []:
                if _clean_isbn(ident.get("identifier")) == cleaned:
                    return vi
        return items[0].get("volumeInfo", {})

    @staticmethod
    def _best_title_match(items: list, title: str):
        if not items:
            return None
        best, best_score = None, -1.0
        for it in items:
            vi = it.get("volumeInfo", {})
            score = _fuzzy(title, vi.get("title") or "")
            if score > best_score:
                best, best_score = vi, score
        return best

    # -- keyless viewapi fallback -------------------------------------------

    def _viewapi_volume(self, cleaned: str) -> dict | None:
        try:
            params = {"jscmd": "viewapi", "bibkeys": f"ISBN:{cleaned}"}
            r = self.session.get(_VIEWAPI, params=params, timeout=SCRAPER_TIMEOUT)
            if r.status_code != 200:
                return None
            # The endpoint returns JavaScript: "var _GBSBookInfo = {...};"
            # but some proxies/older responses return plain JSON, so try both.
            data = None
            try:
                data = r.json()
            except Exception:
                text = r.text.strip()
                start, end = text.find("{"), text.rfind("}")
                if start == -1 or end == -1:
                    return None
                data = json.loads(text[start : end + 1])
            if not isinstance(data, dict):
                return None
            entry = data.get(f"ISBN:{cleaned}")
            if not entry:
                return None
            pd = entry.get("published_year")
            publishers = entry.get("publishers") or []
            publisher = publishers[0] if publishers else None
            subjects = []
            for c in entry.get("subjects", []) or []:
                if isinstance(c, dict):
                    subjects.append(c.get("name") or c.get("value"))
                elif c:
                    subjects.append(str(c))
            return {
                "title": entry.get("title"),
                "authors": entry.get("authors") or [],
                "publisher": publisher,
                "publishedDate": str(pd) if pd else None,
                "pageCount": entry.get("page_count") or entry.get("pages"),
                "categories": [s for s in subjects if s],
                "description": entry.get("description") or entry.get("synopsis"),
                "imageLinks": (
                    {"thumbnail": entry["thumbnail_url"]} if entry.get("thumbnail_url") else {}
                ),
                "infoLink": entry.get("info_url"),
                "industryIdentifiers": [{"type": "ISBN_13", "identifier": cleaned}],
            }
        except Exception:
            return None

    # -- shared volume -> result mapping ------------------------------------

    def _parse_volume(self, vi, preferred_isbn: str | None = None) -> dict | None:
        if not vi or not vi.get("title"):
            return None
        title = vi.get("title")
        authors = vi.get("authors", []) or []
        publisher = vi.get("publisher")
        year = None
        pd = vi.get("publishedDate")
        if pd:
            m = re.search(r"(\d{4})", str(pd))
            if m:
                year = int(m.group(1))
        page_count = vi.get("pageCount")
        subjects = vi.get("categories", []) or []
        desc = _clean_description(vi.get("description"))
        cover = _upgrade_cover(vi.get("imageLinks") or {})
        info_link = vi.get("infoLink")
        # Prefer a real ISBN_13 from the record, fall back to ISBN_10, then the query.
        isbn = None
        for ident in vi.get("industryIdentifiers", []) or []:
            t = ident.get("type")
            val = _clean_isbn(ident.get("identifier"))
            if not val:
                continue
            if t == "ISBN_13":
                isbn = val
                break
            if t == "ISBN_10" and not isbn:
                isbn = val
        if not isbn and preferred_isbn:
            isbn = preferred_isbn
        return {
            "title": title,
            "authors": authors,
            "publisher": publisher,
            "year": year,
            "publish_year": year,
            "first_publish_year": None,
            "page_count": page_count,
            "isbn": isbn,
            "language": vi.get("language"),
            "subjects": subjects[:10],
            "description": desc,
            "cover_url": cover,
            "source_urls": [info_link] if info_link else [],
        }

    # -- direct URL paste support ------------------------------------------

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted Google Books URL to metadata.

        Handles ``books.google.com/books?id=<volumeId>`` and the raw API URL
        ``googleapis.com/books/v1/volumes/<volumeId>``.
        """
        from urllib.parse import parse_qs, urlparse

        try:
            parsed = urlparse(url)
        except Exception:
            return None
        volume_id = None
        # API direct URL: /books/v1/volumes/<id>
        m = re.search(r"/volumes/([^/?#]+)", parsed.path)
        if m:
            volume_id = m.group(1)
        # Web URL: ?id=<id>
        if not volume_id:
            qs = parse_qs(parsed.query or "")
            ids = qs.get("id") or qs.get("vid")
            if ids:
                volume_id = ids[0]
        if not volume_id:
            return None
        try:
            api_url = f"https://www.googleapis.com/books/v1/volumes/{volume_id}"
            params = {}
            try:
                from simurg.config import get_config

                cfg = get_config()
                api_key = cfg.metadata.get("googlebooks_key", "") or cfg.metadata.get(
                    "google_books_key", ""
                )
                if api_key:
                    params["key"] = str(api_key)
            except Exception:
                pass
            r = self.session.get(api_url, params=params, timeout=SCRAPER_TIMEOUT)
            if r.status_code != 200:
                return None
            vi = r.json().get("volumeInfo", {})
            if not vi.get("title"):
                return None
            isbn = None
            for ident in vi.get("industryIdentifiers", []) or []:
                if ident.get("type") in ("ISBN_13", "ISBN_10"):
                    isbn = ident.get("identifier")
                    break
            cleaned = re.sub(r"[^0-9Xx]", "", isbn) if isbn else None
            return self._parse_volume(vi, cleaned)
        except Exception:
            return None
