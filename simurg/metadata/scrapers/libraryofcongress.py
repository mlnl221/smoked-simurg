"""Library of Congress scraper — bibliographic verification for magazines."""

from __future__ import annotations

from simurg.metadata.scrapers.base import BaseScraper
from simurg.metadata.scrapers.util import year_from


class LibraryOfCongressScraper(BaseScraper):
    name = "libraryofcongress"
    categories = {"magazine"}
    url_domains = {"loc.gov"}

    def search_isbn(self, isbn: str):
        return None

    def search_title_author(self, title: str, authors: list[str]):
        return None

    def search_magazine(self, title: str, issue: dict | None = None) -> dict | None:
        try:
            url = "https://www.loc.gov/books/"
            params = {"q": title, "fo": "json", "c": 5}
            r = self.session.get(url, params=params, timeout=10)
            if r.status_code != 200:
                return None
            results = r.json().get("results") or []
            for res in results:
                t = res.get("title")
                if not t:
                    continue
                pub = res.get("publisher") or res.get("publisher_name")
                if isinstance(pub, list):
                    pub = pub[0] if pub else None
                dates = res.get("publish_date") or []
                year = year_from(dates[0]) if dates else None
                return {
                    "title": t,
                    "first_published": year,
                    "publisher": pub,
                    "country": None,
                    "frequency": None,
                    "print_issn": None,
                    "electronic_issn": None,
                    "source_urls": [res["id"]] if isinstance(res.get("id"), str) else [],
                }
            return None
        except Exception:
            return None

    # -- direct URL paste support ------------------------------------------

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted loc.gov item/page URL to metadata.

        Appends ``?fo=json`` to the pasted URL and parses the item record
        (title / publisher / publication year). Returns a normalized dict or
        None. Works for ``loc.gov/item/...`` and ``loc.gov/books/...`` pages.
        """
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
        except Exception:
            return None
        if "loc.gov" not in (parsed.netloc or "").lower():
            return None
        target = url.split("?")[0].rstrip("/") + "?fo=json"
        try:
            r = self.session.get(target, timeout=10)
            if r.status_code != 200:
                return None
            res = r.json()
            t = res.get("title")
            if not t:
                return None
            pub = res.get("publisher") or res.get("publisher_name")
            if isinstance(pub, list):
                pub = pub[0] if pub else None
            dates = res.get("publish_date") or []
            year = year_from(dates[0]) if dates else None
            return {
                "title": t,
                "first_published": year,
                "year": year,
                "publish_year": year,
                "publisher": pub,
                "country": None,
                "frequency": None,
                "print_issn": None,
                "electronic_issn": None,
                "cover_url": None,
                "source_urls": [res.get("id") or url] if isinstance(res.get("id"), str) else [url],
            }
        except Exception:
            return None
