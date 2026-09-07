"""Internet Archive scraper — individual magazine issues (date, volume, issue, pages, cover)."""

from __future__ import annotations

import re

from simurg.constants import SCRAPER_TIMEOUT
from simurg.metadata.scrapers.base import BaseScraper
from simurg.metadata.scrapers.util import date_precision, normalize_issue_date


class InternetArchiveScraper(BaseScraper):
    name = "internetarchive"
    categories = {"magazine"}
    url_domains = {"archive.org"}

    def search_isbn(self, isbn: str):
        return None

    def search_title_author(self, title: str, authors: list[str]):
        return None

    def search_magazine(self, title: str, issue: dict | None = None) -> dict | None:
        try:
            q = title
            if issue:
                bits = []
                if issue.get("issue_number"):
                    bits.append(f"issue:{issue['issue_number']}")
                if issue.get("volume"):
                    bits.append(f"volume:{issue['volume']}")
                if issue.get("issue_date"):
                    bits.append(str(issue["issue_date"]))
                if bits:
                    q = f"({title}) AND {' AND '.join(bits)}"
            url = "https://archive.org/advancedsearch.php"
            params = {
                "q": q,
                "fl[]": [
                    "identifier",
                    "title",
                    "date",
                    "volume",
                    "issue",
                    "publisher",
                    "number_of_pages",
                    "language",
                ],
                "output": "json",
                "rows": 5,
            }
            r = self.session.get(url, params=params, timeout=SCRAPER_TIMEOUT)
            if r.status_code != 200:
                return None
            docs = r.json().get("response", {}).get("docs", [])
            for d in docs:
                ident = d.get("identifier")
                if not ident:
                    continue
                date = d.get("date")
                norm, prec = normalize_issue_date(date)
                pub = d.get("publisher")
                if isinstance(pub, list):
                    pub = pub[0] if pub else None
                lang = d.get("language")
                if isinstance(lang, list):
                    lang = lang[0] if lang else None
                return {
                    "title": d.get("title") or title,
                    "first_published": None,
                    "print_issn": None,
                    "electronic_issn": None,
                    "publisher": pub,
                    "country": None,
                    "frequency": None,
                    "issue_date": norm,
                    "issue_date_precision": prec or date_precision(date),
                    "volume": d.get("volume"),
                    "issue_number": d.get("issue"),
                    "page_count": d.get("number_of_pages"),
                    "language": lang,
                    "cover_url": f"https://archive.org/services/img/{ident}",
                    "description": None,
                    "source_urls": [f"https://archive.org/details/{ident}"],
                }
            return None
        except Exception:
            return None

    # -- direct URL paste support ------------------------------------------

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted archive.org item URL to metadata.

        Handles ``archive.org/details/<identifier>`` and
        ``archive.org/metadata/<identifier>``. Returns a dict with both the
        common ebook fields (title/year/publisher/page_count/cover) and the
        magazine fields (issue_date/volume/issue) so it works in either path.
        """
        from urllib.parse import urlparse

        try:
            path = (urlparse(url).path or "").rstrip("/")
        except Exception:
            return None
        m = re.search(r"/(?:details|metadata)/([^/]+)$", path)
        if not m:
            return None
        ident = m.group(1)
        try:
            r = self.session.get(f"https://archive.org/metadata/{ident}", timeout=SCRAPER_TIMEOUT)
            if r.status_code != 200:
                return None
            data = r.json()
            meta = data.get("metadata") or {}
            title = meta.get("title")
            if not title:
                return None
            date = meta.get("date")
            norm, prec = normalize_issue_date(date)
            pub = meta.get("publisher")
            if isinstance(pub, list):
                pub = pub[0] if pub else None
            lang = meta.get("language")
            if isinstance(lang, list):
                lang = lang[0] if lang else None
            year = None
            if date:
                ym = re.search(r"(\d{4})", str(date))
                if ym:
                    year = int(ym.group(1))
            return {
                "title": title,
                "authors": meta.get("creator") or [],
                "publisher": pub,
                "year": year,
                "publish_year": year,
                "first_published": None,
                "page_count": meta.get("number_of_pages"),
                "language": lang,
                "subjects": meta.get("subject") or [],
                "description": None,
                "cover_url": f"https://archive.org/services/img/{ident}",
                "issue_date": norm,
                "issue_date_precision": prec,
                "volume": meta.get("volume"),
                "issue_number": meta.get("issue"),
                "source_urls": [f"https://archive.org/details/{ident}"],
            }
        except Exception:
            return None
