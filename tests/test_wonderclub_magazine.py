"""Tests for WonderClub magazine /magazines/* URL support."""

from simurg.metadata.scrapers.wonderclub import (
    WonderClubScraper,
    _parse_page,
    _parse_search_results,
)

# Minimal HTML snapshot of https://wonderclub.com/magazines/penthouse-february-2002
# trimmed but preserves structure used by scraper
PENTHOUSE_HTML = """
<body>
<div><h1 align="center"><span class="h-card"><span itemprop="name" class="title"><a href="https://wonderclub.com/magazines/penthouse-february-2002">Penthouse February 2002</a> <a href="https://wonderclub.com/magazines/">Magazine</a></span></span></h1></div>
<div class="xzoom-container image-wrapper">
  <a href="https://wonderclub.com/magazines/adult_magazine_single_page.php?u=PENT200202">
    <img itemprop="image" style="width:100%;" src="https://wonderclub.com/images/PENT/PENT200202.webp" />
  </a>
</div>
<ul class="nav nav-tabs">
    <li class="active"><a data-toggle="tab" href="#menu1">Details</a></li>
</ul>
<div class="tab-content">
<div id="menu1" class="tab-pane fade active in">
<p><b>Title:</b> <span itemprop="name"><a href="https://wonderclub.com/magazines/penthouse-february-2002">Penthouse February 2002</a></span></p>
<p><b>Title of Series:</b> <a href="https://wonderclub.com/magazines/magazine_history.php?magazine=penthouse">Penthouse (USA)</a></p>
<p><b>Publisher:</b> <a href="https://wonderclub.com/product_by_manufacturer.php?id=50"><span itemprop="brand"><span itemprop="name">Penthouse</span></span></a></p>
<p><b>Item Number:</b> <span itemprop="sku"><a href="https://wonderclub.com/magazines/penthouse-february-2002">PENT200202</a></span></p>
<p><b>Publication Date:</b> <meta itemprop="datePublished" content="February 2002"><a href="https://wonderclub.com/magazines/magsbymonth.php?month=February">February</a> <a href="https://wonderclub.com/magazines/adult_magazine_full_year.php?mag=PENT&year=2002">2002</a></p>
<p><b>Volume:</b> <span itemprop="volumeNumber">33</span>, <b>Issue:</b> <span itemprop="issueNumber">6</span></p>
<p><b>Product Description:</b> <b>Full Name:</b> <span itemprop="description"><a href="https://wonderclub.com/magazines/penthouse-february-2002">Penthouse February 2002</a></span></p>
<p><b>Image Location:</b> <span itemprop="image"><a href="https://wonderclub.com/images/PENT/PENT200202.webp">https://wonderclub.com/images/PENT/PENT200202.webp</a></span></p>
<p><b>Category:</b> <a href="https://wonderclub.com/product_by_category.php?cat=1">Magazines</a></p>
<p><b>WonderClub Stock Keeping Unit (WSKU):</b> <span itemprop="mpn"><a href="https://wonderclub.com/magazines/penthouse-february-2002">PENT200202</a></span></p>
<p><b>Universal Product Code (UPC):</b> <span itemprop="upc"><a href="https://wonderclub.com/magazines/penthouse-february-2002">00928102242802</a></span></p>
</div>
</div>
</body>
"""

SEARCH_HTML = """
<html><body>
<a href="https://wonderclub.com/books/some-book-123">Some Book</a>
<a href="https://wonderclub.com/magazines/penthouse-february-2002">Penthouse February 2002</a>
<a href="/magazines/penthouse-march-2002">Penthouse March 2002</a>
<a href="#menu1">Details</a>
<a href="https://wonderclub.com/search_results.php?search_key=test">Search</a>
</body></html>
"""


def test_search_url_accepts_magazines(monkeypatch):
    sc = WonderClubScraper(session=None)

    # mock network to return our HTML for magazines URL
    class FakeResp:
        status_code = 200
        text = PENTHOUSE_HTML
        url = "https://wonderclub.com/magazines/penthouse-february-2002"

    monkeypatch.setattr(sc, "_get", lambda url, params=None: FakeResp())
    res = sc.search_url("https://wonderclub.com/magazines/penthouse-february-2002")
    assert res is not None
    # Title should be series verbatim
    assert res["title"] == "Penthouse (USA)"


def test_search_url_accepts_www_prefix(monkeypatch):
    sc = WonderClubScraper(session=None)

    class FakeResp:
        status_code = 200
        text = PENTHOUSE_HTML
        url = "https://www.wonderclub.com/magazines/penthouse-february-2002"

    monkeypatch.setattr(sc, "_get", lambda url, params=None: FakeResp())
    res = sc.search_url("https://www.wonderclub.com/magazines/penthouse-february-2002")
    assert res is not None
    assert res["publisher"] == "Penthouse"


def test_search_url_accepts_books_still(monkeypatch):
    sc = WonderClubScraper(session=None)
    html = PENTHOUSE_HTML.replace("/magazines/penthouse-february-2002", "/books/some-book")
    html = html.replace("Penthouse (USA)", "Some Book")

    class FakeResp:
        status_code = 200
        text = html
        url = "https://wonderclub.com/books/some-book"

    monkeypatch.setattr(sc, "_get", lambda url, params=None: FakeResp())
    res = sc.search_url("https://wonderclub.com/books/some-book")
    assert res is not None


def test_search_url_rejects_unrelated():
    sc = WonderClubScraper(session=None)
    assert sc.search_url("https://wonderclub.com/search_results.php?search_key=test") is None
    assert sc.search_url("https://example.com/magazines/penthouse") is None


def test_parse_search_results_includes_magazines():
    cands = _parse_search_results(SEARCH_HTML, "https://wonderclub.com")
    urls = [c["url"] for c in cands]
    assert any("/magazines/penthouse-february-2002" in u for u in urls)
    assert any("/books/some-book" in u for u in urls)
    # tab anchor should be excluded
    assert not any(u.endswith("#menu1") for u in urls)


def test_parse_page_magazine_penthouse():
    res = _parse_page(PENTHOUSE_HTML, "https://wonderclub.com/magazines/penthouse-february-2002")
    assert res is not None
    # Keep verbatim Title of Series
    assert res["title"] == "Penthouse (USA)"
    assert res["details"]["Title of Series"] == "Penthouse (USA)"
    # Publisher
    assert res["publisher"] == "Penthouse"
    # Year from Publication Date
    assert res["year"] == 2002
    assert res["issue_date"] == "2002-02"
    assert res["issue_date_precision"] == "month"
    # Volume / Issue via itemprop
    assert res["volume"] == "33"
    assert res["issue_number"] == "6"
    # Details should have split Volume/Issue
    assert res["details"]["Volume"] == "33"
    assert res["details"]["Issue"] == "6"
    # Cover via Image Location
    assert res["cover_url"] == "https://wonderclub.com/images/PENT/PENT200202.webp"
    # WSKU / Item Number verbatim
    assert res["wsku"] == "PENT200202"
    assert res["item_number"] == "PENT200202"
    assert res["details"]["Item Number"] == "PENT200202"
    # Category
    assert "Magazines" in res["details"]["Category"]


def test_parse_page_combined_volume_issue_fallback():
    # HTML with only combined string, no itemprop
    html = """
    <div id="menu1">
    <p><b>Title:</b> Test Mag March 2002</p>
    <p><b>Title of Series:</b> Test Mag</p>
    <p><b>Publication Date:</b> March 2002</p>
    <p><b>Volume:</b> 10, <b>Issue:</b> 3</p>
    <p><b>Publisher:</b> TestPub</p>
    </div>
    """
    res = _parse_page(html, "https://wonderclub.com/magazines/test-mag-march-2002")
    assert res["volume"] == "10"
    assert res["issue_number"] == "3"


def test_enricher_search_by_url_routes_to_wonderclub(monkeypatch):
    import requests

    from simurg.metadata.enricher import search_by_url

    # Stub WonderClubScraper.search_url directly (bypass network + ratelimit)
    from simurg.metadata.scrapers.wonderclub import WonderClubScraper as WCS
    from simurg.metadata.scrapers.wonderclub import _parse_page

    def fake_search_url(self, url):
        if "penthouse-february-2002" in url:
            return _parse_page(PENTHOUSE_HTML, url)
        return None

    monkeypatch.setattr(WCS, "search_url", fake_search_url)
    res = search_by_url(
        "https://wonderclub.com/magazines/penthouse-february-2002", requests.Session()
    )
    assert res is not None
    assert res["_scraper"] == "wonderclub"
    assert res["title"] == "Penthouse (USA)"
