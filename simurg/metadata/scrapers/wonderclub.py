"""WonderClub scraper — books + magazines via search + Details tab #menu1.

WonderClub (https://wonderclub.com) exposes books/magazines at
``/books/<slug>`` and ``/<isbn>``.  Metadata lives in a Bootstrap tab:

  <a data-toggle="tab" href="#menu1">Details</a>
  <div id="menu1"> ... </div>

Inside #menu1 every field is a label/value pair (table, dl, div row,
span, etc.).  The scraper dynamically extracts ALL fields without a
hard-coded allow-list, preserving raw names (``ISBN-10``, ``Publication
Year``, ...) and normalising separately.

Search (discovery only) — both endpoints use ``urllib.parse.urlencode``;
``st`` is optional and never sent (docs/wonderclub3.txt §2):

  General: https://wonderclub.com/search_results.php?search_key=<query>
  Title:   https://wonderclub.com/books/bookbytitleexp.php?booktitle=<title>

Individual page is authoritative; search just resolves candidate URLs.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
from ratelimit import limits, sleep_and_retry

from simurg.metadata.scrapers.base import BaseScraper
from simurg.metadata.scrapers.util import (
    clean_issn,
    normalize_issue_date,
    year_from,
)

_WONDER_BASE = "https://wonderclub.com"
_SEARCH_URL = f"{_WONDER_BASE}/search_results.php"
_TITLE_URL = f"{_WONDER_BASE}/books/bookbytitleexp.php"

_CALLS = 3
_PERIOD = 10

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean_text(value) -> str:
    if not value:
        return ""
    return " ".join(str(value).split())


class WonderClubScraper(BaseScraper):
    name = "wonderclub"
    categories = {"ebook", "magazine"}
    url_domains = {"wonderclub.com"}

    def __init__(self, session=None):
        super().__init__(session)
        self.session.headers.update(_BROWSER_HEADERS)

    # ------------------------------------------------------------------ rate
    @sleep_and_retry
    @limits(_CALLS, _PERIOD)
    def _get(self, url: str, params=None):
        try:
            r = self.session.get(url, params=params, timeout=15)
        except Exception:
            return None
        if r is None or r.status_code != 200:
            return None
        return r

    # ------------------------------------------------------------------ search helpers
    def _search_general(self, query: str) -> list[dict]:
        """Search via search_results.php?search_key=...  Return candidates."""
        if not query or not query.strip():
            return []
        params = urlencode({"search_key": query.strip()})
        url = f"{_SEARCH_URL}?{params}"
        r = self._get(url)
        if r is None:
            return []
        return _parse_search_results(r.text, _WONDER_BASE)

    def _search_title(self, title: str) -> list[dict]:
        """Search via bookbytitleexp.php?booktitle=..."""
        if not title or not title.strip():
            return []
        params = urlencode({"booktitle": title.strip()})
        url = f"{_TITLE_URL}?{params}"
        r = self._get(url)
        if r is None:
            return []
        cands = _parse_search_results(r.text, _WONDER_BASE)
        if not cands:
            # fallback to general search
            return self._search_general(title)
        return cands

    def _fetch_and_parse(self, url: str) -> dict | None:
        r = self._get(url)
        if r is None:
            return None
        return _parse_page(r.text, r.url if hasattr(r, "url") else url)

    # ------------------------------------------------------------------ public API
    def search_isbn(self, isbn: str) -> dict | None:
        cleaned = re.sub(r"[^0-9Xx]", "", isbn)
        if not cleaned or len(cleaned) not in (10, 13):
            return None
        cands = self._search_general(cleaned)
        if not cands:
            return None
        # Prefer exact ISBN match (normalised) in candidate isbn field
        best_url = None
        for c in cands:
            cand_isbn = re.sub(r"[^0-9Xx]", "", str(c.get("isbn") or ""))
            if cand_isbn and cand_isbn == cleaned:
                best_url = c.get("url")
                break
            # also check raw details-like identifiers in title/url
            if cleaned in re.sub(r"[^0-9Xx]", "", c.get("url") or ""):
                best_url = c.get("url")
                break
        if not best_url:
            best_url = cands[0].get("url")
        if not best_url:
            return None
        res = self._fetch_and_parse(best_url)
        if res and not res.get("isbn"):
            res["isbn"] = cleaned
        return res

    def search_title_author(self, title: str, authors: list[str]) -> dict | None:
        q = title or ""
        if authors:
            q += " " + " ".join(str(a) for a in authors[:2])
        q = q.strip()
        if not q:
            return None
        cands = self._search_general(q)
        if not cands:
            # fallback to title-specific endpoint
            cands = self._search_title(title)
        if not cands:
            return None
        # Rank: exact title > partial (use difflib-like simple)
        ranked = _rank_candidates(cands, title, authors)
        best_url = ranked[0].get("url") if ranked else cands[0].get("url")
        if not best_url:
            return None
        return self._fetch_and_parse(best_url)

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
        cands = self._search_general(q.strip())
        # If general search yields nothing for a dated issue, try direct magazine slug
        # e.g. /magazines/penthouse-february-2002 (WonderClub's canonical magazine URL)
        if not cands and issue and issue.get("issue_date"):
            try:
                # Build slug like "penthouse-february-2002" from title + month + year
                slug_title = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
                raw_date = issue.get("issue_date") or ""
                # Support both YYYY-MM with or without precision
                m_match = re.match(r"(\d{4})-(\d{2})", str(raw_date))
                if m_match and slug_title:
                    y, m = m_match.group(1), m_match.group(2)
                    from simurg.metadata.scrapers.util import MONTHS as _MONTHS

                    month_name = _MONTHS.get(int(m), "").lower()
                    if month_name:
                        direct_slug = f"{slug_title}-{month_name}-{y}"
                        direct_url = f"{_WONDER_BASE}/magazines/{direct_slug}"
                        direct_res = self._fetch_and_parse(direct_url)
                        if direct_res and direct_res.get("title"):
                            return direct_res
                    # Also try alternative slug without month hyphen? fallback to title-year
                    # e.g. some magazines use penthouse-2002-02
                    alt_slug = f"{slug_title}-{y}-{m}"
                    alt_url = f"{_WONDER_BASE}/magazines/{alt_slug}"
                    alt_res = self._fetch_and_parse(alt_url)
                    if alt_res and alt_res.get("title"):
                        return alt_res
            except Exception:
                pass
        if not cands:
            cands = self._search_title(title)
        if not cands:
            return None
        ranked = _rank_candidates(cands, title, [])
        best_url = ranked[0].get("url") if ranked else cands[0].get("url")
        if not best_url:
            return None
        res = self._fetch_and_parse(best_url)
        if res and issue and issue.get("issue_date") and not res.get("issue_date"):
            # keep original issue for label; validation will pass with year-only fallback
            pass
        return res

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted wonderclub book/magazine page URL.

        Accepts:
        - /books/<slug>
        - /magazines/<slug>  (e.g. /magazines/penthouse-february-2002)
        - /<slug>-<isbn>  (e.g. /penthouse-9780446610339)
        - /<isbn> / numeric slugs
        www. prefix is allowed via BaseScraper.match_url semantics.
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        if "wonderclub.com" not in (parsed.netloc or "").lower():
            return None
        path = parsed.path or ""
        # Explicitly reject search / api endpoints (not product pages)
        if path.startswith("/search_results.php") or path.startswith("/books/bookbytitleexp.php"):
            return None
        # Accept /books/<slug>, /magazines/<slug>, slug-ISBN, and numeric slugs
        if (
            not re.search(r"/books/[^/]+", path)
            and not re.search(r"/magazines/[^/]+", path)
            and not re.search(r"\d{7,}", path)
        ):
            return None
        return self._fetch_and_parse(url)


# ======================================================================
# Parsing helpers
# ======================================================================


def _parse_search_results(html: str, base: str) -> list[dict]:
    """Parse WonderClub search HTML into candidate dicts.

    Must be resilient — inspect actual structure, not guess.  Look for
    anchors whose href matches /books/<slug>, /magazines/<slug>, or
    slug-ISBN product pages (e.g. /penthouse-9780446610339).
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []
    seen: set[str] = set()
    # Collect all anchors that look like book records
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href:
            continue
        # Must look like a record URL (books, magazines, or ISBN-bearing product)
        if "/books/" not in href and "/magazines/" not in href and not re.search(r"\d{7,}", href):
            continue
        # Skip navigation / tab / search anchors
        if href.startswith("#"):
            continue
        if "search_results.php" in href or "bookbytitleexp.php" in href:
            continue
        # Skip bare section indexes like /magazines/ (no product slug)
        if href.rstrip("/").endswith("/magazines") or href.rstrip("/").endswith("/books"):
            continue
        abs_url = urljoin(base, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        title = _clean_text(a.get_text(" ", strip=True))
        # Try to find sibling author / type info nearby
        parent_text = (
            _clean_text(a.find_parent().get_text(" ", strip=True)) if a.find_parent() else ""
        )
        # Heuristic author: first text after title inside same container
        author = None
        # Look for nearby small / span that might be author
        sib = a.find_next_sibling()
        if sib:
            sib_text = _clean_text(sib.get_text(" ", strip=True))
            if sib_text and sib_text != title and len(sib_text) < 80:
                author = sib_text
        # ISBN in result text
        isbn = None
        m = re.search(r"\b(?:97[89][\d\-]{10,}|[\dX][\d\-]{8,}[\dX])\b", parent_text)
        if m:
            cleaned = re.sub(r"[^0-9Xx]", "", m.group(0))
            if len(cleaned) in (10, 13):
                isbn = cleaned
        # Image near result
        img_url = None
        container = a.find_parent()
        if container:
            img = container.find("img")
            if img and (img.get("src") or img.get("data-src")):
                img_url = urljoin(base, img.get("src") or img.get("data-src") or "")
        candidates.append(
            {
                "title": title or None,
                "author": author,
                "url": abs_url,
                "type": "book",
                "isbn": isbn,
                "image": img_url,
            }
        )
    return candidates


def _rank_candidates(cands: list[dict], title: str, authors: list[str]) -> list[dict]:
    """Simple ranking: exact title > partial > general."""
    if not cands:
        return []
    title_low = (title or "").lower().strip()
    authors_low = [a.lower().strip() for a in (authors or []) if a]

    def score(c):
        s = 0
        ct = (c.get("title") or "").lower()
        ca = (c.get("author") or "").lower()
        if ct == title_low and title_low:
            s += 100
        elif title_low and title_low in ct:
            s += 50
        elif ct and title_low and any(w in ct for w in title_low.split()):
            s += 20
        for al in authors_low:
            if al and al in ca:
                s += 30
            elif al and al.split()[-1] in ca:
                s += 15
        return s

    return sorted(cands, key=score, reverse=True)


def _parse_page(html: str, url: str) -> dict | None:
    """Parse a WonderClub individual book page."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    details = _extract_details(soup)
    jsonld = _extract_jsonld(soup)
    og = _extract_og(soup)

    # Title: prefer logical page title, not raw details noise
    # For magazines, "Title of Series" is the canonical periodical title (keep verbatim, e.g. "Penthouse (USA)")
    title = None
    if details.get("Title of Series"):
        title = _clean_text(details["Title of Series"])
    if not title:
        # h1 is most reliable for wonderclub book pages; for magazines h1 contains
        # "<issue> <a>Magazine</a>" so extract first <a> to avoid trailing " Magazine"
        for sel in ("h1", ".product-title", ".book-title", "h1.product-name"):
            el = soup.select_one(sel)
            if el and _clean_text(el.get_text()):
                # Prefer first anchor text when h1 has "Title Magazine" pattern
                first_a = el.find("a")
                if first_a and _clean_text(first_a.get_text()):
                    # If h1 has two links (issue + "Magazine"), first is the issue title
                    candidate = _clean_text(first_a.get_text())
                    # Only use first-a if it looks like title and not just "Magazine"
                    if candidate.lower() != "magazine" and len(candidate) > 3:
                        # If second anchor is "Magazine", this candidate is correct issue title
                        # but for magazines we already used Title of Series above, so fallback is issue title
                        title = (
                            candidate
                            if details.get("Title of Series")
                            else _clean_text(el.get_text())
                        )
                        # Strip trailing " Magazine" suffix if present and details Title exists
                        if title.endswith(" Magazine") and details.get("Title"):
                            title = title[: -len(" Magazine")].strip()
                        else:
                            title = _clean_text(el.get_text())
                    else:
                        title = _clean_text(el.get_text())
                else:
                    title = _clean_text(el.get_text())
                # Strip trailing category suffix added via second <a>Magazine</a>
                if title.endswith(" Magazine") and details.get("Title"):
                    title = details.get("Title") or title[: -len(" Magazine")].strip()
                break
    if not title:
        title = og.get("og:title") or jsonld.get("name") or details.get("Title") or ""
    title = title.strip() if title else ""

    # Authors
    authors: list[str] = []
    # jsonld author
    ja = jsonld.get("author")
    if ja:
        if isinstance(ja, list):
            for entry in ja:
                n = entry.get("name") if isinstance(entry, dict) else str(entry)
                if n:
                    authors.append(_clean_text(n))
        elif isinstance(ja, dict) and ja.get("name"):
            authors.append(_clean_text(ja["name"]))
        elif isinstance(ja, str):
            authors.append(_clean_text(ja))
    # Details Manufacturer sometimes is author/publisher conflated — not author
    # Look for explicit author element, but skip review authors
    for sel in (".author", ".book-author", "[itemprop='author']", "a[href*='/author/']"):
        # Find ALL matches and skip those inside a review container
        for el in soup.select(sel):
            # Skip if this author is inside a review / aggregateRating block
            if el.find_parent(attrs={"itemprop": "review"}) or el.find_parent(
                attrs={"itemprop": "aggregateRating"}
            ):
                continue
            # Also skip if parent chain contains a review type
            _ = el.find_parent()
            is_review = False
            for anc in el.parents:
                try:
                    if anc.get("itemprop") == "review":
                        is_review = True
                        break
                except Exception:
                    continue
            if is_review:
                continue
            if _clean_text(el.get_text()):
                val = _clean_text(el.get_text())
                if val and val not in authors:
                    authors.append(val)
                break
        if authors:
            break
    # For magazines, there is no book author — clear review-derived authors
    # (e.g. Penthouse February 2002 review author Randall Kushell)
    if details.get("Category") and "Magazines" in str(details.get("Category")):
        # Magazine pages should not expose a book author from reviews
        if authors and not details.get("Author"):
            # Only keep authors if page explicitly lists an Author field
            authors = []
    # Fallback: details Author field (rare)
    if not authors and details.get("Author"):
        authors = [_clean_text(details["Author"])]

    # Description
    description = (
        details.get("Product Description")
        or details.get("Description")
        or jsonld.get("description")
        or og.get("og:description")
        or ""
    )
    # Try meta description fallback
    if not description:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            description = _clean_text(meta["content"])

    # Cover
    cover_url = (
        details.get("Image Location")
        or details.get("Image")
        or jsonld.get("image")
        or og.get("og:image")
        or ""
    )
    if isinstance(cover_url, list):
        cover_url = cover_url[0] if cover_url else ""
    if isinstance(cover_url, dict) and cover_url.get("url"):
        cover_url = cover_url["url"]
    cover_url = str(cover_url).strip() if cover_url else ""
    if cover_url and cover_url.startswith("//"):
        cover_url = "https:" + cover_url
    elif cover_url and cover_url.startswith("/"):
        cover_url = urljoin(_WONDER_BASE, cover_url)
    # Also look for page img if still empty
    if not cover_url:
        img = (
            soup.select_one("#menu1 img")
            or soup.select_one(".product-image img")
            or soup.find("img")
        )
        if img and img.get("src"):
            src = img["src"]
            if src.startswith("//"):
                cover_url = "https:" + src
            elif src.startswith("/"):
                cover_url = urljoin(_WONDER_BASE, src)
            else:
                cover_url = src

    # ISBN — prefer raw details ISBN-10 / ISBN, keep both raw and normalised
    raw_isbn = (
        details.get("ISBN-10")
        or details.get("ISBN")
        or details.get("ISBN-13")
        or jsonld.get("isbn")
        or ""
    )
    isbn = None
    if raw_isbn:
        cleaned = re.sub(r"[^0-9Xx]", "", str(raw_isbn))
        if len(cleaned) in (10, 13):
            isbn = cleaned
    # Also scan details values for ISBN-like strings
    if not isbn:
        for v in details.values():
            m = re.search(r"\b97[89][\d\-]{10,}\b|\b[\dX][\d\-]{8,}[\dX]\b", str(v))
            if m:
                c = re.sub(r"[^0-9Xx]", "", m.group(0))
                if len(c) in (10, 13):
                    isbn = c
                    break

    # Publisher — Manufacturer is publisher on WonderClub
    publisher = (
        details.get("Manufacturer") or details.get("Publisher") or jsonld.get("publisher") or ""
    )
    if isinstance(publisher, dict):
        publisher = publisher.get("name") or ""
    publisher = _clean_text(publisher) if publisher else ""
    # WonderClub duplicates brand name via hidden + visible spans
    # e.g. "Penthouse Penthouse" or "Abrams Books Abrams Books" -> dedupe
    if publisher:
        parts = publisher.split()
        if len(parts) % 2 == 0 and len(parts) >= 2:
            half = len(parts) // 2
            if parts[:half] == parts[half:]:
                publisher = " ".join(parts[:half])

    # Year / Publication Date (magazines use "Publication Date: February 2002")
    year = None
    raw_year = (
        details.get("Publication Date")
        or details.get("Publication Year")
        or details.get("Year")
        or ""
    )
    # Fallback: meta itemprop datePublished (e.g. <meta itemprop="datePublished" content="February 2002">)
    meta_date = soup.find("meta", attrs={"itemprop": "datePublished"})
    if not raw_year and meta_date and meta_date.get("content"):
        raw_year = _clean_text(meta_date["content"])
    if raw_year:
        year = year_from(raw_year)
    if not year and jsonld.get("datePublished"):
        year = year_from(str(jsonld["datePublished"]))
    if not year:
        # Scan details for any 4-digit year
        for v in details.values():
            y = year_from(str(v))
            if y and 1000 <= y <= 2100:
                # prefer Publication Date/Year already tried
                pass

    # ISSN (rare on WonderClub, but check)
    print_issn = None
    for k, v in details.items():
        if "issn" in k.lower():
            print_issn = clean_issn(str(v))
            if print_issn:
                break

    # Magazine Volume / Issue — prefer structured itemprop, fallback to details text
    volume = None
    issue_number = None
    vol_el = soup.select_one('[itemprop="volumeNumber"]')
    if vol_el and _clean_text(vol_el.get_text()):
        volume = _clean_text(vol_el.get_text())
    iss_el = soup.select_one('[itemprop="issueNumber"]')
    if iss_el and _clean_text(iss_el.get_text()):
        issue_number = _clean_text(iss_el.get_text())
    # Fallback: parse "Volume: 33, Issue: 6" combined value in details["Volume"]
    if not volume and details.get("Volume"):
        # details["Volume"] may be "33, Issue: 6" due to combined <p>
        vol_raw = details.get("Volume") or ""
        m = re.search(r"^\s*(\d+)", vol_raw)
        if m:
            volume = m.group(1)
        # Also try to recover Issue from same string if itemprop missing
        if not issue_number and "Issue" in vol_raw:
            m2 = re.search(r"Issue:\s*(\d+)", vol_raw)
            if m2:
                issue_number = m2.group(1)
    if not issue_number and details.get("Issue"):
        issue_number = _clean_text(details.get("Issue"))

    # WSKU / Item Number — keep verbatim for magazines (e.g. PENT200202)
    wsku = None
    for key in (
        "Item Number",
        "WonderClub Stock Keeping Unit (WSKU)",
        "WSKU",
        "Universal Product Code (UPC)",
    ):
        if details.get(key):
            wsku = _clean_text(details.get(key))
            if key == "WonderClub Stock Keeping Unit (WSKU)" and details.get("Item Number"):
                # Prefer Item Number over WSKU when both exist (they are same for magazines)
                wsku = _clean_text(details.get("Item Number"))
            break
    # Also fallback to meta sku / itemprop sku if missing
    if not wsku:
        sku_el = soup.select_one('[itemprop="sku"]')
        if sku_el and _clean_text(sku_el.get_text()):
            wsku = _clean_text(sku_el.get_text())
        else:
            sku_meta = soup.find("meta", attrs={"itemprop": "sku"})
            if sku_meta and sku_meta.get("content"):
                wsku = _clean_text(sku_meta["content"])

    # Category -> tags
    raw_category = details.get("Category") or ""
    # og:type etc not needed

    # Page count — not typical on WonderClub, leave None

    # Build normalised result; also keep raw details for caller
    result: dict = {
        "title": title or details.get("Title") or "Unknown",
        "authors": authors,
        "year": year,
        "publish_year": year,
        "first_publish_year": year,
        "first_published": year,
        "publisher": publisher or None,
        "isbn": isbn,
        "page_count": None,
        "language": None,
        "subjects": [],
        "description": description or None,
        "cover_url": cover_url or None,
        "source_urls": [url],
        "details": details,
        # Preserve WSKU / Item Number verbatim (user requested extraction)
        "wsku": wsku,
        "item_number": wsku,
        "sku": wsku,
        # Magazine-compatible mirrors
        "print_issn": print_issn,
        "electronic_issn": None,
        "country": None,
        "frequency": None,
        "issue_date": None,
        "issue_date_precision": None,
        "volume": volume,
        "issue_number": issue_number,
    }

    # Magazine issue_date from Publication Date/Year when possible
    if raw_year:
        norm, prec = normalize_issue_date(str(raw_year))
        if norm:
            result["issue_date"] = norm
            result["issue_date_precision"] = prec

    # Tags: Category -> comma-separated on same line
    # Raw Category is "Media >> Magazines >> XXX Magazines >> Perfect Women"
    # Must be split on ">>" and re-joined with "," only (user request).
    if raw_category:
        parts = [p.strip() for p in raw_category.split(">>") if p.strip()]
        # Fallback: if no ">>" found but raw contains ">" or "," keep as single
        if not parts:
            parts = [raw_category.strip()]
        result["subjects"] = parts
        result["tags"] = ",".join(parts)
    else:
        result["tags"] = ""
        result["subjects"] = []

    if not result["title"] or result["title"] == "Unknown":
        return None
    return result


def _extract_details(soup: BeautifulSoup) -> dict:
    """Dynamically extract ALL label/value pairs inside #menu1.

    Resilient to table / dl / div.row / span layouts.  Preserves original
    WonderClub field names.  For <a> values keep displayed text; for <img>
    keep src.  Returns {} if panel missing.
    """
    # Verify tab exists (tutorial §1) but don't require it
    panel = soup.select_one("#menu1")
    if panel is None:
        return {}
    details: dict[str, str] = {}

    # 1. Table rows (wonderclub.txt:113 example)
    for tr in panel.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) >= 2:
            k = _clean_text(cells[0].get_text(" ", strip=True))
            # value may be link/img
            v_el = cells[1]
            v = _extract_cell_value(v_el)
            if k and v and k not in details:
                details[k] = v
        elif len(cells) == 1:
            # Single cell with "Label: Value" ?
            txt = _clean_text(cells[0].get_text(" ", strip=True))
            if ":" in txt:
                k, v = [p.strip() for p in txt.split(":", 1)]
                if k and v and k not in details:
                    details[k] = v

    # 2. Definition lists
    for dl in panel.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds, strict=False):
            k = _clean_text(dt.get_text(" ", strip=True))
            v = _extract_cell_value(dd)
            if k and v and k not in details:
                details[k] = v

    # 3. Generic row / detail blocks
    for row in panel.select(".row"):
        # Try two-column spans/divs
        cols = row.find_all(["span", "div", "p", "td"], recursive=False)
        if len(cols) >= 2:
            k = _clean_text(cols[0].get_text(" ", strip=True).rstrip(":"))
            v = _extract_cell_value(cols[1])
            if k and v and k not in details:
                details[k] = v
        elif len(cols) == 1:
            txt = _clean_text(cols[0].get_text(" ", strip=True))
            if ":" in txt:
                k, v = [p.strip() for p in txt.split(":", 1)]
                if k and v and k not in details:
                    details[k] = v

    # 4. Fallback: any element with "Label: Value" pattern inside panel not yet captured
    if not details:
        # Last resort: split panel text by lines that look like labels (only block elements to avoid inner span URL fragments)
        for elem in panel.find_all(["div", "p", "li"]):
            txt = _clean_text(elem.get_text(" ", strip=True))
            if ":" in txt and len(txt) < 200:
                k, v = [p.strip() for p in txt.split(":", 1)]
                # skip URL scheme fragments like "https: //..."
                if k.lower() in ("https", "http") or "://" in k:
                    continue
                if k and v and len(k) < 40 and k not in details:
                    details[k] = v

    # 5. Also capture any remaining <li> or <div> that contain key: value not in table
    for li in panel.find_all("li"):
        txt = _clean_text(li.get_text(" ", strip=True))
        if ":" in txt and len(txt) < 200:
            k, v = [p.strip() for p in txt.split(":", 1)]
            if k.lower() in ("https", "http") or "://" in k:
                continue
            if k and v and k not in details:
                details[k] = v

    # 6. Post-process combined "Volume: 33, Issue: 6" case (single <p> holds both)
    # The fallback stores Volume="33, Issue: 6" — split into separate keys
    if "Volume" in details and "Issue" not in details and "Issue:" in details["Volume"]:
        vol_val = details["Volume"]
        # vol_val e.g. "33, Issue: 6" or "33, Issue:6"
        m_vol = re.search(r"^\s*(\d+)", vol_val)
        m_iss = re.search(r"Issue:\s*(\d+)", vol_val)
        if m_vol:
            details["Volume"] = m_vol.group(1)
        if m_iss:
            details["Issue"] = m_iss.group(1)
    # Remove spurious URL-scheme keys
    details.pop("https", None)
    details.pop("http", None)
    # Also handle publication date split if needed, but keep verbatim

    return details


def _extract_cell_value(el) -> str:
    """Extract text value from a cell, preserving link text or image src when relevant."""
    if el is None:
        return ""
    # If cell contains a link with meaningful text, use the link text (wonderclub.txt:217)
    a = el.find("a")
    img = el.find("img")
    if a and _clean_text(a.get_text()):
        # Keep displayed category text, not href (wonderclub2.txt:498)
        return _clean_text(a.get_text(" ", strip=True))
    if img and img.get("src"):
        src = img.get("src")
        if src:
            return _clean_text(src)
    # Check for image Location style — may be plain text URL
    txt = _clean_text(el.get_text(" ", strip=True))
    return txt


def _extract_jsonld(soup: BeautifulSoup) -> dict:
    """Extract first Book-typed JSON-LD object; tolerate malformed / arrays / @graph."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        # Unwrap arrays / @graph
        candidates = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict) and "@graph" in data:
            graph = data["@graph"]
            candidates = graph if isinstance(graph, list) else [graph]
        elif isinstance(data, dict):
            candidates = [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            types = [t] if isinstance(t, str) else (t or [])
            if any("Book" in str(x) for x in types) or "name" in obj or "isbn" in obj:
                return obj
        # Fallback: return first dict
        for obj in candidates:
            if isinstance(obj, dict):
                return obj
    return {}


def _extract_og(soup: BeautifulSoup) -> dict:
    og: dict[str, str] = {}
    for prop in ("og:title", "og:description", "og:image", "og:url"):
        el = soup.find("meta", attrs={"property": prop})
        if el and el.get("content"):
            og[prop] = _clean_text(el["content"])
    return og
