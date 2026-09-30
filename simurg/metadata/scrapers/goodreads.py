"""Goodreads scraper.

Goodreads book pages are server-rendered Next.js + Apollo and carry the
canonical edition metadata we want in embedded JSON: title, author, publisher,
publication year, page count, language, ISBN, genres and description plus a
cover image.

Two entry points:

* ``search_isbn`` — resolve via the ungated
  ``/book/auto_complete?format=json&q=<isbn>`` endpoint to a ``bookId``,
  then fetch ``/book/show/<bookId>``.
* ``search_title_author`` — same autocomplete with a title+author query,
  fetch up to 3 book pages and return up to 3 distinct-year editions
  (Google Books shape) so the picker can show them.

Goodreads fronts ``/book/show`` with AWS WAF Bot Control. A challenged
response is an HTML interstitial (``gokuProps`` / ``challenge-container`` /
``AwsWafIntegration``) instead of book HTML. We degrade to ``None`` on any
challenge / non-200 / missing-title response. An optional user-supplied
``aws-waf-token`` cookie (``[metadata] goodreads_waf_token`` in
``config.toml``, override ``GOODREADS_WAF_TOKEN`` env) is attached when set
and raises the detail-page hit rate; ``auto_complete`` needs no cookie.
``/search`` is robots-disallowed — never crawl it, use ``auto_complete``.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from difflib import SequenceMatcher

from bs4 import BeautifulSoup
from ratelimit import RateLimitException, limits, sleep_and_retry

from simurg.constants import SCRAPER_TIMEOUT
from simurg.metadata.scrapers.base import BaseScraper
from simurg.metadata.scrapers.util import clean_description, year_from

_AUTOCOMPLETE = "https://www.goodreads.com/book/auto_complete"
_BOOK_SHOW = "https://www.goodreads.com/book/show"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Politeness: Goodreads WAF is aggressive; keep traffic low.
_CALLS = 2
_PERIOD = 10

_CHALLENGE_MARKERS = ("gokuProps", "challenge-container", "AwsWafIntegration")


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _clean_isbn(value) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", str(value))
    return cleaned if len(cleaned) in (10, 13) else None


def _year_from_ms(value) -> int | None:
    """Convert a Goodreads ``publicationTime`` ms epoch to a year."""
    try:
        ts = int(value) / 1000.0
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC).year
    except (OverflowError, OSError, ValueError):
        return None


class GoodreadsScraper(BaseScraper):
    name = "goodreads"
    url_domains = {"goodreads.com"}

    def __init__(self, session=None):
        super().__init__(session)
        self.session.headers.update(_HEADERS)
        token = os.environ.get("GOODREADS_WAF_TOKEN", "") or ""
        if not token:
            try:
                from simurg.config import get_config

                token = str(get_config().metadata.get("goodreads_waf_token", "") or "")
            except Exception:
                token = ""
        token = token.strip().strip("\"'")
        # Accept a pasted full "aws-waf-token=<value>" cookie fragment too.
        m = re.search(r"aws-waf-token=([^;\s]+)", token)
        if m:
            token = m.group(1)
        self._waf_token = token
        if token:
            try:
                self.session.cookies.set("aws-waf-token", token, domain="www.goodreads.com")
            except Exception:
                pass

    # -- HTTP helper (rate-limited, challenge-aware) -------------------------

    @sleep_and_retry
    @limits(_CALLS, _PERIOD)
    def _get(self, url: str, params=None) -> object | None:
        try:
            r = self.session.get(url, params=params, timeout=SCRAPER_TIMEOUT)
        except RateLimitException:
            raise  # let sleep_and_retry handle the wait
        except Exception:
            return None
        if r.status_code != 200:
            if r.status_code in (429, 503):
                time.sleep(5)
            return None
        if _looks_like_challenge(r.text):
            return None
        return r

    # -- public API ----------------------------------------------------------

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted ``goodreads.com/book/show/<id>`` URL directly."""
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
        except Exception:
            return None
        if "goodreads.com" not in (parsed.netloc or "").lower():
            return None
        m = re.search(r"/book/show/(\d+)", parsed.path or "")
        if not m:
            return None
        return self._fetch_book(m.group(1))

    def search_isbn(self, isbn: str) -> dict | None:
        cleaned = _clean_isbn(isbn)
        if not cleaned:
            return None
        hits = self._autocomplete(cleaned)
        if not hits:
            return None
        book_id = str(hits[0].get("bookId") or "")
        if not book_id:
            return None
        return self._fetch_book(book_id, preferred_isbn=cleaned)

    def search_title_author(
        self, title: str, authors: list[str], year: int | None = None
    ) -> dict | list[dict] | None:
        q = title or ""
        if authors:
            q += " " + " ".join(str(a) for a in authors[:2])
        q = q.strip()
        if not q:
            return None
        hits = self._autocomplete(q)
        if not hits:
            return None
        editions = self._best_editions(hits, title, authors, year=year, limit=3)
        if not editions:
            return None
        if len(editions) == 1:
            return editions[0]
        return editions

    # -- discovery -----------------------------------------------------------

    def _best_editions(
        self,
        hits: list[dict],
        title: str,
        authors: list[str],
        year: int | None = None,
        limit: int = 3,
    ) -> list[dict]:
        """Fetch autocomplete hits into up to ``limit`` distinct-year editions.

        Junk hits (title similarity < 0.5, or author < 0.4 when authors are
        known) are dropped. Survivors rank by title similarity, then closeness
        to the inbuilt edition ``year``; only the first hit per distinct year
        is kept. Challenged/unparseable pages are skipped silently.
        """
        ranked: list[tuple[tuple, dict]] = []
        for hit in hits[:5]:
            if not isinstance(hit, dict) or not hit.get("bookId"):
                continue
            hit_title = str(hit.get("bookTitleBare") or hit.get("title") or "")
            t_score = _fuzzy(title, hit_title)
            if t_score < 0.5:
                continue
            if authors and isinstance(hit.get("author"), dict) and hit["author"].get("name"):
                a_score = max(
                    (_fuzzy(a1, str(hit["author"]["name"])) for a1 in authors),
                    default=0.0,
                )
                if a_score < 0.4:
                    continue
            ranked.append(((t_score, -_hit_year_distance(year, hit)), hit))
        ranked.sort(key=lambda kv: kv[0], reverse=True)
        out: list[dict] = []
        seen_years: set = set()
        for _, hit in ranked:
            parsed = self._fetch_book(str(hit["bookId"]))
            if not parsed or not parsed.get("title"):
                continue
            y = parsed.get("year")
            if y in seen_years:
                continue
            seen_years.add(y)
            out.append(parsed)
            if len(out) >= limit:
                break
        return out

    def _autocomplete(self, query: str) -> list[dict]:
        """Ungated JSON endpoint: title/ISBN/author -> ranked bookId hits."""
        try:
            r = self.session.get(
                _AUTOCOMPLETE, params={"format": "json", "q": query}, timeout=SCRAPER_TIMEOUT
            )
        except Exception:
            return []
        if r.status_code != 200:
            return []
        try:
            data = r.json()
        except Exception:
            return []
        if isinstance(data, dict):
            # Some responses wrap hits in a results/docs envelope.
            for key in ("results", "docs", "books"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            return []
        return [h for h in data if isinstance(h, dict) and h.get("bookId")]

    def _fetch_book(self, book_id: str, preferred_isbn: str | None = None) -> dict | None:
        try:
            r = self._get(f"{_BOOK_SHOW}/{book_id}")
        except Exception:
            return None
        if r is None:
            return None
        url = getattr(r, "url", "") or f"{_BOOK_SHOW}/{book_id}"
        return _parse_book_page(r.text, url, str(book_id), preferred_isbn)


def _hit_year_distance(query_year: int | None, hit: dict) -> float:
    """Year distance for autocomplete-hit tie-breaking; unknown sorts last."""
    if not query_year:
        return 0.0
    m = re.search(r"(\d{4})", str(hit.get("title") or ""))
    if not m:
        return 999.0
    try:
        return abs(query_year - int(m.group(1)))
    except ValueError:
        return 999.0


def _looks_like_challenge(html: str) -> bool:
    if not html:
        return True
    return any(m in html for m in _CHALLENGE_MARKERS)


def _parse_book_page(
    html: str, url: str, book_id: str, preferred_isbn: str | None = None
) -> dict | None:
    """Parse a ``/book/show`` page via NEXT_DATA -> ld+json -> HTML fallback."""
    if _looks_like_challenge(html):
        return None
    soup = BeautifulSoup(html, "html.parser")
    data = _parse_next_data(soup, book_id, url)
    if data is None:
        data = _blank_result(url)
    _fill_from_ld_json(soup, data)
    _fill_from_html(soup, data)
    if not data.get("title"):
        return None
    if preferred_isbn and not data.get("isbn"):
        data["isbn"] = preferred_isbn
    return data


def _blank_result(url: str) -> dict:
    return {
        "title": None,
        "authors": [],
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
        "source_urls": [url] if url else [],
    }


def _parse_next_data(soup: BeautifulSoup, book_id: str, url: str) -> dict | None:
    """Extract the edition record from the ``__NEXT_DATA__`` Apollo cache."""
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        payload = json.loads(tag.string)
    except Exception:
        return None
    try:
        state = payload["props"]["pageProps"]["apolloState"]
    except (KeyError, TypeError):
        return None
    if not isinstance(state, dict):
        return None
    book = None
    for node in state.values():
        if (
            isinstance(node, dict)
            and node.get("__typename") == "Book"
            and str(node.get("legacyId")) == str(book_id)
        ):
            book = node
            break
    if book is None:
        return None

    def resolve(value):
        if isinstance(value, dict) and set(value) == {"__ref"}:
            ref = state.get(value["__ref"])
            return ref if isinstance(ref, dict) else None
        return value if isinstance(value, dict) else None

    data = _blank_result(book.get("webUrl") or url)
    data["title"] = book.get("titleComplete") or book.get("title")

    # Author via primaryContributorEdge -> Contributor.
    edge = book.get("primaryContributorEdge") or {}
    if isinstance(edge, dict):
        contributor = resolve(edge.get("node"))
        if contributor and contributor.get("name"):
            data["authors"] = [str(contributor["name"]).strip()]

    # Edition details (inline dict).
    details = book.get("details") or {}
    if isinstance(details, dict):
        data["publisher"] = details.get("publisher") or None
        data["page_count"] = details.get("numPages") or None
        lang = details.get("language")
        if isinstance(lang, dict):
            data["language"] = lang.get("name") or None
        elif isinstance(lang, str):
            data["language"] = lang
        data["isbn"] = (
            _clean_isbn(details.get("isbn13"))
            or _clean_isbn(details.get("isbn"))
            or _clean_isbn(details.get("asin"))
        )
        year = _year_from_ms(details.get("publicationTime"))
        data["year"] = year
        data["publish_year"] = year

    # Genres.
    subjects: list[str] = []
    for bg in book.get("bookGenres") or []:
        if not isinstance(bg, dict):
            continue
        genre = resolve(bg.get("genre")) or bg.get("genre")
        name = genre.get("name") if isinstance(genre, dict) else None
        if name and name not in subjects:
            subjects.append(str(name))
    data["subjects"] = subjects[:10]

    # Description: prefer the stripped variant, fall back to raw HTML.
    desc_raw: str | None = None
    desc_stripped: str | None = None
    for k, v in book.items():
        if not isinstance(v, str) or not v.strip():
            continue
        if k == "description":
            desc_raw = desc_raw or v
        elif k.startswith("description(") and "stripped" in k:
            desc_stripped = desc_stripped or v
    data["description"] = clean_description(desc_stripped or desc_raw)

    # Cover.
    cover = book.get("imageUrl")
    if cover and "no-cover" not in str(cover):
        data["cover_url"] = str(cover)

    # Work-level original publication year.
    work = resolve(book.get("work"))
    if work and isinstance(work, dict):
        wdetails = work.get("details") or {}
        if isinstance(wdetails, dict):
            data["first_publish_year"] = _year_from_ms(wdetails.get("publicationTime"))
    return data


def _fill_from_ld_json(soup: BeautifulSoup, data: dict) -> None:
    """Fill gaps from ``<script type="application/ld+json">`` Book blocks."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(tag.string or "")
        except Exception:
            continue
        blocks = payload if isinstance(payload, list) else [payload]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            types = block.get("@type") or ""
            if isinstance(types, list):
                is_book = "Book" in types
            else:
                is_book = types == "Book"
            if not is_book:
                continue
            if not data.get("title") and block.get("name"):
                data["title"] = str(block["name"]).strip()
            if not data.get("authors"):
                authors = block.get("author") or []
                if isinstance(authors, dict):
                    authors = [authors]
                names = [a.get("name") for a in authors if isinstance(a, dict) and a.get("name")]
                if names:
                    data["authors"] = [str(n).strip() for n in names]
            if not data.get("page_count") and block.get("numberOfPages"):
                try:
                    data["page_count"] = int(block["numberOfPages"])
                except (TypeError, ValueError):
                    pass
            if not data.get("isbn"):
                data["isbn"] = _clean_isbn(block.get("isbn"))
            if not data.get("language") and block.get("inLanguage"):
                lang = block["inLanguage"]
                data["language"] = str(lang[0] if isinstance(lang, list) else lang)
            if not data.get("cover_url") and block.get("image"):
                img = block["image"]
                img = img[0] if isinstance(img, list) else img
                if img and "no-cover" not in str(img) and "logo" not in str(img).lower():
                    data["cover_url"] = str(img)
            if data.get("title"):
                return


def _fill_from_html(soup: BeautifulSoup, data: dict) -> None:
    """Fill gaps from server-rendered ``data-testid`` elements."""
    if not data.get("title"):
        el = soup.select_one('[data-testid="bookTitle"]')
        if el:
            data["title"] = el.get_text(" ", strip=True) or None
    if not data.get("authors"):
        names = []
        for a in soup.select('a.ContributorLink [data-testid="name"]'):
            t = a.get_text(" ", strip=True)
            if t and t not in names:
                names.append(t)
        if names:
            data["authors"] = names
    if not data.get("description"):
        el = soup.select_one('[data-testid="description"] span.Formatted')
        if el:
            data["description"] = clean_description(str(el))
        else:
            el = soup.select_one('[data-testid="description"]')
            if el:
                data["description"] = clean_description(el.get_text(" ", strip=True))
    if not data.get("cover_url"):
        img = soup.select_one(".BookCover__image img")
        src = (img.get("src") or "") if img else ""
        if src and "no-cover" not in src and "logo" not in src.lower():
            data["cover_url"] = src
        else:
            og = soup.find("meta", attrs={"property": "og:image"})
            if og and og.get("content"):
                c = str(og["content"]).strip()
                if "no-cover" not in c and "logo" not in c.lower():
                    data["cover_url"] = c
    if not data.get("page_count") or not data.get("year"):
        el = soup.select_one('[data-testid="pagesFormat"]')
        if el and not data.get("page_count"):
            m = re.search(r"(\d+)\s*pages?", el.get_text(" ", strip=True), re.I)
            if m:
                data["page_count"] = int(m.group(1))
        el = soup.select_one('[data-testid="publicationInfo"]')
        if el and not data.get("year"):
            y = year_from(el.get_text(" ", strip=True))
            data["year"] = y
            data["publish_year"] = y
    if not data.get("subjects"):
        tags = []
        for a in soup.select('[data-testid="genresList"] a[href^="/genres/"]'):
            t = a.get_text(" ", strip=True)
            if t and t not in tags:
                tags.append(t)
        if tags:
            data["subjects"] = tags[:10]
