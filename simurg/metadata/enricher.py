"""Enricher - parallel scraper queries, always scraped fresh (no cache).

Two entry points:

* ``search_all_scrapers`` — query every scraper and return ALL hits (each
  tagged with ``_scraper`` + fuzzy-match scores). Used by the interactive
  ``up`` flow so the user can pick which result to fill metadata from.
* ``enrich_metadata`` — legacy best-effort auto-pick of the single best hit.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import requests

from simurg.metadata.scrapers.abebooks import AbeBooksScraper
from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
from simurg.metadata.scrapers.crossref import CrossrefScraper
from simurg.metadata.scrapers.goodreads import GoodreadsScraper
from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
from simurg.metadata.scrapers.internetarchive import InternetArchiveScraper
from simurg.metadata.scrapers.libraryofcongress import LibraryOfCongressScraper
from simurg.metadata.scrapers.librarything import LibraryThingScraper
from simurg.metadata.scrapers.openalex import OpenAlexScraper
from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper
from simurg.metadata.scrapers.penguinrandomhouse import PenguinRandomHouseScraper
from simurg.metadata.scrapers.wonderclub import WonderClubScraper


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# Weighting: title dominates, author matters, edition year breaks ties.
# Junk cutoffs punish unrelated works (wrong title/author get dropped).
_TITLE_WEIGHT = 0.5
_AUTHOR_WEIGHT = 0.3
_YEAR_WEIGHT = 0.2
_TITLE_JUNK_CUTOFF = 0.5
_AUTHOR_JUNK_CUTOFF = 0.4


def _extract_year(inbuilt: dict) -> int | None:
    """Pull the inbuilt edition year as an int, tolerating str/date forms."""
    for key in ("year", "publish_year"):
        y = inbuilt.get(key)
        if isinstance(y, int) and 1000 <= y <= 2100:
            return y
        if isinstance(y, str):
            m = re.search(r"(\d{4})", y)
            if m and 1000 <= int(m.group(1)) <= 2100:
                return int(m.group(1))
    return None


def _fuzzy_year(query_year: int | None, result: dict) -> float:
    """Soft year score: exact=1.0, ±1=0.6, ±2=0.3, then linear decay.

    Unknown query or result year is neutral (0.5) — never punished, never
    preferred. Wrong years are down-ranked, never hidden.
    """
    if not query_year:
        return 0.5
    raw = result.get("year") or result.get("publish_year")
    try:
        diff = abs(int(query_year) - int(str(raw)[:4] if isinstance(raw, str) else raw))
    except (TypeError, ValueError):
        return 0.5
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.6
    if diff == 2:
        return 0.3
    return max(0.0, 1.0 - diff / 10.0)


def _tag_result(
    res: dict,
    scraper_name: str,
    title: str,
    authors: list[str],
    year: int | None = None,
) -> None:
    """Attach provenance + fuzzy-match scores to a scraper result (in place)."""
    res["_scraper"] = scraper_name
    res["_fuzzy_title"] = 1.0
    res["_fuzzy_author"] = 0.0
    if title:
        res["_fuzzy_title"] = _fuzzy(title, res.get("title") or "")
    if authors and res.get("authors"):
        res["_fuzzy_author"] = max(_fuzzy(a1, a2) for a1 in authors for a2 in res["authors"])
    res["_fuzzy_year"] = _fuzzy_year(year, res)
    res["_score"] = (
        _TITLE_WEIGHT * res["_fuzzy_title"]
        + _AUTHOR_WEIGHT * res["_fuzzy_author"]
        + _YEAR_WEIGHT * res["_fuzzy_year"]
    )


def _is_junk(res: dict, authors_known: bool) -> bool:
    """True when the result is unrelated to the query (punish random works)."""
    if res.get("_fuzzy_title", 0) < _TITLE_JUNK_CUTOFF:
        return True
    return bool(authors_known) and res.get("_fuzzy_author", 0) < _AUTHOR_JUNK_CUTOFF


def _result_score(res: dict) -> float:
    if "_score" in res:
        return float(res["_score"])
    return float(res.get("_fuzzy_title", 0)) + float(res.get("_fuzzy_author", 0))


def _call_title_author(sc, title: str, authors: list[str], year: int | None):
    """Call search_title_author with year, tolerating old two-arg mocks."""
    try:
        return sc.search_title_author(title, authors, year=year)
    except TypeError:
        return sc.search_title_author(title, authors)


def _clean_isbn(isbn) -> str | None:
    if not isbn:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", str(isbn))
    return cleaned if len(cleaned) in (10, 13) else None


def _all_scrapers(session: requests.Session):
    return [
        OpenLibraryScraper(session),
        GoogleBooksScraper(session),
        BookBrainzScraper(session),
        AbeBooksScraper(session),
        GoodreadsScraper(session),
        PenguinRandomHouseScraper(session),
        LibraryThingScraper(session),
        WonderClubScraper(session),
        # Magazine-only scrapers (tagged {"magazine"}; excluded from ebook path)
        InternetArchiveScraper(session),
        LibraryOfCongressScraper(session),
        CrossrefScraper(session),
        OpenAlexScraper(session),
    ]


def _scrapers_for(session: requests.Session, categories: set[str]):
    """Return scrapers whose ``categories`` intersect the requested set."""
    return [s for s in _all_scrapers(session) if s.categories & categories]


def supported_url_domains() -> set[str]:
    """Return the set of netlocs a pasted URL may be routed to."""
    domains: set[str] = set()
    for sc in _all_scrapers(requests.Session()):
        domains |= set(getattr(sc, "url_domains", set()))
    return domains


def search_by_url(url: str, session: requests.Session | None = None) -> dict | None:
    """Route a user-pasted book-page URL to the matching scraper.

    Finds the scraper whose ``url_domains`` matches the URL's netloc, calls its
    ``search_url``, and returns the result tagged with ``_scraper`` plus fuzzy
    provenance (it shares the result shape of ``search_all_scrapers``). Returns
    ``None`` when no scraper handles the domain or the fetch yields nothing.
    """
    if not url or not str(url).strip():
        return None
    session = session or requests.Session()
    for sc in _all_scrapers(session):
        if not getattr(sc, "url_domains", None):
            continue
        if not sc.match_url(url):
            continue
        try:
            res = sc.search_url(url)
        except Exception:
            return None
        if res and res.get("title"):
            _tag_result(res, sc.name, "", [])
            return res
    return None


def search_all_scrapers(
    inbuilt: dict, session: requests.Session | None = None, categories: set[str] | None = None
) -> list[dict]:
    """Query every scraper in the requested category and return ALL hits.

    ISBN queries run first when a valid ISBN is present; if they yield no
    results (or none is a confident match), title+author searches run and are
    appended too. Each returned dict is tagged with ``_scraper`` plus
    ``_fuzzy_title``/``_fuzzy_author`` so the caller can rank/display them.
    Returns [] when nothing was found.

    ``categories`` defaults to ``{"ebook"}`` so the ebook path is unchanged.
    """
    title = (inbuilt.get("title") or "").strip()
    authors = inbuilt.get("authors") or []
    isbn = _clean_isbn(inbuilt.get("isbn"))
    year = _extract_year(inbuilt)
    session = session or requests.Session()
    scrapers = _scrapers_for(session, categories or {"ebook"})

    results: list[dict] = []
    isbn_results: list[dict] = []

    if isbn:
        for sc in scrapers:
            try:
                res = sc.search_isbn(isbn)
            except Exception:
                continue
            if res and res.get("title"):
                _tag_result(res, sc.name, title, authors, year)
                isbn_results.append(res)
        results.extend(isbn_results)

    # Fall back to title+author when there is no ISBN, or the ISBN hits exist
    # but none is a confident match — give the user the broader choice.
    high_conf = [
        r
        for r in isbn_results
        if r.get("_fuzzy_title", 1) >= 0.8 or r.get("_fuzzy_author", 0) >= 0.8
    ]
    if (not isbn_results) or (not high_conf):
        if title:
            for sc in scrapers:
                try:
                    res = _call_title_author(sc, title, authors, year)
                except Exception:
                    continue
                # Scrapers may return several edition hits (Google Books).
                hits = res if isinstance(res, list) else [res]
                for hit in hits:
                    if hit and hit.get("title"):
                        _tag_result(hit, sc.name, title, authors, year)
                        if _is_junk(hit, bool(authors)):
                            continue
                        results.append(hit)
    return results


def search_custom(
    query: str, session: requests.Session | None = None, categories: set[str] | None = None
) -> list[dict]:
    """Query every scraper with a freeform user-entered search string.

    The ``query`` is passed as the title to ``search_title_author`` (with an
    empty author list) so scrapers treat it as a raw keyword search.  Each
    returned dict is tagged with ``_scraper`` and fuzzy scores (title=1.0,
    author=0.0) matching the shape of ``search_all_scrapers`` results.
    Returns [] when nothing was found.
    """
    query = (query or "").strip()
    if not query:
        return []
    session = session or requests.Session()
    scrapers = _scrapers_for(session, categories or {"ebook"})
    results: list[dict] = []
    for sc in scrapers:
        try:
            res = _call_title_author(sc, query, [], None)
        except Exception:
            continue
        hits = res if isinstance(res, list) else [res]
        for hit in hits:
            if hit and hit.get("title"):
                _tag_result(hit, sc.name, query, [])
                results.append(hit)
    return results


def search_magazine_scrapers(inbuilt: dict, session: requests.Session | None = None) -> list[dict]:
    """Query only magazine-category scrapers; return ALL tagged hits.

    The result dicts carry the same ``_scraper``/``_fuzzy_title`` provenance
    tags as ``search_all_scrapers`` so the interactive picker can display them.
    """
    title = (inbuilt.get("canonical_title") or inbuilt.get("title") or "").strip()
    issue = {
        k: inbuilt.get(k)
        for k in ("issue_date", "issue_number", "volume", "year")
        if inbuilt.get(k)
    }
    session = session or requests.Session()
    if not title:
        return []
    scrapers = _scrapers_for(session, {"magazine"})
    results: list[dict] = []
    for sc in scrapers:
        try:
            res = sc.search_magazine(title, issue)
        except Exception:
            continue
        if res and res.get("title"):
            _tag_result(res, sc.name, title, [])
            results.append(res)
    return results


def rank_results(results: list[dict]) -> dict | None:
    """Return the best-ranked hit (provenance tags stripped) or None."""
    if not results:
        return None
    best = max(results, key=_result_score)
    return {k: v for k, v in best.items() if not k.startswith("_")}


# Fields where, when the primary result is empty, we fill from other ISBN sources.
_SCALAR_FILL_FIELDS = (
    "publisher",
    "year",
    "first_publish_year",
    "publish_year",
    "page_count",
    "number_of_pages",
    "description",
    "synopsis",
    "cover_url",
    "isbn",
)
# List fields: merge + dedupe (primary's values preserved, others appended).
_LIST_MERGE_FIELDS = (
    "authors",
    "subjects",
    "tags",
    "translators",
    "editors",
    "illustrators",
    "source_urls",
)
# Fields that stay authoritative from the primary match (never overwritten).
_PRIMARY_LOCKED_FIELDS = ("title", "authors", "year", "first_publish_year", "publish_year")


def search_all_by_isbn(isbn: str, session: requests.Session | None = None) -> list[dict]:
    """Re-query every scraper by a known ISBN and return ALL hits.

    Each hit is tagged with ``_scraper`` (and fuzzy scores of 1.0/0.0) so it
    shares the shape of ``search_all_scrapers`` results. Returns [] if no hits.
    """
    isbn = _clean_isbn(isbn)
    if not isbn:
        return []
    session = session or requests.Session()
    scrapers = _all_scrapers(session)
    results: list[dict] = []
    for sc in scrapers:
        try:
            res = sc.search_isbn(isbn)
        except Exception:
            continue
        if res and res.get("title"):
            _tag_result(res, sc.name, "", [])
            results.append(res)
    return results


def merge_fill_gaps(primary: dict, isbn_hits: list[dict]) -> dict:
    """Return a copy of ``primary`` with empty fields filled from ``isbn_hits``.

    Policy (per design): the primary match stays authoritative for
    ``title``/``authors``/``year`` — those are never overwritten, guarding
    against edition drift. Other scalar fields are filled only when the
    primary's value is empty/falsy. List fields (authors etc.) are merged and
    de-duplicated, preserving primary-first order.
    """
    if not primary:
        primary = {}
    merged = dict(primary)
    for hit in isbn_hits:
        if not hit:
            continue
        for field in _SCALAR_FILL_FIELDS:
            if field in _PRIMARY_LOCKED_FIELDS:
                continue
            cur = merged.get(field)
            if cur in (None, "", [], {}):
                val = hit.get(field)
                if val not in (None, "", [], {}):
                    merged[field] = val
        for field in _LIST_MERGE_FIELDS:
            if field in _PRIMARY_LOCKED_FIELDS:
                continue
            extra = hit.get(field)
            if not extra:
                continue
            if not isinstance(extra, (list, tuple)):
                extra = [extra]
            base = merged.get(field)
            if not isinstance(base, (list, tuple)):
                base = [base] if base not in (None, "", []) else []
            seen = {str(x).strip().lower() for x in base if str(x).strip()}
            combined = list(base)
            for item in extra:
                s = str(item).strip()
                if not s or s.lower() in seen:
                    continue
                seen.add(s.lower())
                combined.append(item)
            merged[field] = combined
    return merged


def enrich_metadata(inbuilt: dict, session: requests.Session | None = None) -> dict:
    """Enrich missing fields via scrapers. Returns best scraper dict or empty."""
    candidates = search_all_scrapers(inbuilt, session)
    if not candidates:
        return {}
    if _clean_isbn(inbuilt.get("isbn")):
        # C9: only overwrite if isbn match is confident (title/authors fuzzy >=0.8)
        high_conf = [
            c
            for c in candidates
            if c.get("_fuzzy_title", 1) >= 0.8 or c.get("_fuzzy_author", 0) >= 0.8
        ]
        return rank_results(high_conf) or {}
    # No ISBN: require both title and author confidence (when author known).
    authors = inbuilt.get("authors") or []
    best = max(candidates, key=_result_score)
    bt = best.get("_fuzzy_title", 0)
    ba = best.get("_fuzzy_author", 0)
    if bt >= 0.7 and (ba >= 0.7 if authors else True):
        return {k: v for k, v in best.items() if not k.startswith("_")}
    return {}
