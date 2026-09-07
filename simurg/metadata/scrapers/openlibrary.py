"""OpenLibrary scraper."""

from __future__ import annotations

import re

from simurg.constants import SCRAPER_TIMEOUT
from simurg.metadata.scrapers.base import BaseScraper


class OpenLibraryScraper(BaseScraper):
    name = "openlibrary"
    # Ebook only: magazine path dropped per docs/magazine.txt review
    # (OpenLibrary holds book editions, not stable periodical start-year/ISSN;
    # consumer magazines are hit-or-miss and pollute magazine scrapes).
    categories = {"ebook"}
    url_domains = {"openlibrary.org"}

    def search_isbn(self, isbn: str) -> dict | None:
        cleaned = re.sub(r"[^0-9Xx]", "", isbn)
        try:
            # books API
            url = "https://openlibrary.org/api/books"
            params = {"bibkeys": f"ISBN:{cleaned}", "format": "json", "jscmd": "data"}
            r = self.session.get(url, params=params, timeout=SCRAPER_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                entry = data.get(f"ISBN:{cleaned}")
                if entry:
                    return self._parse_entry(entry, cleaned)
            # fallback search.json — docs carry *work*-level first_publish_year
            url2 = "https://openlibrary.org/search.json"
            r2 = self.session.get(url2, params={"isbn": cleaned}, timeout=SCRAPER_TIMEOUT)
            if r2.status_code == 200:
                docs = r2.json().get("docs", [])
                if docs:
                    d = docs[0]
                    # Edition year not reliably available here; use first_publish_year
                    # as Publication-level field only (see docs/ebook.txt §3 / §4).
                    fpy = d.get("first_publish_year")
                    # Try edition year from publish_year array if present, else None
                    pub_y = None
                    if d.get("publish_year"):
                        try:
                            py_list = d.get("publish_year") or []
                            if py_list:
                                pub_y = int(py_list[0])
                        except Exception:
                            pub_y = None
                    return {
                        "title": d.get("title"),
                        "authors": d.get("author_name", []),
                        "publisher": (d.get("publisher") or [None])[0],
                        "year": pub_y,
                        "publish_year": pub_y,
                        "first_publish_year": fpy,
                        "page_count": d.get("number_of_pages_median"),
                        "isbn": cleaned,
                        "language": (d.get("language") or [None])[0],
                        "subjects": d.get("subject", [])[:10],
                        "description": None,
                        "cover_url": f"https://covers.openlibrary.org/b/isbn/{cleaned}-L.jpg"
                        if d.get("cover_i")
                        else None,
                        "source_urls": [f"https://openlibrary.org/isbn/{cleaned}"],
                    }
        except Exception:
            pass
        return None

    def search_title_author(self, title: str, authors: list[str]) -> dict | None:
        q = title
        if authors:
            q += " " + " ".join(authors[:2])
        try:
            url = "https://openlibrary.org/search.json"
            params = {"q": q, "limit": 5}
            r = self.session.get(url, params=params, timeout=SCRAPER_TIMEOUT)
            if r.status_code == 200:
                docs = r.json().get("docs", [])
                if docs:
                    d = docs[0]
                    isbn = (d.get("isbn") or [None])[0]
                    cleaned = re.sub(r"[^0-9Xx]", "", isbn) if isbn else None
                    fpy = d.get("first_publish_year")
                    pub_y = None
                    if d.get("publish_year"):
                        try:
                            py_list = d.get("publish_year") or []
                            if py_list:
                                pub_y = int(py_list[0])
                        except Exception:
                            pub_y = None
                    return {
                        "title": d.get("title"),
                        "authors": d.get("author_name", []),
                        "publisher": (d.get("publisher") or [None])[0],
                        "year": pub_y,
                        "publish_year": pub_y,
                        "first_publish_year": fpy,
                        "page_count": d.get("number_of_pages_median"),
                        "isbn": cleaned,
                        "language": (d.get("language") or [None])[0],
                        "subjects": d.get("subject", [])[:10],
                        "description": None,
                        "cover_url": f"https://covers.openlibrary.org/b/isbn/{cleaned}-L.jpg"
                        if cleaned and d.get("cover_i")
                        else None,
                        "source_urls": [f"https://openlibrary.org{d.get('key')}"]
                        if d.get("key")
                        else [],
                    }
        except Exception:
            pass
        return None

    def search_magazine(self, title: str, issue: dict | None = None) -> dict | None:
        """Magazine-aware query: best-effort catalogue metadata for a periodical."""
        try:
            url = "https://openlibrary.org/search.json"
            r = self.session.get(url, params={"q": title, "limit": 3}, timeout=SCRAPER_TIMEOUT)
            if r.status_code != 200:
                return None
            for d in r.json().get("docs", []):
                t = d.get("title")
                if not t:
                    continue
                isbn = (d.get("isbn") or [None])[0]
                cleaned = re.sub(r"[^0-9Xx]", "", isbn) if isbn else None
                return {
                    "title": t,
                    "first_published": d.get("first_publish_year"),
                    "publisher": (d.get("publisher") or [None])[0],
                    "country": None,
                    "frequency": None,
                    "language": (d.get("language") or [None])[0],
                    "page_count": d.get("number_of_pages_median"),
                    "print_issn": None,
                    "electronic_issn": None,
                    "cover_url": f"https://covers.openlibrary.org/b/isbn/{cleaned}-L.jpg"
                    if cleaned and d.get("cover_i")
                    else None,
                    "description": None,
                    "source_urls": [f"https://openlibrary.org{d['key']}"] if d.get("key") else [],
                }
            return None
        except Exception:
            return None

    def _parse_entry(self, entry, isbn):
        authors = []
        for a in entry.get("authors", []) or []:
            n = a.get("name")
            if n:
                authors.append(n)
        publishers = entry.get("publishers", []) or []
        publisher = (
            publishers[0].get("name")
            if publishers and isinstance(publishers[0], dict)
            else (publishers[0] if publishers else None)
        )
        year = None
        publish_date = entry.get("publish_date")
        if publish_date:
            m = re.search(r"(\d{4})", str(publish_date))
            if m:
                year = int(m.group(1))
        subjects = []
        for s in entry.get("subjects", []) or []:
            name = s.get("name") if isinstance(s, dict) else str(s)
            if name:
                subjects.append(name)
        desc = entry.get("description")
        if isinstance(desc, dict):
            desc = desc.get("value")
        cover = None
        cover_data = entry.get("cover")
        if cover_data and isinstance(cover_data, dict):
            cover = cover_data.get("large") or cover_data.get("medium")
        if not cover:
            cover = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
        return {
            "title": entry.get("title"),
            "authors": authors,
            "publisher": publisher,
            "year": year,
            "publish_year": year,
            "first_publish_year": None,
            "page_count": entry.get("number_of_pages"),
            "isbn": isbn,
            "language": None,
            "subjects": subjects[:10],
            "description": desc,
            "cover_url": cover,
            "source_urls": [entry.get("url") or f"https://openlibrary.org/isbn/{isbn}"],
        }

    # -- direct URL paste support ------------------------------------------

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted openlibrary.org page URL to metadata.

        Supports /isbn/<isbn>, /books/<OLID> (edition) and /works/<OLID>
        (follows to the first edition). Returns a normalized dict or None.
        """
        from urllib.parse import urlparse

        try:
            path = (urlparse(url).path or "").rstrip("/")
        except Exception:
            return None
        if not path:
            return None
        # /isbn/<isbn> -> reuse the ISBN path
        m = re.search(r"/isbn/([0-9Xx\-]+)$", path)
        if m:
            return self.search_isbn(m.group(1))
        # /books/<OLID> (edition) or /works/<OLID>; trailing title slug ignored
        m = re.search(r"/(books|works)/(OL\d+[A-Za-z0-9]*)(?:/[^/]+)?$", path)
        if not m:
            return None
        kind, olid = m.group(1), m.group(2)
        if kind == "books":
            return self._fetch_book_json(olid)
        # works -> first edition
        try:
            r = self.session.get(
                f"https://openlibrary.org/works/{olid}/editions.json", timeout=SCRAPER_TIMEOUT
            )
            if r.status_code != 200:
                return None
            entries = (r.json().get("entries") or [])[:1]
            if not entries:
                return None
            entry = entries[0]
            # editions.json entries already carry edition-level fields
            return self._parse_edition_json(entry)
        except Exception:
            return None

    def _fetch_book_json(self, olid: str) -> dict | None:
        try:
            r = self.session.get(
                f"https://openlibrary.org/books/{olid}.json", timeout=SCRAPER_TIMEOUT
            )
            if r.status_code != 200:
                return None
            return self._parse_edition_json(r.json())
        except Exception:
            return None

    def _parse_edition_json(self, data: dict) -> dict | None:
        if not isinstance(data, dict):
            return None
        title = data.get("title")
        if not title:
            return None
        authors = []
        for a in data.get("authors", []) or []:
            if isinstance(a, dict):
                n = a.get("name")
                if n:
                    authors.append(n)
            elif isinstance(a, str):
                authors.append(a)
        publishers = data.get("publishers") or []
        publisher = publishers[0] if publishers else None
        if isinstance(publisher, dict):
            publisher = publisher.get("name")
        year = None
        publish_date = data.get("publish_date")
        if publish_date:
            mm = re.search(r"(\d{4})", str(publish_date))
            if mm:
                year = int(mm.group(1))
        isbn = None
        for k in ("isbn_13", "isbn_10"):
            v = data.get(k)
            if not v:
                continue
            raw = v[0] if isinstance(v, list) else v
            cleaned = re.sub(r"[^0-9Xx]", "", str(raw))
            if len(cleaned) in (10, 13):
                isbn = cleaned
                break
        page_count = data.get("number_of_pages")
        subjects = []
        for s in data.get("subjects", []) or []:
            name = s.get("name") if isinstance(s, dict) else str(s)
            if name:
                subjects.append(name)
        desc = data.get("description")
        if isinstance(desc, dict):
            desc = desc.get("value")
        cover = None
        cov = data.get("cover")
        if isinstance(cov, dict):
            cover = cov.get("large") or cov.get("medium")
        if not cover and isbn:
            cover = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
        return {
            "title": title,
            "authors": authors,
            "publisher": publisher,
            "year": year,
            "publish_year": year,
            "first_publish_year": None,
            "page_count": page_count,
            "isbn": isbn,
            "language": None,
            "subjects": subjects[:10],
            "description": desc,
            "cover_url": cover,
            "source_urls": [f"https://openlibrary.org{data['key']}" if data.get("key") else []],
        }
