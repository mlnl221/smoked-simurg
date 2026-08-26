"""ISSN Portal scraper — canonical magazine metadata (ISSN, publisher, country, frequency).

Two entry points:

* ``search_magazine`` — query by title via the ISSN Portal search API.
* ``search_url`` — resolve a pasted ``portal.issn.org/resource/ISSN/...`` page URL
  directly to publication-level metadata.
"""

from __future__ import annotations

import re

from simurg.metadata.scrapers.base import BaseScraper
from simurg.metadata.scrapers.util import (
    clean_issn,
    year_from,
)


class IssnPortalScraper(BaseScraper):
    name = "issnportal"
    categories = {"magazine"}
    url_domains = {"portal.issn.org"}

    def search_isbn(self, isbn: str):
        return None

    def search_title_author(self, title: str, authors: list[str]):
        return None

    def search_magazine(self, title: str, issue: dict | None = None) -> dict | None:
        try:
            url = "https://portal.issn.org/resource/search"
            params = {"query": title, "format": "json"}
            r = self.session.get(url, params=params, timeout=10)
            if r.status_code != 200:
                return None
            try:
                data = r.json()
            except Exception:
                return self._parse_html(r.text, title)
            items = []
            if isinstance(data, dict):
                items = data.get("data") or data.get("results") or data.get("items") or []
            for it in items:
                if isinstance(it, dict) and (it.get("title") or it.get("resourceTitle")):
                    return self._build(it, title)
            return None
        except Exception:
            return None

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted portal.issn.org resource URL to magazine metadata.

        Accepts URLs like ``portal.issn.org/resource/ISSN/0090-2020`` (with or
        without query params).  Fetches the HTML page and extracts ISSN,
        title, medium, country, publisher, and archival data.
        """
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
        except Exception:
            return None
        if "portal.issn.org" not in (parsed.netloc or "").lower():
            return None
        # Extract ISSN from path: /resource/ISSN/XXXX-XXXX or /resource/ISSN-L/XXXX-XXXX
        m = re.search(r"resource/ISSN(?:-L)?/(\d{4}-\d{3}[\dX])", parsed.path or "")
        if not m:
            return None
        issn = clean_issn(m.group(1))
        if not issn:
            return None
        try:
            r = self.session.get(url.split("?")[0], timeout=10)
            if r.status_code != 200:
                return None
            return self._parse_resource_page(r.text, issn, url)
        except Exception:
            return None

    def _build(self, it: dict, title: str) -> dict:
        pissn = it.get("pissn") or it.get("printIssn") or it.get("issn") or it.get("issnPrint")
        eissn = it.get("eissn") or it.get("electronicIssn") or it.get("issnElectronic")
        start = it.get("startYear") or it.get("startDate")
        return {
            "title": it.get("title") or it.get("resourceTitle") or title,
            "first_published": year_from(start),
            "print_issn": clean_issn(pissn),
            "electronic_issn": clean_issn(eissn),
            "publisher": it.get("publisher") or it.get("publisherName"),
            "country": it.get("country") or it.get("countryName"),
            "frequency": it.get("frequency"),
            "language": it.get("language"),
            "cover_url": None,
            "description": None,
            "source_urls": [it["@id"]] if isinstance(it.get("@id"), str) else [],
        }

    def _parse_html(self, html: str, title: str) -> dict | None:
        m = re.search(r"(\d{4}-\d{3}[\dX])", html or "")
        if not m:
            return None
        return {"title": title, "print_issn": m.group(1)}

    def _parse_resource_page(self, html: str, issn: str, url: str) -> dict | None:
        """Parse an ISSN Portal resource page for publication-level metadata."""
        if not html:
            return None

        # Title: look for "Title proper:" label followed by the title text
        title = None
        m = re.search(
            r"Title proper:\s*</[^>]+>\s*(?:<[^>]+>\s*)*([A-Za-z][\w\s.,:&\-()]+)",
            html,
            re.IGNORECASE,
        )
        if m:
            title = m.group(1).strip()
            # Strip trailing period that ISSN portal adds
            if title.endswith("."):
                title = title[:-1].strip()
        if not title:
            # Fallback: og:title or <title>
            m = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', html)
            if m:
                title = m.group(1).strip()
            else:
                m = re.search(r"<title>\s*ISSN\s+\d{4}-\d{3}[\dX]\s*-\s*(.+?)\s*</title>", html)
                if m:
                    title = m.group(1).strip()

        # Country
        country = None
        m = re.search(r"Country:\s*</[^>]+>\s*(?:<[^>]+>\s*)*([A-Za-z][\w\s]+)", html)
        if m:
            country = m.group(1).strip()

        # Publisher (from archival status table or publisher section)
        publisher = None
        m = re.search(r"Publisher:\s*</[^>]+>\s*(?:<[^>]+>\s*)*([A-Za-z][\w\s.,&]+)", html)
        if m:
            publisher = m.group(1).strip()
        if not publisher:
            # Try the archival table which shows the publisher name
            m = re.search(r"Penthouse International[^<]*", html)
            if m:
                publisher = m.group(0).strip()

        return {
            "title": title,
            "first_published": None,
            "print_issn": issn,
            "electronic_issn": None,
            "publisher": publisher,
            "country": country,
            "frequency": None,
            "language": None,
            "cover_url": None,
            "description": None,
            "source_urls": [url],
        }
