"""Base scraper."""

from __future__ import annotations

import abc
from urllib.parse import urlparse

import requests


class BaseScraper(abc.ABC):
    name: str = "base"
    # Which upload categories this scraper serves. Ebook scrapers default to
    # {"ebook"}; magazine scrapers use {"magazine"}; a shared source (e.g.
    # Open Library) uses both. The enricher filters scrapers by this set so a
    # category's scrapers are never invoked from another category's path.
    categories: set[str] = {"ebook"}
    # Netlocs this scraper can resolve a *specific page URL* for (so a user can
    # paste a book page they found online). Empty = URL paste unsupported.
    url_domains: set[str] = set()

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "simurg/0.1.0 (+https://simurg.world)"})

    @abc.abstractmethod
    def search_isbn(self, isbn: str) -> dict | None:
        """Return normalized ebook dict or None.

        Contract (see docs/ebook.txt S7-S12):
          title (str), authors (list), publisher (str|None), isbn (str|None),
          year / publish_year (int|None) - **Release/edition year** (specific
          edition being uploaded),
          first_publish_year (int|None) — **Publication/work year** (original
          first published, Publication-level per docs/ebook.txt:12),
          page_count (int|None) — Release-level, language, subjects/tags,
          description, cover_url, source_urls.
        Only OpenLibrary reliably provides first_publish_year; other scrapers
        may return None for it (handled as unknown in combine).
        """
        ...

    @abc.abstractmethod
    def search_title_author(self, title: str, authors: list[str]) -> dict | None:
        """Same contract as search_isbn, title+author query."""
        ...

    def search_magazine(self, title: str, issue: dict | None = None) -> dict | None:
        """Optional magazine lookup. Ebook-only scrapers return None.

        Magazine scrapers implement this and return a dict that may include:
        title, first_published, print_issn, electronic_issn, publisher,
        country, frequency, issue_date, issue_date_precision, volume,
        issue_number, page_count, language, cover_url, description, source_urls.
        """
        return None

    def match_url(self, url: str) -> bool:
        """Return True if this scraper can resolve a pasted page ``url``."""
        if not self.url_domains:
            return False
        try:
            netloc = (urlparse(url).netloc or "").lower()
        except Exception:
            return False
        if not netloc:
            return False
        return any(netloc == d or netloc.endswith("." + d) for d in self.url_domains)

    def search_url(self, url: str) -> dict | None:
        """Fetch + parse a specific book page ``url`` the user pasted.

        Default: URL paste is unsupported for this scraper. Subclasses with a
        browsable site override this to extract an identifier from ``url`` and
        reuse their existing API/HTML parsing. Returns a tagged-ready dict (the
        enricher attaches ``_scraper`` provenance) or None on any failure.
        """
        return None
