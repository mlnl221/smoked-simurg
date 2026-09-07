"""LibraryThing scraper — magazine issues and ebook metadata via Talpa + Common Knowledge APIs.

Two APIs, same token/API key:

* **Talpa search** (``token`` param): ``/api/talpa.php?token=...&search=...&limit=N``
  Returns JSON with ``response.resultlist[]`` containing ``rank, title, work_id,
  score, isbns[]``.
* **Common Knowledge REST** (``apikey`` param):
  ``/services/rest/1.1/?method=librarything.ck.getwork&id=work_id&apikey=...``
  Returns XML with ``<item>`` containing ``<title>``, ``<author>``,
  ``<commonknowledge><fieldList>`` with fields like ``originalpublicationdate``,
  ``canonicaltitle``, ``series``.

Both use the same API key (set `[metadata] librarything_token` in
``config.toml``), but Talpa uses ``token`` and the REST API uses
``apikey`` — do not mix parameter names.

Entry points:

* ``search_isbn`` — search Talpa by ISBN, check ``isbns`` field in results.
* ``search_title_author`` — search Talpa by title + author.
* ``search_magazine`` — search Talpa by title + issue label.
* ``search_url`` — resolve a pasted ``librarything.com/work/<id>`` URL directly.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from ratelimit import limits, sleep_and_retry

from simurg.constants import SCRAPER_TIMEOUT
from simurg.metadata.scrapers.base import BaseScraper
from simurg.metadata.scrapers.util import (
    clean_issn,
    normalize_issue_date,
    year_from,
)

_TALPA_URL = "https://www.librarything.com/api/talpa.php"
_CK_URL = "https://www.librarything.com/services/rest/1.1/"
_CALLS = 3
_PERIOD = 10


class LibraryThingScraper(BaseScraper):
    name = "librarything"
    categories = {"ebook", "magazine"}
    url_domains = {"librarything.com"}

    def __init__(self, session=None):
        super().__init__(session)
        self._token = None
        try:
            from simurg.config import get_config

            cfg = get_config()
            self._token = cfg.metadata.get("librarything_token") or ""
        except Exception:
            pass

    @sleep_and_retry
    @limits(_CALLS, _PERIOD)
    def _talpa_search(self, query: str, limit: int = 5) -> list[dict]:
        """Search LibraryThing Talpa API. Returns list of result dicts."""
        if not self._token or not query.strip():
            return []
        try:
            r = self.session.get(
                _TALPA_URL,
                params={"token": self._token, "search": query.strip(), "limit": limit},
                timeout=SCRAPER_TIMEOUT,
            )
            if r.status_code != 200:
                return []
            data = r.json()
            return data.get("response", {}).get("resultlist", [])
        except Exception:
            return []

    def _fetch_ck(self, work_id: int | str) -> dict:
        """Fetch Common Knowledge XML for a work_id. Returns parsed fields."""
        if not self._token:
            return {}
        try:
            r = self.session.get(
                _CK_URL,
                params={
                    "method": "librarything.ck.getwork",
                    "id": str(work_id),
                    "apikey": self._token,
                },
                timeout=SCRAPER_TIMEOUT,
            )
            if r.status_code != 200:
                return {}
            return _parse_ck_xml(r.text)
        except Exception:
            return {}

    def search_isbn(self, isbn: str) -> dict | None:
        cleaned = re.sub(r"[^0-9Xx]", "", isbn)
        if not cleaned or len(cleaned) not in (10, 13):
            return None
        results = self._talpa_search(cleaned, limit=5)
        if not results:
            return None
        # Prefer a result whose isbns list contains our ISBN
        best = None
        for hit in results:
            hit_isbns = hit.get("isbns") or []
            if cleaned in hit_isbns:
                best = hit
                break
        if not best:
            # Fall back to first result (Talpa ranks by relevance)
            best = results[0]
        work_id = best.get("work_id")
        if not work_id:
            return None
        ck = self._fetch_ck(work_id)
        return _build_ebook_result(best, ck, source="librarything")

    def search_title_author(self, title: str, authors: list[str]) -> dict | None:
        q = title or ""
        if authors:
            q += " " + " ".join(str(a) for a in authors[:2])
        q = q.strip()
        if not q:
            return None
        results = self._talpa_search(q, limit=5)
        if not results:
            return None
        best = results[0]
        work_id = best.get("work_id")
        if not work_id:
            return None
        ck = self._fetch_ck(work_id)
        return _build_ebook_result(best, ck, source="librarything")

    def search_magazine(self, title: str, issue: dict | None = None) -> dict | None:
        q = title or ""
        if issue:
            from simurg.metadata.scrapers.util import issue_label as _il

            label = _il(
                issue.get("issue_date"),
                issue.get("issue_date_precision"),
                issue.get("volume"),
                issue.get("issue_number"),
            )
            if label:
                q = f"{title} {label}"
        if not q.strip():
            return None
        results = self._talpa_search(q.strip(), limit=5)
        if not results:
            return None
        best = results[0]
        work_id = best.get("work_id")
        if not work_id:
            return None
        ck = self._fetch_ck(work_id)
        return _build_magazine_result(best, ck, title, source="librarything")

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted librarything.com work URL to metadata.

        Handles ``librarything.com/work/<id>`` and
        ``librarything.com/work/<id>/...`` paths.
        """
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
        except Exception:
            return None
        if "librarything.com" not in (parsed.netloc or "").lower():
            return None
        m = re.search(r"/work/(\d+)", parsed.path or "")
        if not m:
            return None
        work_id = m.group(1)
        ck = self._fetch_ck(work_id)
        if not ck.get("title"):
            return None
        # Determine if this looks like a magazine issue (title contains "|" or month name)
        title_raw = ck.get("title", "")
        is_mag = bool(
            re.search(
                r"\|\s*(January|February|March|April|May|June|July|August|September|October|November|December)",
                title_raw,
                re.I,
            )
        )
        if is_mag:
            return _build_magazine_result(
                {"work_id": work_id, "title": title_raw}, ck, title_raw, source="librarything"
            )
        return _build_ebook_result(
            {"work_id": work_id, "title": title_raw}, ck, source="librarything"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_ck_xml(xml_text: str) -> dict:
    """Parse LibraryThing Common Knowledge XML into a flat dict."""
    result = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return result
    # Namespace: http://www.librarything.com/
    ns = {"lt": "http://www.librarything.com/"}
    item = root.find(".//lt:item", ns)
    if item is None:
        item = root.find(".//item")
    if item is None:
        return result
    # Title — never use `or` on Element (bool(childless element) is False)
    title_el = item.find("lt:title", ns)
    if title_el is None:
        title_el = item.find("title")
    if title_el is not None and title_el.text:
        result["title"] = title_el.text.strip()
    # Author
    author_el = item.find("lt:author", ns)
    if author_el is None:
        author_el = item.find("author")
    if author_el is not None and author_el.text:
        result["author"] = author_el.text.strip()
    # CK fields
    for field_el in item.findall(".//lt:field", ns) or item.findall(".//field"):
        name = field_el.get("name", "")
        fact = field_el.find(".//lt:fact", ns)
        if fact is None:
            fact = field_el.find(".//fact")
        if fact is not None and fact.text:
            result[name] = fact.text.strip()
    return result


def _build_ebook_result(talpa_hit: dict, ck: dict, source: str = "librarything") -> dict:
    """Build an enricher-compatible ebook result dict from Talpa + CK data."""
    title = ck.get("title") or talpa_hit.get("title") or ""
    # Author from CK (single string) → list
    author_str = ck.get("author") or ""
    authors = [author_str] if author_str else []
    # ISBNs from Talpa hit
    isbns = talpa_hit.get("isbns") or []
    isbn = None
    for candidate in isbns:
        cleaned = re.sub(r"[^0-9Xx]", "", str(candidate))
        if len(cleaned) in (10, 13):
            isbn = cleaned
            break
    # Publication date from CK
    pub_date = ck.get("originalpublicationdate") or ""
    pub_year = year_from(pub_date)
    # URL
    work_id = talpa_hit.get("work_id")
    url = f"https://www.librarything.com/work/{work_id}" if work_id else ""
    return {
        "title": title,
        "authors": authors,
        "year": pub_year,
        "publish_year": pub_year,
        "first_publish_year": pub_year,
        "publisher": None,
        "isbn": isbn,
        "page_count": None,
        "language": None,
        "subjects": [],
        "description": None,
        "cover_url": None,
        "source_urls": [url] if url else [],
    }


def _build_magazine_result(
    talpa_hit: dict, ck: dict, search_title: str, source: str = "librarything"
) -> dict:
    """Build a magazine scraper result dict from Talpa + CK data."""
    title_raw = ck.get("title") or talpa_hit.get("title") or search_title
    # Extract canonical magazine title: everything before "|"
    canonical = title_raw
    issue_date_str = None
    precision = None
    m = re.search(r"(.+?)\|\s*(.+)", title_raw)
    if m:
        canonical = m.group(1).strip()
        issue_part = m.group(2).strip()
        issue_date_str, precision = normalize_issue_date(issue_part)
    if not issue_date_str:
        # Try parsing the whole title as a date
        issue_date_str, precision = normalize_issue_date(title_raw)
    # ISSN from CK (if present)
    issn = clean_issn(ck.get("issn"))
    # Publication date from CK
    pub_date = ck.get("originalpublicationdate") or ""
    first_published = year_from(pub_date)
    # URL
    work_id = talpa_hit.get("work_id")
    url = f"https://www.librarything.com/work/{work_id}" if work_id else ""
    return {
        "title": canonical,
        "first_published": first_published,
        "print_issn": issn,
        "electronic_issn": None,
        "publisher": None,
        "country": None,
        "frequency": None,
        "issue_date": issue_date_str,
        "issue_date_precision": precision,
        "volume": None,
        "issue_number": None,
        "page_count": None,
        "language": None,
        "cover_url": None,
        "description": None,
        "source_urls": [url] if url else [],
    }
