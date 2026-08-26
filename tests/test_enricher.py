import pytest

from simurg.metadata.enricher import (
    enrich_metadata,
    merge_fill_gaps,
    rank_results,
    search_all_by_isbn,
    search_all_scrapers,
    search_by_url,
    supported_url_domains,
)


@pytest.fixture(autouse=True)
def _stub_penguinrandomhouse(monkeypatch):
    """Keep the PRH scraper offline by default; individual tests may override."""
    from simurg.metadata.scrapers.librarything import LibraryThingScraper
    from simurg.metadata.scrapers.openalex import OpenAlexScraper
    from simurg.metadata.scrapers.penguinrandomhouse import PenguinRandomHouseScraper
    from simurg.metadata.scrapers.wonderclub import WonderClubScraper

    monkeypatch.setattr(PenguinRandomHouseScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(PenguinRandomHouseScraper, "search_title_author", lambda s, *a, **k: None)
    monkeypatch.setattr(PenguinRandomHouseScraper, "search_url", lambda s, *a, **k: None)
    monkeypatch.setattr(LibraryThingScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(LibraryThingScraper, "search_title_author", lambda s, *a, **k: None)
    monkeypatch.setattr(LibraryThingScraper, "search_url", lambda s, *a, **k: None)
    monkeypatch.setattr(LibraryThingScraper, "search_magazine", lambda s, *a, **k: None)
    monkeypatch.setattr(WonderClubScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(WonderClubScraper, "search_title_author", lambda s, *a, **k: None)
    monkeypatch.setattr(WonderClubScraper, "search_url", lambda s, *a, **k: None)
    monkeypatch.setattr(WonderClubScraper, "search_magazine", lambda s, *a, **k: None)
    monkeypatch.setattr(OpenAlexScraper, "search_magazine", lambda s, *a, **k: None)
    monkeypatch.setattr(OpenAlexScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(OpenAlexScraper, "search_title_author", lambda s, *a, **k: None)


def test_search_all_scrapers_collects_every_hit(monkeypatch):
    from simurg.metadata.scrapers.abebooks import AbeBooksScraper
    from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
    from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper

    monkeypatch.setattr(
        OpenLibraryScraper,
        "search_title_author",
        lambda s, t, a: {"title": "Dune", "authors": ["Frank Herbert"], "publisher": "Ace"},
    )
    monkeypatch.setattr(
        GoogleBooksScraper,
        "search_title_author",
        lambda s, t, a: {
            "title": "Dune",
            "authors": ["Frank Herbert"],
            "publisher": "Ace Books",
            "page_count": 412,
        },
    )
    monkeypatch.setattr(BookBrainzScraper, "search_title_author", lambda s, t, a: None)
    monkeypatch.setattr(AbeBooksScraper, "search_title_author", lambda s, t, a: None)
    for sc in (OpenLibraryScraper, GoogleBooksScraper, BookBrainzScraper, AbeBooksScraper):
        monkeypatch.setattr(sc, "search_isbn", lambda s, *a, **k: None)

    res = search_all_scrapers({"title": "Dune", "authors": ["Frank Herbert"], "isbn": None})
    assert len(res) == 2
    scrapers = {r["_scraper"] for r in res}
    assert scrapers == {"openlibrary", "googlebooks"}
    assert all("_fuzzy_title" in r and "_fuzzy_author" in r for r in res)


def test_search_all_scrapers_empty(monkeypatch):
    from simurg.metadata.scrapers.abebooks import AbeBooksScraper
    from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
    from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
    from simurg.metadata.scrapers.librarything import LibraryThingScraper
    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper
    from simurg.metadata.scrapers.penguinrandomhouse import PenguinRandomHouseScraper
    from simurg.metadata.scrapers.wonderclub import WonderClubScraper

    for sc in (
        OpenLibraryScraper,
        GoogleBooksScraper,
        BookBrainzScraper,
        AbeBooksScraper,
        PenguinRandomHouseScraper,
        LibraryThingScraper,
        WonderClubScraper,
    ):
        monkeypatch.setattr(sc, "search_title_author", lambda s, *a, **k: None)
        monkeypatch.setattr(sc, "search_isbn", lambda s, *a, **k: None)

    assert search_all_scrapers({"title": "Whatever", "authors": [], "isbn": None}) == []


def test_rank_results_best_first(monkeypatch):
    from simurg.metadata.scrapers.abebooks import AbeBooksScraper
    from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
    from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper

    monkeypatch.setattr(
        OpenLibraryScraper,
        "search_title_author",
        lambda s, t, a: {"title": "Dune", "authors": ["Frank Herbert"], "publisher": "Ace"},
    )
    monkeypatch.setattr(
        GoogleBooksScraper,
        "search_title_author",
        lambda s, t, a: {"title": "Totally Different Book", "authors": ["Someone Else"]},
    )
    monkeypatch.setattr(BookBrainzScraper, "search_title_author", lambda s, t, a: None)
    monkeypatch.setattr(AbeBooksScraper, "search_title_author", lambda s, t, a: None)
    for sc in (OpenLibraryScraper, GoogleBooksScraper, BookBrainzScraper, AbeBooksScraper):
        monkeypatch.setattr(sc, "search_isbn", lambda s, *a, **k: None)

    res = search_all_scrapers({"title": "Dune", "authors": ["Frank Herbert"], "isbn": None})
    best = rank_results(res)
    assert best["title"] == "Dune"
    assert "_scraper" not in best  # provenance tags stripped


def test_enrich_no_isbn_low_fuzzy_returns_empty(monkeypatch):
    # Mock all scrapers to return low fuzzy match for title "Book One" -> "Random Book"
    from simurg.metadata.scrapers.abebooks import AbeBooksScraper
    from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
    from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper

    def fake_empty(self, *a, **k):
        return {"title": "Totally Unrelated Title XYZ", "authors": ["Nobody"], "publisher": "Pub"}

    monkeypatch.setattr(OpenLibraryScraper, "search_title_author", fake_empty)
    monkeypatch.setattr(GoogleBooksScraper, "search_title_author", fake_empty)
    monkeypatch.setattr(BookBrainzScraper, "search_title_author", lambda s, *a, **k: None)
    monkeypatch.setattr(AbeBooksScraper, "search_title_author", lambda s, *a, **k: None)
    monkeypatch.setattr(OpenLibraryScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(GoogleBooksScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(BookBrainzScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(AbeBooksScraper, "search_isbn", lambda s, *a, **k: None)

    res = enrich_metadata({"title": "Book One", "authors": ["Author A"], "isbn": None})
    # low fuzzy <0.7 should return empty
    assert res == {}


def test_enrich_high_fuzzy_returns_result(monkeypatch):
    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper

    def fake_good(self, title, authors):
        return {"title": title, "authors": authors, "publisher": "Pub", "year": 2020}

    monkeypatch.setattr(OpenLibraryScraper, "search_title_author", fake_good)
    # others return None
    from simurg.metadata.scrapers.abebooks import AbeBooksScraper
    from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
    from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper

    monkeypatch.setattr(GoogleBooksScraper, "search_title_author", lambda s, *a, **k: None)
    monkeypatch.setattr(BookBrainzScraper, "search_title_author", lambda s, *a, **k: None)
    monkeypatch.setattr(AbeBooksScraper, "search_title_author", lambda s, *a, **k: None)
    monkeypatch.setattr(OpenLibraryScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(GoogleBooksScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(BookBrainzScraper, "search_isbn", lambda s, *a, **k: None)
    monkeypatch.setattr(AbeBooksScraper, "search_isbn", lambda s, *a, **k: None)

    res = enrich_metadata({"title": "Dune", "authors": ["Frank Herbert"], "isbn": None})
    assert res["title"] == "Dune"
    assert res["publisher"] == "Pub"


def test_merge_fill_gaps_fills_empty_scalar_fields(monkeypatch):
    from simurg.metadata.scrapers.abebooks import AbeBooksScraper
    from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
    from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper

    for sc in (OpenLibraryScraper, GoogleBooksScraper, BookBrainzScraper, AbeBooksScraper):
        monkeypatch.setattr(sc, "search_isbn", lambda s, *a, **k: None)

    primary = {
        "title": "Dune",
        "authors": ["Frank Herbert"],
        "year": 1965,
        "publisher": "",  # empty -> should be filled
        "page_count": None,  # empty -> should be filled
        "subjects": ["science fiction"],
    }
    hits = [
        {"_scraper": "openlibrary", "publisher": "Ace", "subjects": ["science fiction", "desert"]},
        {"_scraper": "googlebooks", "page_count": 412, "description": "A saga of Arrakis."},
    ]
    merged = merge_fill_gaps(primary, hits)
    assert merged["publisher"] == "Ace"
    assert merged["page_count"] == 412
    assert merged["description"] == "A saga of Arrakis."


def test_merge_fill_gaps_keeps_primary_authoritative(monkeypatch):
    from simurg.metadata.scrapers.abebooks import AbeBooksScraper
    from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
    from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper

    for sc in (OpenLibraryScraper, GoogleBooksScraper, BookBrainzScraper, AbeBooksScraper):
        monkeypatch.setattr(sc, "search_isbn", lambda s, *a, **k: None)

    primary = {"title": "Dune", "authors": ["Frank Herbert"], "year": 1965}
    hits = [
        {
            "_scraper": "openlibrary",
            "title": "Dune (Illustrated)",
            "authors": ["F. Herbert"],
            "year": 2018,
        },
    ]
    merged = merge_fill_gaps(primary, hits)
    # Locked fields must NOT be overwritten by other sources.
    assert merged["title"] == "Dune"
    assert merged["authors"] == ["Frank Herbert"]
    assert merged["year"] == 1965


def test_merge_fill_gaps_merges_lists_deduped(monkeypatch):
    from simurg.metadata.scrapers.abebooks import AbeBooksScraper
    from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
    from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper

    for sc in (OpenLibraryScraper, GoogleBooksScraper, BookBrainzScraper, AbeBooksScraper):
        monkeypatch.setattr(sc, "search_isbn", lambda s, *a, **k: None)

    primary = {"title": "Dune", "authors": ["Frank Herbert"], "subjects": ["science fiction"]}
    hits = [
        {"_scraper": "openlibrary", "subjects": ["science fiction", "desert"]},
        {"_scraper": "googlebooks", "subjects": ["desert", "classic"]},
    ]
    merged = merge_fill_gaps(primary, hits)
    assert merged["subjects"] == ["science fiction", "desert", "classic"]


def test_search_all_by_isbn_aggregates_hits(monkeypatch):
    from simurg.metadata.scrapers.abebooks import AbeBooksScraper
    from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
    from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
    from simurg.metadata.scrapers.librarything import LibraryThingScraper
    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper
    from simurg.metadata.scrapers.penguinrandomhouse import PenguinRandomHouseScraper
    from simurg.metadata.scrapers.wonderclub import WonderClubScraper

    monkeypatch.setattr(
        OpenLibraryScraper,
        "search_isbn",
        lambda s, i: {"title": "Dune", "isbn": i, "publisher": "Ace"},
    )
    monkeypatch.setattr(
        GoogleBooksScraper,
        "search_isbn",
        lambda s, i: {"title": "Dune", "isbn": i, "page_count": 412},
    )
    monkeypatch.setattr(BookBrainzScraper, "search_isbn", lambda s, i: None)
    monkeypatch.setattr(AbeBooksScraper, "search_isbn", lambda s, i: None)
    monkeypatch.setattr(PenguinRandomHouseScraper, "search_isbn", lambda s, i: None)
    monkeypatch.setattr(LibraryThingScraper, "search_isbn", lambda s, i: None)
    monkeypatch.setattr(WonderClubScraper, "search_isbn", lambda s, i: None)

    import requests

    hits = search_all_by_isbn("9780441013593", requests.Session())
    assert len(hits) == 2
    names = {h["_scraper"] for h in hits}
    assert names == {"openlibrary", "googlebooks"}
    assert all(h.get("_fuzzy_title") == 1.0 for h in hits)


def test_search_all_by_isbn_invalid_returns_empty(monkeypatch):
    import requests

    assert search_all_by_isbn("not-an-isbn", requests.Session()) == []


def test_supported_url_domains_lists_scrapers():
    domains = supported_url_domains()
    assert "openlibrary.org" in domains
    assert "books.google.com" in domains
    assert "bookbrainz.org" in domains
    assert "abebooks.com" in domains
    assert "archive.org" in domains
    assert "loc.gov" in domains
    assert "example.com" not in domains


def test_search_by_url_routes_to_scraper(monkeypatch):
    import requests

    from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper

    monkeypatch.setattr(
        OpenLibraryScraper,
        "search_url",
        lambda s, url: {"title": "Dune", "authors": ["Frank Herbert"], "publisher": "Ace"},
    )
    res = search_by_url("https://openlibrary.org/books/OL123", requests.Session())
    assert res is not None
    assert res["title"] == "Dune"
    assert res["_scraper"] == "openlibrary"
    assert "_fuzzy_title" in res


def test_search_by_url_unsupported_domain_returns_none(monkeypatch):
    import requests

    res = search_by_url("https://example.com/some/book", requests.Session())
    assert res is None


def test_search_by_url_empty_returns_none(monkeypatch):
    import requests

    assert search_by_url("", requests.Session()) is None
    assert search_by_url(None, requests.Session()) is None
