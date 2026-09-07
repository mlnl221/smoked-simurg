"""Penguin Random House scraper.

PRH is the publisher's own site, so product pages carry canonical edition
metadata: title, author, publisher/imprint, publication date, page count,
ISBN, language, description and a cover image.

Three entry points:

* ``search_isbn`` — hit the structured ``/ajaxc/isbn/?isbn=`` JSON endpoint
  (title/author/imprint/pages/date), then fetch the linked product page for
  the description + cover. Falls back to the predictive-search typeahead.
* ``search_title_author`` — query the predictive-search typeahead endpoint
  and follow the first ``/books/<id>/...`` result to its product page.
* ``search_url`` — fetch a pasted PRH book page directly.

Product pages are parsed from JSON-LD structured data (preferred) with an
HTML fallback (``<h1>``, contributor links, the Product Details drawer).
"""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from ratelimit import RateLimitException, limits, sleep_and_retry

from simurg.constants import SCRAPER_TIMEOUT
from simurg.metadata.scrapers.base import BaseScraper

_PRH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_TYPEAHEAD_URL = (
    "https://www.penguinrandomhouse.com/wp-content/themes/penguinrandomhouse/predictive-search.php"
)
_ISBN_API_URL = "https://www.penguinrandomhouse.com/ajaxc/isbn/"

_CALLS = 5
_PERIOD = 10


class PenguinRandomHouseScraper(BaseScraper):
    name = "penguinrandomhouse"
    categories = {"ebook"}
    url_domains = {"penguinrandomhouse.com"}

    def __init__(self, session: requests.Session | None = None):
        super().__init__(session)
        self.session.headers.update(_PRH_HEADERS)

    # -- HTTP helper -------------------------------------------------------

    @sleep_and_retry
    @limits(_CALLS, _PERIOD)
    def _get(self, url: str, params=None) -> requests.Response | None:
        """Rate-limited GET; retries politely on 429/503."""
        for attempt in range(3):
            try:
                r = self.session.get(
                    url, params=params, timeout=SCRAPER_TIMEOUT, allow_redirects=True
                )
            except RateLimitException:
                raise
            except Exception:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (429, 503):
                retry_after = float(r.headers.get("Retry-After", "5"))
                time.sleep(retry_after if 0 < retry_after <= 30 else 5)
                continue
            return r
        return None

    # -- public API --------------------------------------------------------

    def search_isbn(self, isbn: str) -> dict | None:
        cleaned = re.sub(r"[^0-9Xx]", "", isbn)
        if len(cleaned) not in (10, 13):
            return None

        # Structured edition JSON (no HTML parsing) -> product page for desc/cover.
        data = None
        r = self._get(_ISBN_API_URL, params={"isbn": cleaned})
        if r is not None and r.status_code == 200:
            try:
                api = r.json()
            except ValueError:
                api = None
            if api and api.get("title"):
                slug = api.get("seoFriendlyUrl") or ""
                page_url = urljoin("https://www.penguinrandomhouse.com/", slug)
                page_html = self._fetch_text(page_url)
                data = self._parse_product_page(page_html, page_url)
                if data is None:
                    data = _data_from_api(api, page_url or None)
                else:
                    _fill_from_api(data, api)
                return data

        # Fallback: typeahead treats an ISBN as a plain query.
        results = self._typeahead_book_urls(cleaned)
        if not results:
            return None
        html = self._fetch_text(results[0])
        if not html:
            return None
        return self._parse_product_page(html, results[0])

    def search_title_author(self, title: str, authors: list[str]) -> dict | None:
        q = title or ""
        if authors:
            q += " " + " ".join(str(a) for a in authors[:2])
        q = q.strip()
        if not q:
            return None
        urls = self._typeahead_book_urls(q)
        # Parse up to 3 candidates and keep the closest title match — the
        # typeahead often leads with foreign-market editions of the same work.
        needle = (title or q).lower().strip()
        best: tuple[float, dict] | None = None
        for url in urls[:3]:
            html = self._fetch_text(url)
            if not html:
                continue
            data = self._parse_product_page(html, url)
            if not data or not data.get("title"):
                continue
            score = SequenceMatcher(None, needle, str(data["title"]).lower()).ratio()
            if best is None or score > best[0]:
                best = (score, data)
        if best is None or best[0] < 0.5:
            return None
        return best[1]

    def search_url(self, url: str) -> dict | None:
        if not self.match_url(url):
            return None
        html = self._fetch_text(url)
        if not html:
            return None
        return self._parse_product_page(html, url)

    # -- discovery ---------------------------------------------------------

    def _typeahead_book_urls(self, query: str) -> list[str]:
        """Return candidate /books/<id>/... URLs from the typeahead endpoint."""
        r = self._get(_TYPEAHEAD_URL, params={"q": query, "search": "All"})
        if r is None or r.status_code != 200:
            return []
        try:
            payload = json.loads(r.text)
        except ValueError:
            return []
        urls: list[str] = []
        for group in payload.get("data") or []:
            if group.get("result-type") not in ("books", "title", None):
                continue
            for res in group.get("results") or []:
                href = res.get("href") or ""
                if "/books/" in href:
                    urls.append(urljoin("https://www.penguinrandomhouse.com/", href))
        return urls

    def _fetch_text(self, url: str) -> str | None:
        if not url:
            return None
        r = self._get(url)
        if r is None or r.status_code != 200:
            return None
        return r.text

    # -- parsing -----------------------------------------------------------

    def _parse_product_page(self, html: str | None, url: str) -> dict | None:
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")

        data = _data_from_jsonld(soup, url)
        if data is None or not data.get("title"):
            fallback = {
                "title": _html_title(soup),
                "authors": _html_authors(soup),
                "publisher": None,
                "year": None,
                "publish_year": None,
                "first_publish_year": None,
                "page_count": None,
                "isbn": None,
                "language": None,
                "subjects": [],
                "description": _html_description(soup),
                "cover_url": _html_cover(soup),
                "source_urls": [url],
            }
            details = _product_details(soup)
            fallback["isbn"] = details.get("ISBN")
            fallback["publisher"] = details.get("Published by")
            pub_date = details.get("Published on")
            year = _extract_year(pub_date)
            fallback["year"] = year
            fallback["publish_year"] = year
            fallback["first_publish_year"] = None
            pages = details.get("Pages")
            if pages and pages.isdigit():
                fallback["page_count"] = int(pages)
            data = fallback if not data else _merge(data, fallback)

        if not data or not data.get("title"):
            return None

        # The drawer carries the imprint ("Penguin Books") while JSON-LD only
        # has the umbrella org ("Penguin Random House"); prefer the drawer.
        details = _product_details(soup)
        if details.get("Published by"):
            data["publisher"] = details["Published by"]

        # JSON-LD description is the truncated meta description; the drawer
        # copy holds the full text.
        full_desc = _html_description(soup)
        if full_desc:
            data["description"] = full_desc

        if not data.get("cover_url") and data.get("isbn"):
            cleaned = re.sub(r"[^0-9Xx]", "", str(data["isbn"]))
            if len(cleaned) == 13:
                data["cover_url"] = f"https://images.penguinrandomhouse.com/cover/{cleaned}"
        return data


# -- JSON-LD parsing -------------------------------------------------------


def _data_from_jsonld(soup: BeautifulSoup, url: str) -> dict | None:
    """Build the metadata dict from the page's schema.org Book JSON-LD."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        blocks = payload if isinstance(payload, list) else [payload]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            entity = block.get("mainEntity")
            if isinstance(entity, dict) and entity.get("@type") == "Book":
                return _data_from_book_entity(entity, block, url)
            if isinstance(entity, list):
                for cand in entity:
                    if isinstance(cand, dict) and cand.get("@type") == "Book":
                        return _data_from_book_entity(cand, block, url)
    return None


def _data_from_book_entity(entity: dict, block: dict, url: str) -> dict:
    examples = entity.get("workExample") or []
    example = examples[0] if isinstance(examples, list) and examples else {}
    if not isinstance(example, dict):
        example = {}

    authors = _authors_from_jsonld(entity)
    publisher = None
    pub_org = example.get("publisher")
    if isinstance(pub_org, dict):
        publisher = pub_org.get("name")
    elif isinstance(pub_org, str):
        publisher = pub_org
    date_published = example.get("datePublished") or ""
    year = _extract_year(str(date_published))

    isbn = example.get("isbn") or entity.get("isbn") or ""
    isbn = re.sub(r"[^0-9Xx]", "", str(isbn))

    cover = entity.get("image") or ""
    if isinstance(cover, list):
        cover = cover[0] if cover else ""

    description = block.get("description") or entity.get("description")

    return {
        "title": entity.get("name"),
        "authors": authors,
        "publisher": publisher,
        "year": year,
        "publish_year": year,
        "first_publish_year": None,
        "page_count": entity.get("numberOfPages"),
        "isbn": isbn or None,
        "language": entity.get("inLanguage"),
        "subjects": [],
        "description": description,
        "cover_url": cover or None,
        "source_urls": [url],
    }


def _authors_from_jsonld(entity: dict) -> list[str]:
    raw = entity.get("author")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, dict):
        raw = [raw]
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
    return names


# -- HTML fallback parsing --------------------------------------------------


def _html_title(soup: BeautifulSoup) -> str | None:
    el = soup.select_one(".product-title h1") or soup.select_one("header h1")
    if el:
        text = el.get_text(" ", strip=True)
        if text:
            return text
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        content = str(og["content"]).strip()
        # og:title looks like "Title by Author: ISBN | PenguinRandomHouse.com: Books"
        content = re.sub(r"\s*\|.*$", "", content)
        content = re.sub(r":\s*97[89][\d]{9}\s*$", "", content)
        content = (
            re.sub(r"\s+by\s+.+$", "", content, flags=re.I)
            if " by " in content.lower()
            else content
        )
        return content.strip() or None
    return None


def _html_authors(soup: BeautifulSoup) -> list[str]:
    authors: list[str] = []
    contrib = soup.select_one(".contrib-wrap")
    if contrib:
        for span in contrib.select("span.contributor"):
            text = span.get_text(" ", strip=True)
            text = re.sub(r"^\s*By\s+", "", text, flags=re.I)
            text = re.sub(r"^\s*and\s+", "", text, flags=re.I)
            text = text.strip(" ,/")
            if text:
                authors.append(text)
    if authors:
        return authors
    tealium = _utag_data(soup)
    author = tealium.get("book_author") or tealium.get("product_author")
    if isinstance(author, list):
        return [str(a).strip() for a in author if str(a).strip()]
    if isinstance(author, str) and author.strip():
        return [author.strip()]
    return []


def _html_description(soup: BeautifulSoup) -> str | None:
    el = soup.select_one("#book-description-copy")
    if el:
        text = el.get_text("\n", strip=True)
        text = re.sub(r"\s*…?\s*(Read More|See Less)\s*$", "", text)
        if text:
            return text
    meta = soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        return str(meta["content"]).strip()
    return None


def _html_cover(soup: BeautifulSoup) -> str | None:
    img = soup.select_one("img#coverFormat")
    if img and img.get("src"):
        return img["src"]
    og = soup.find("meta", attrs={"property": "og:image"})
    if og and og.get("content"):
        content = str(og["content"]).strip()
        if "/smedia/" in content or "/cover/" in content:
            return content
    return None


def _product_details(soup: BeautifulSoup) -> dict[str, str]:
    """Parse the Product Details drawer: <p><span>Label</span>Value</p>."""
    details: dict[str, str] = {}
    panel = soup.select_one("#drawer-product-details") or soup.select_one("#productdetails")
    if not panel:
        return details
    for p in panel.find_all("p"):
        label_el = p.find("span", class_="drawer-medium")
        if not label_el:
            continue
        label = label_el.get_text(" ", strip=True)
        value = p.get_text(" ", strip=True)
        value = value[len(label) :].strip() if value.startswith(label) else value
        if label and value:
            details[label] = value
    return details


def _utag_data(soup: BeautifulSoup) -> dict:
    """Best-effort extraction of the Tealium ``var utag_data={...}`` object."""
    for script in soup.find_all("script"):
        text = script.string or ""
        m = re.search(r"var\s+utag_data\s*=\s*(\{.*?\});", text, re.S)
        if not m:
            continue
        try:
            payload = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


# -- /ajaxc/isbn helpers ----------------------------------------------------


def _data_from_api(api: dict, page_url: str | None) -> dict:
    """Metadata dict straight from the /ajaxc/isbn JSON (no product page)."""
    year = _extract_year(str((api.get("onSaleDate") or {}).get("date", "")))
    contributors = api.get("contributors") or {}
    authors = [
        str(c.get("display")).strip()
        for c in contributors.values()
        if isinstance(c, dict) and c.get("display")
    ]
    if not authors and api.get("author"):
        authors = [str(api["author"]).strip()]
    imprint = api.get("imprint") or {}
    isbn = str(api.get("isbn") or "")
    return {
        "title": api.get("title"),
        "authors": authors,
        "publisher": imprint.get("name"),
        "year": year,
        "publish_year": year,
        "first_publish_year": None,
        "page_count": api.get("totalPages"),
        "isbn": isbn or None,
        "language": None,
        "subjects": [],
        "description": None,
        "cover_url": f"https://images.penguinrandomhouse.com/cover/{isbn}" if isbn else None,
        "source_urls": [page_url] if page_url else [],
    }


def _fill_from_api(data: dict, api: dict) -> None:
    """Backfill fields the product page lacked using /ajaxc/isbn JSON."""
    if not data.get("publisher"):
        imprint = api.get("imprint") or {}
        if imprint.get("name"):
            data["publisher"] = imprint["name"]
    if not data.get("year") and api.get("onSaleDate"):
        year = _extract_year(str(api["onSaleDate"].get("date", "")))
        if year:
            data["year"] = year
            data["publish_year"] = year
    if not data.get("page_count") and api.get("totalPages"):
        data["page_count"] = api["totalPages"]
    if not data.get("authors") and api.get("author"):
        data["authors"] = [str(api["author"]).strip()]
    if not data.get("isbn") and api.get("isbn"):
        data["isbn"] = str(api["isbn"])


def _merge(primary: dict, fallback: dict) -> dict:
    merged = dict(fallback)
    for key, val in primary.items():
        if val not in (None, "", [], {}):
            merged[key] = val
    return merged


def _extract_year(value: str) -> int | None:
    m = re.search(r"((?:19|20)\d{2})", value or "")
    return int(m.group(1)) if m else None
