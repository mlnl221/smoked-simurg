"""BookBrainz scraper - via the public REST API.

The BookBrainz frontend is a JS SPA, so plain `requests` cannot scrape the
search pages; the API at https://api.bookbrainz.org/1/ is the supported path.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from simurg.constants import SCRAPER_TIMEOUT
from simurg.metadata.scrapers.base import BaseScraper


class BookBrainzScraper(BaseScraper):
    name = "bookbrainz"
    api_base = "https://api.bookbrainz.org/1"
    url_domains = {"bookbrainz.org"}

    def search_isbn(self, isbn: str) -> dict | None:
        cleaned = re.sub(r"[^0-9Xx]", "", isbn)
        return self._search(cleaned)

    def search_title_author(self, title: str, authors: list[str]) -> dict | None:
        q = title
        if authors:
            q += " " + " ".join(str(a) for a in authors[:2])
        return self._search(q)

    def _search(self, query: str) -> dict | None:
        try:
            r = self.session.get(
                f"{self.api_base}/search",
                params={"q": query, "type": "edition", "page": 1, "size": 5},
                timeout=SCRAPER_TIMEOUT,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            hits = data.get("searchResult") or []
            if not hits:
                return None
            bbid = hits[0].get("bbid")
            alias = (hits[0].get("defaultAlias") or {}).get("name")
            return self._lookup_edition(bbid, fallback_title=alias)
        except Exception:
            return None

    def _lookup_edition(self, bbid: str, fallback_title: str | None = None) -> dict | None:
        try:
            r = self.session.get(f"{self.api_base}/edition/{bbid}", timeout=SCRAPER_TIMEOUT)
            if r.status_code != 200:
                return None
            data = r.json()
            title = (data.get("defaultAlias") or {}).get("name") or fallback_title
            year = None
            red = data.get("releaseEventDate")
            if red:
                # ISO extended format like "+002005-09" (zero-padded year -> 2005)
                m = re.match(r"^\+?(\d+)", str(red))
                if m:
                    year = int(m.group(1))
            authors = []
            for n in (data.get("authorCredits") or {}).get("names") or []:
                name = n.get("name")
                if name:
                    authors.append(name)
            languages = data.get("languages") or []
            publisher = None
            pub = data.get("publishers")
            if isinstance(pub, list) and pub:
                p0 = pub[0]
                publisher = p0.get("name") if isinstance(p0, dict) else str(p0)
            elif isinstance(pub, dict) and pub.get("name"):
                publisher = pub.get("name")
            isbn = None
            try:
                r2 = self.session.get(
                    f"{self.api_base}/edition/{bbid}/identifiers", timeout=SCRAPER_TIMEOUT
                )
                if r2.status_code == 200:
                    for ident in r2.json().get("identifiers") or []:
                        if ident.get("type") in ("ISBN-13", "ISBN-10") and ident.get("value"):
                            isbn = re.sub(r"[^0-9Xx]", "", str(ident["value"]))
                            break
            except Exception:
                pass
            return {
                "title": title,
                "authors": authors,
                "publisher": publisher,
                "year": year,
                "publish_year": year,
                "first_publish_year": None,
                "page_count": data.get("pages"),
                "isbn": isbn,
                "language": languages[0] if languages else None,
                "subjects": [],
                "description": None,
                "cover_url": self._scrape_cover(bbid),
                "source_urls": [f"https://bookbrainz.org/edition/{bbid}"],
            }
        except Exception:
            return None

    def _scrape_cover(self, bbid: str) -> str | None:
        """Fetch the edition HTML page and extract the cover image URL."""
        try:
            r = self.session.get(f"https://bookbrainz.org/edition/{bbid}", timeout=SCRAPER_TIMEOUT)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            img = soup.find("img", class_="edition-cover-image")
            if img and img.get("src"):
                return img["src"]
        except Exception:
            pass
        return None

    # -- direct URL paste support ------------------------------------------

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted bookbrainz.org URL to metadata.

        Supports ``/edition/<bbid>`` (used directly), plus ``/work/<bbid>`` and
        ``/book/<bbid>`` (follows to the first edition via the API).
        """
        from urllib.parse import urlparse

        try:
            path = (urlparse(url).path or "").rstrip("/")
        except Exception:
            return None
        m = re.search(
            r"/(edition|work|book)/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/[^/]+)?$",
            path,
        )
        if not m:
            return None
        kind, bbid = m.group(1), m.group(2)
        if kind == "edition":
            return self._lookup_edition(bbid)
        # work/book -> fetch first edition
        try:
            r = self.session.get(f"{self.api_base}/{kind}/{bbid}/editions", timeout=SCRAPER_TIMEOUT)
            if r.status_code != 200:
                return None
            editions = (r.json().get("editions") or [])[:1]
            if not editions:
                return None
            ed_bbid = editions[0].get("bbid")
            if not ed_bbid:
                return None
            return self._lookup_edition(ed_bbid)
        except Exception:
            return None
