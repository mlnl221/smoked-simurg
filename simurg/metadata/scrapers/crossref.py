"""Crossref scraper — periodical metadata where indexed (e.g. National Geographic)."""

from __future__ import annotations

from simurg.constants import SCRAPER_TIMEOUT
from simurg.metadata.scrapers.base import BaseScraper
from simurg.metadata.scrapers.util import clean_issn


class CrossrefScraper(BaseScraper):
    name = "crossref"
    categories = {"magazine"}

    def search_isbn(self, isbn: str):
        return None

    def search_title_author(self, title: str, authors: list[str]):
        return None

    def search_magazine(self, title: str, issue: dict | None = None) -> dict | None:
        try:
            url = "https://api.crossref.org/works"
            params = {"query.bibliographic": title, "rows": 5, "filter": "type:journal"}
            r = self.session.get(url, params=params, timeout=SCRAPER_TIMEOUT)
            if r.status_code != 200:
                return None
            items = r.json().get("message", {}).get("items", [])
            for it in items:
                t = it.get("title")
                if isinstance(t, list):
                    t = t[0] if t else None
                if not t:
                    continue
                issns = it.get("ISSN") or []
                publisher = it.get("publisher")
                # Crossref issued date is the article year, not the
                # periodical's first-published year (docs/magazine.txt §4).
                return {
                    "title": t,
                    "first_published": None,
                    "print_issn": clean_issn(issns[0]) if len(issns) >= 1 else None,
                    "electronic_issn": clean_issn(issns[1]) if len(issns) >= 2 else None,
                    "publisher": publisher,
                    "country": None,
                    "frequency": None,
                    "source_urls": [it["URL"]] if isinstance(it.get("URL"), str) else [],
                }
            return None
        except Exception:
            return None
