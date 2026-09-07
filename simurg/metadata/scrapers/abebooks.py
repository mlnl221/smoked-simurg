"""AbeBooks scraper.

AbeBooks is a marketplace, so the product pages are server-rendered (no API),
but they reliably carry the canonical edition metadata we want: title, author,
publisher, publication year, page count, language, synopsis and a cover image.

Two entry points:

* ``search_isbn`` — fetch ``https://www.abebooks.com/products/isbn/<isbn>``.
* ``search_title_author`` — run an AbeBooks search and follow the first product
  link (the ``/products/isbn/...``, ``/servlet/BookDetailsPL`` and
  ``/<slug>/<id>/bd`` styles all resolve to a product page we can parse).

AbeBooks is bot-sensitive: it serves a block/CAPTCHA page for some user agents.
We send a desktop browser UA + Accept headers and degrade to ``None`` on any
non-200 / missing-title / block response, degrading gracefully to ``None``.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from ratelimit import RateLimitException, limits, sleep_and_retry

from simurg.constants import SCRAPER_TIMEOUT
from simurg.metadata.scrapers.base import BaseScraper

# Browser-like headers to avoid the AbeBooks bot wall.
_ABE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Politeness: cap AbeBooks traffic so we don't trip their bot wall.
_CALLS = 3
_PERIOD = 10


class AbeBooksScraper(BaseScraper):
    name = "abebooks"
    url_domains = {"abebooks.com"}

    def __init__(self, session=None):
        super().__init__(session)
        self.session.headers.update(_ABE_HEADERS)

    # -- HTTP helper (rate-limited + block-aware retries) ------------------

    @sleep_and_retry
    @limits(_CALLS, _PERIOD)
    def _get(self, url: str, params=None) -> object | None:
        """Rate-limited GET with retry on 429/503 bot walls.

        Returns the ``requests.Response`` or ``None`` if every attempt is
        blocked/errored. Rate-limit exceptions are absorbed by ``sleep_and_retry``.
        """
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                r = self.session.get(
                    url, params=params, timeout=SCRAPER_TIMEOUT, allow_redirects=True
                )
            except RateLimitException:
                raise  # let sleep_and_retry handle the wait
            except Exception as e:  # network blip
                last_exc = e
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (429, 503):
                retry_after = float(r.headers.get("Retry-After", "5"))
                time.sleep(retry_after if 0 < retry_after <= 30 else 5)
                continue
            # Any other non-200: surface it so the caller can decide (-> None).
            return r
        if last_exc:
            return None
        return None

    # -- public API --------------------------------------------------------

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted AbeBooks product-page URL directly.

        Accepts the ``/products/isbn/...``, ``/servlet/BookDetailsPL`` and
        ``/<slug>/<id>/bd`` product-page shapes — all parse via the existing
        product-page parser (preferring the cleaner ISBN page when present).
        """
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
        except Exception:
            return None
        if "abebooks.com" not in (parsed.netloc or "").lower():
            return None
        # If the pasted URL is a search page, follow its first product link.
        if "/servlet/SearchResults" in (parsed.path or ""):
            try:
                r = self._get(url)
            except Exception:
                return None
            if r is None or r.status_code != 200:
                return None
            product_url = self._first_product_link(r.text)
            if not product_url:
                return None
            url = product_url
        try:
            r = self._get(url)
        except Exception:
            return None
        if r is None or r.status_code != 200:
            return None
        return self._parse_product_page(r.text, r.url)

    def search_isbn(self, isbn: str) -> dict | None:
        cleaned = re.sub(r"[^0-9Xx]", "", isbn)
        if not cleaned:
            return None
        url = f"https://www.abebooks.com/products/isbn/{cleaned}"
        try:
            r = self._get(url)
        except Exception:
            return None
        if r is None or r.status_code != 200:
            return None
        return self._parse_product_page(r.text, r.url, preferred_isbn=cleaned)

    def search_title_author(self, title: str, authors: list[str]) -> dict | None:
        q = title or ""
        if authors:
            q += " " + " ".join(str(a) for a in authors[:2])
        q = q.strip()
        if not q:
            return None
        try:
            r = self._get("https://www.abebooks.com/servlet/SearchResults", params={"kn": q})
        except Exception:
            return None
        if r is None or r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        # The search-results page shows the full title; individual listing pages
        # often truncate it, so prefer the longest listing-title we can find.
        # (AbeBooks sometimes shows a shortened title on the first result only.)
        search_title = None
        for lt in soup.find_all(attrs={"data-test-id": "listing-title"}):
            t = lt.get_text(" ", strip=True)
            if t and (search_title is None or len(t) > len(search_title)):
                search_title = t
        search_author = None
        for la in soup.find_all(attrs={"data-test-id": "listing-author"}):
            a = _normalize_author(la.get_text(" ", strip=True))
            if a:
                search_author = a
                break

        product_url = self._first_product_link(r.text)
        data = None
        if product_url:
            try:
                r2 = self._get(product_url)
            except Exception:
                r2 = None
            if r2 is not None and r2.status_code == 200:
                data = self._parse_product_page(r2.text, r2.url)

        if data is None:
            # Couldn't reach a product page; return what the results page gave us.
            if not search_title:
                return None
            data = {
                "title": search_title,
                "authors": [search_author] if search_author else list(authors),
                "publisher": None,
                "year": None,
                "publish_year": None,
                "first_publish_year": None,
                "page_count": None,
                "isbn": None,
                "language": None,
                "subjects": [],
                "description": None,
                "cover_url": None,
                "source_urls": [r.url],
            }
        # Prefer the fuller search-results title.
        if search_title and (
            not data["title"]
            or data["title"].lower() == "abebooks"
            or len(search_title) > len(data["title"])
        ):
            data["title"] = search_title
        if not data["authors"] and search_author:
            data["authors"] = [search_author]
        if not data["authors"] and authors:
            data["authors"] = list(authors)
        return data

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def _first_product_link(html: str) -> str | None:
        """Find the first AbeBooks product link from a search-results page."""
        soup = BeautifulSoup(html, "html.parser")
        patterns = (
            re.compile(r"/products/isbn/"),
            re.compile(r"/servlet/BookDetailsPL"),
            re.compile(r"/bd$"),
        )

        def looks_like_product(href: str) -> bool:
            return any(p.search(href) for p in patterns)

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if looks_like_product(href):
                return urljoin("https://www.abebooks.com", href)
        # Last resort: any /<slug>/<digits>/bd style link anywhere.
        for a in soup.find_all("a", href=True):
            if re.search(r"/\d{6,}/bd", a["href"]):
                return urljoin("https://www.abebooks.com", a["href"])
        return None

    # -- parsing -----------------------------------------------------------

    def _parse_product_page(
        self, html: str, url: str, preferred_isbn: str | None = None
    ) -> dict | None:
        soup = BeautifulSoup(html, "html.parser")

        # A block/CAPTCHA page has no book title element; bail early.
        data = {
            "title": self._parse_title(soup),
            "authors": self._parse_authors(soup),
            "publisher": None,
            "year": None,
            "publish_year": None,
            "first_publish_year": None,
            "page_count": None,
            "isbn": None,
            "language": None,
            "subjects": [],
            "description": self._parse_synopsis(soup),
            "cover_url": self._parse_cover(soup),
            "source_urls": [url],
        }
        if not data["title"]:
            return None

        pub_name, pub_year = self._parse_publisher_year(soup)
        data["publisher"] = pub_name
        data["year"] = pub_year
        data["publish_year"] = pub_year
        data["first_publish_year"] = None

        data["page_count"] = self._parse_pages(soup)
        data["language"] = self._parse_language(soup)
        data["isbn"] = self._parse_isbn(soup, preferred_isbn)

        return data

    @staticmethod
    def _parse_title(soup: BeautifulSoup) -> str | None:
        # Cleanest: the itemprop="name" meta tag on the product page.
        # (Some /bd listing pages set this to the site name "AbeBooks" — skip it.)
        meta = soup.find("meta", attrs={"itemprop": "name"})
        if meta and meta.get("content"):
            c = str(meta["content"]).strip()
            if c and c.lower() != "abebooks":
                return c
        # Server-rendered product title (may carry a " - <Binding>" suffix).
        for sel in ("h1.offer-title", "h1.title", "h1"):
            el = soup.select_one(sel)
            if el:
                text = el.get_text(" ", strip=True)
                if text:
                    return _strip_binding(text)
        # Fallback: derive from <title> "Title - Author: ISBN - AbeBooks".
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            t = title_tag.get_text(strip=True)
            t = re.sub(r"\s*-\s*AbeBooks\.?$", "", t)
            t = re.sub(r"\s*-\s*[^:]+:\s*[\dxX\-]{10,17}\s*$", "", t)
            if t:
                return _strip_binding(t)
        return None

    @staticmethod
    def _parse_authors(soup: BeautifulSoup) -> list[str]:
        authors: list[str] = []
        # Canonical: itemprop="author" meta tag(s).
        for meta in soup.find_all("meta", attrs={"itemprop": "author"}):
            c = meta.get("content")
            if c:
                authors.append(_normalize_author(str(c).strip()))
        if authors:
            return _dedupe(authors)
        # Fallback: contributor links (href starts with /author/, exclude "Explore").
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("/author/"):
                continue
            name = a.get_text(" ", strip=True)
            if not name or name.lower().startswith("explore"):
                continue
            authors.append(_normalize_author(name))
        if authors:
            return _dedupe(authors)
        # Last resort: "Author: Name" text.
        text = soup.get_text(" ", strip=True)
        m = re.search(r"Author:\s*([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3})", text)
        if m:
            return [_normalize_author(m.group(1))]
        return []

    @staticmethod
    def _parse_publisher_year(soup: BeautifulSoup):
        # Prefer the <dl class="listing-metadata"> labeled blocks.
        pub = _label_value(soup, "Publisher")
        year = _label_value(soup, "Publication date")
        if pub or year:
            y = None
            if year:
                ym = re.search(r"((?:19|20)\d{2})", year)
                if ym:
                    y = int(ym.group(1))
            return (pub.strip() if pub else None), y
        # Fallback: "Publisher: Name, Year" text.
        text = soup.get_text(" ", strip=True)
        m = re.search(r"Publisher:?\s*([^,]+),\s*((?:19|20)\d{2})", text)
        if m:
            return m.group(1).strip(), int(m.group(2))
        return None, None

    @staticmethod
    def _parse_pages(soup: BeautifulSoup) -> int | None:
        val = _label_value(soup, "Number of pages")
        if val:
            m = re.search(r"(\d+)", val)
            if m:
                return int(m.group(1))
        text = soup.get_text(" ", strip=True)
        m = re.search(r"Number of pages\s*(\d+)", text)
        if m:
            return int(m.group(1))
        m = re.search(r"(\d+)\s*pages?", text)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _parse_language(soup: BeautifulSoup) -> str | None:
        val = _label_value(soup, "Language")
        if val:
            return val.strip()
        text = soup.get_text(" ", strip=True)
        m = re.search(r"Language:?\s*([A-Z][a-z]{1,20})", text)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _parse_synopsis(soup: BeautifulSoup) -> str | None:
        # The "Synopsis" block.
        for header in soup.find_all(string=re.compile(r"^\s*Synopsis\s*$", re.I)):
            container = header.find_parent()
            node = container or header
            # Grab the sibling/next text block.
            para = node.find_next("p") if node else None
            if para:
                txt = para.get_text(" ", strip=True)
            else:
                txt = node.get_text(" ", strip=True) if node else ""
            txt = re.sub(r"\s*\"?synopsis\"? may belong to another edition.*$", "", txt, flags=re.I)
            if txt:
                return txt.strip()
        # Markdown-friendly: first long <p> under a synopsis section.
        syn = soup.find(id=re.compile(r"synopsis", re.I)) or soup.find(
            class_=re.compile(r"synopsis", re.I)
        )
        if syn:
            txt = syn.get_text(" ", strip=True)
            txt = re.sub(r"\s*\"?synopsis\"? may belong to another edition.*$", "", txt, flags=re.I)
            if txt:
                return txt.strip()
        return None

    @staticmethod
    def _parse_cover(soup: BeautifulSoup) -> str | None:
        # 1) Real product cover image (pictures.abebooks.com / images.abebooks.com).
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
            if "pictures.abebooks.com" in src or "images.abebooks.com" in src:
                return _abs(src)
        # 2) Preload <link> for the cover image.
        for link in soup.find_all("link", attrs={"rel": "preload"}):
            h = link.get("href") or ""
            if "pictures.abebooks.com" in h or "images.abebooks.com" in h:
                return _abs(h)
        # 3) og:image, but skip the site logo fallback.
        og = soup.find("meta", attrs={"property": "og:image"})
        if og and og.get("content"):
            c = str(og["content"]).strip()
            if "logo" not in c.lower() and "abebookscdn" not in c.lower():
                return _abs(c)
        return None

    @staticmethod
    def _parse_isbn(soup: BeautifulSoup, preferred_isbn: str | None) -> str | None:
        text = soup.get_text(" ", strip=True)
        isbn13 = re.findall(r"\b97[89][\d-]{10,17}\b", text)
        isbn10 = re.findall(r"\b(?:[\dXx][\d-]{8,12}[\dXx])\b", text)
        found = [re.sub(r"[^0-9Xx]", "", x) for x in isbn13 + isbn10]
        found = [x for x in found if len(x) in (10, 13)]
        if not found:
            return None
        if preferred_isbn and preferred_isbn in found:
            return preferred_isbn
        return found[0]


def _normalize_author(name: str) -> str:
    """Flip inverted 'Last, First' into 'First Last' for fuzzy matching."""
    name = name.strip()
    if "," in name and not name.lower().startswith("et al"):
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2 and parts[1]:
            return f"{parts[1]} {parts[0]}"
    return name


_BINDINGS = (
    "Hardcover",
    "Softcover",
    "Paperback",
    "Mass Market Paperback",
    "Library Binding",
    "Kindle Edition",
    "Leatherbound",
    "Board book",
    "Spiral-bound",
    "Unearthed",
    "Audio CD",
    "Audiobook",
)


def _strip_binding(title: str) -> str:
    """Drop an AbeBooks ' - <Binding>' suffix appended to the product title."""
    for b in _BINDINGS:
        if title.endswith(f" - {b}"):
            return title[: -(len(b) + 3)].strip()
    return title


def _abs(url: str) -> str:
    """Make a possibly protocol-relative URL absolute."""
    if url.startswith("//"):
        return "https:" + url
    return url


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for i in items:
        if i not in seen:
            seen.append(i)
    return seen


def _label_value(soup: BeautifulSoup, label: str) -> str | None:
    """Find a value next to a label like 'Publisher' / 'Language'."""
    for el in soup.find_all(string=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)):
        parent = el.find_parent()
        if not parent:
            continue
        nxt = parent.find_next_sibling()
        if nxt:
            val = nxt.get_text(" ", strip=True)
            if val:
                return val
        val = parent.get_text(" ", strip=True)
        val = re.sub(rf"^{re.escape(label)}\s*", "", val, flags=re.I)
        if val and val != label:
            return val
    return None
