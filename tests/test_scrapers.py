import pytest

from simurg.metadata.scrapers.abebooks import AbeBooksScraper
from simurg.metadata.scrapers.base import BaseScraper
from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper
from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper
from simurg.metadata.scrapers.internetarchive import InternetArchiveScraper
from simurg.metadata.scrapers.libraryofcongress import LibraryOfCongressScraper
from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper


class DummyResponse:
    def __init__(self, json_data=None, text="", status_code=200, url="https://example.com/"):
        self._json = json_data or {}
        self.text = text
        self.status_code = status_code
        self.headers = {}
        self.url = url

    def json(self):
        return self._json


def test_openlibrary_search_isbn(monkeypatch):
    sc = OpenLibraryScraper()
    isbn = "9780441172719"

    def fake_get(url, params=None, timeout=10, **kwargs):
        if "api/books" in url:
            return DummyResponse(
                {
                    f"ISBN:{isbn}": {
                        "title": "Dune",
                        "authors": [{"name": "Frank Herbert"}],
                        "publishers": [{"name": "Ace"}],
                        "publish_date": "1965",
                        "number_of_pages": 896,
                        "cover": {"large": "https://covers.example.com/dune.jpg"},
                        "url": "https://openlibrary.org/books/OL123",
                    }
                }
            )
        return DummyResponse()

    monkeypatch.setattr(sc.session, "get", fake_get)

    res = sc.search_isbn(isbn)
    assert res["title"] == "Dune"
    assert res["isbn"] == isbn
    assert res["publisher"] == "Ace"


def test_openlibrary_search_isbn_miss(monkeypatch):
    sc = OpenLibraryScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        return DummyResponse({"": {}}, status_code=200)

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_isbn("0000000000")
    assert res is None


def test_googlebooks_search_isbn(monkeypatch):
    sc = GoogleBooksScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        return DummyResponse(
            {
                "items": [
                    {
                        "volumeInfo": {
                            "title": "Dune",
                            "authors": ["Frank Herbert"],
                            "publisher": "Ace",
                            "publishedDate": "1965",
                            "pageCount": 412,
                            "categories": ["Fiction"],
                            "description": "desc",
                            "imageLinks": {"thumbnail": "http://example.com/thumb.jpg"},
                            "infoLink": "https://books.google.com/books?id=123",
                            "industryIdentifiers": [
                                {"type": "ISBN_13", "identifier": "9780441172719"}
                            ],
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_isbn("9780441172719")
    assert res["title"] == "Dune"
    assert res["publisher"] == "Ace"
    assert res["cover_url"].startswith("https://")


def test_googlebooks_cover_upgrade(monkeypatch):
    sc = GoogleBooksScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        return DummyResponse(
            {
                "items": [
                    {
                        "volumeInfo": {
                            "title": "Cover Book",
                            "imageLinks": {
                                "thumbnail": (
                                    "http://books.google.com/books/content?id=ABC"
                                    "&printsec=frontcover&img=1&zoom=1&edge=curl"
                                    "&source=gbs_api"
                                )
                            },
                            "industryIdentifiers": [
                                {"type": "ISBN_13", "identifier": "9780000000000"}
                            ],
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_isbn("9780000000000")
    assert res["cover_url"] == (
        "https://books.google.com/books/content?id=ABC"
        "&printsec=frontcover&img=1&zoom=3"
        "&source=gbs_api"
    )


def test_googlebooks_description_cleaned(monkeypatch):
    sc = GoogleBooksScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        return DummyResponse(
            {
                "items": [
                    {
                        "volumeInfo": {
                            "title": "Dirty Desc",
                            "description": "A &amp; B<br/>Second line   padded",
                            "industryIdentifiers": [
                                {"type": "ISBN_13", "identifier": "9780000000001"}
                            ],
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_isbn("9780000000001")
    assert res["description"] == "A & B Second line padded"


def test_googlebooks_prefers_isbn13(monkeypatch):
    sc = GoogleBooksScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        return DummyResponse(
            {
                "items": [
                    {
                        "volumeInfo": {
                            "title": "Mixed IDs",
                            "industryIdentifiers": [
                                {"type": "ISBN_10", "identifier": "0-316-00000-0"},
                                {"type": "ISBN_13", "identifier": "9780316000000"},
                            ],
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_isbn("9780316000000")
    assert res["isbn"] == "9780316000000"


def test_googlebooks_title_ranking(monkeypatch):
    sc = GoogleBooksScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        return DummyResponse(
            {
                "items": [
                    {
                        "volumeInfo": {
                            "title": "The Leftovers Something Else",
                            "industryIdentifiers": [
                                {"type": "ISBN_13", "identifier": "9780000000002"}
                            ],
                        }
                    },
                    {
                        "volumeInfo": {
                            "title": "The Leftovers",
                            "industryIdentifiers": [
                                {"type": "ISBN_13", "identifier": "9780000000003"}
                            ],
                        }
                    },
                ]
            }
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_title_author("The Leftovers", [])
    assert res["title"] == "The Leftovers"


def test_googlebooks_viewapi_fallback(monkeypatch):
    sc = GoogleBooksScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        if "googleapis.com" in url:
            return DummyResponse({"items": []})  # API returns nothing
        # viewapi fallback
        return DummyResponse(
            {
                "ISBN:9780000000004": {
                    "title": "Fallback Title",
                    "authors": ["Fall Back"],
                    "published_year": 2021,
                    "publishers": ["FB Press"],
                    "thumbnail_url": "http://example.com/fb.jpg",
                    "info_url": "https://books.google.com/books?id=fb",
                }
            }
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_isbn("9780000000004")
    assert res["title"] == "Fallback Title"
    assert res["authors"] == ["Fall Back"]
    assert res["year"] == 2021
    assert res["publisher"] == "FB Press"
    assert res["isbn"] == "9780000000004"
    assert res["cover_url"] == "https://example.com/fb.jpg"


def test_scraper_base_abstract():
    with pytest.raises(TypeError):
        BaseScraper()  # abstract


BB_SEARCH = {
    "resultCount": 1,
    "searchResult": [
        {
            "bbid": "fb7d0a29-e03a-4d53-81ce-25c2712d4845",
            "defaultAlias": {
                "language": "eng",
                "name": "Dune",
                "primary": True,
                "sortName": "Dune",
            },
            "entityType": "Edition",
        }
    ],
    "totalCount": 1,
}
BB_EDITION = {
    "bbid": "fb7d0a29-e03a-4d53-81ce-25c2712d4845",
    "defaultAlias": {"language": "eng", "name": "Dune", "primary": True},
    "authorCredits": {"authorCount": 1, "names": [{"name": "Frank Herbert"}]},
    "languages": ["eng"],
    "pages": 563,
    "publishers": None,
    "releaseEventDate": "+002005-09",
}
BB_IDENTS = {
    "bbid": "fb7d0a29-e03a-4d53-81ce-25c2712d4845",
    "identifiers": [{"type": "ISBN-13", "value": "978-1-101-94880-4"}],
}


def test_bookbrainz_search_isbn(monkeypatch):
    sc = BookBrainzScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        if "/search" in url:
            return DummyResponse(BB_SEARCH)
        if url.endswith("/identifiers"):
            return DummyResponse(BB_IDENTS)
        return DummyResponse(BB_EDITION)

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_isbn("9781101948804")
    assert res["title"] == "Dune"
    assert res["authors"] == ["Frank Herbert"]
    assert res["year"] == 2005
    assert res["page_count"] == 563
    assert res["isbn"] == "9781101948804"
    assert res["source_urls"] == [
        "https://bookbrainz.org/edition/fb7d0a29-e03a-4d53-81ce-25c2712d4845"
    ]


def test_bookbrainz_search_title_author(monkeypatch):
    sc = BookBrainzScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        if "/search" in url:
            assert params["type"] == "edition"
            return DummyResponse(BB_SEARCH)
        if url.endswith("/identifiers"):
            return DummyResponse(BB_IDENTS)
        return DummyResponse(BB_EDITION)

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_title_author("Dune", ["Frank Herbert"])
    assert res["title"] == "Dune"


def test_bookbrainz_search_miss(monkeypatch):
    sc = BookBrainzScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        return DummyResponse({"resultCount": 0, "searchResult": [], "totalCount": 0})

    monkeypatch.setattr(sc.session, "get", fake_get)
    assert sc.search_isbn("0000000000") is None


ABE_PRODUCT_HTML = """
<html><head>
  <title>Ironwood: A Catalina Novel - Connelly, Michael: 9780316595384 - AbeBooks</title>
  <meta property="og:image" content="//assets.prod.abebookscdn.com/cdn/shared/images/common/logos/abebooks-logo-com.png">
  <meta itemprop="name" content="Ironwood: A Catalina Novel" />
  <meta itemprop="author" content="Connelly, Michael" />
</head><body>
  <h1 class="offer-title">Ironwood: A Catalina Novel - Hardcover</h1>
  <img src="https://pictures.abebooks.com/isbn/9780316595384-us.jpg" />
  <p>ISBN 13: 9780316595384</p>
  <p>ISBN 10: 0316595381</p>
  <dl class="listing-metadata">
    <dt>Publisher</dt><dd>Little, Brown and Company</dd>
    <dt>Publication date</dt><dd>2026</dd>
    <dt>Language</dt><dd>English</dd>
    <dt>Number of pages</dt><dd>336</dd>
  </dl>
  <h2>Synopsis</h2>
  <p>AN INSTANT NEW YORK TIMES BESTSELLER. Detective Sergeant Stilwell knows his posting on Catalina Island is no paradise.</p>
</body></html>
"""

ABE_SEARCH_HTML = """
<html><body>
  <div class="result">
    <a href="/Ironwood-Catalina-Connelly/31812591556/bd">Ironwood: A Catalina Novel</a>
  </div>
</body></html>
"""


def test_abebooks_search_isbn(monkeypatch):
    sc = AbeBooksScraper()

    def fake_get(url, params=None, timeout=15, allow_redirects=True, **kwargs):
        return DummyResponse(
            text=ABE_PRODUCT_HTML, url="https://www.abebooks.com/products/isbn/9780316595384"
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_isbn("9780316595384")
    assert res is not None
    assert res["title"] == "Ironwood: A Catalina Novel"
    assert res["authors"] == ["Michael Connelly"]  # inverted name normalized
    assert res["publisher"] == "Little, Brown and Company"
    assert res["year"] == 2026
    assert res["publish_year"] == 2026
    assert res["page_count"] == 336
    assert res["language"] == "English"
    assert res["isbn"] == "9780316595384"
    assert res["cover_url"].startswith("https://pictures.abebooks.com")
    assert "NEW YORK TIMES BESTSELLER" in res["description"]


def test_abebooks_search_title_author(monkeypatch):
    sc = AbeBooksScraper()
    calls: list[str] = []

    def fake_get(url, params=None, timeout=15, allow_redirects=True, **kwargs):
        calls.append(url)
        if "SearchResults" in url:
            return DummyResponse(text=ABE_SEARCH_HTML, url=url)
        return DummyResponse(
            text=ABE_PRODUCT_HTML,
            url="https://www.abebooks.com/Ironwood-Catalina-Connelly/31812591556/bd",
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_title_author("Ironwood", ["Michael Connelly"])
    assert res is not None
    assert res["title"] == "Ironwood: A Catalina Novel"
    assert res["authors"] == ["Michael Connelly"]
    assert any("SearchResults" in c for c in calls)
    assert any("/31812591556/bd" in c for c in calls)


def test_abebooks_blocked_returns_none(monkeypatch):
    sc = AbeBooksScraper()

    def fake_get(url, params=None, timeout=15, allow_redirects=True, **kwargs):
        return DummyResponse(status_code=403)

    monkeypatch.setattr(sc.session, "get", fake_get)
    assert sc.search_isbn("9780140328721") is None


def test_abebooks_missing_title_returns_none(monkeypatch):
    sc = AbeBooksScraper()

    def fake_get(url, params=None, timeout=15, allow_redirects=True, **kwargs):
        return DummyResponse(text="<html><body><p>Just a CAPTCHA wall.</p></body></html>")

    monkeypatch.setattr(sc.session, "get", fake_get)
    assert sc.search_isbn("9780140328721") is None


# ---- direct URL paste support ----------------------------------------------


def test_match_url_routing():
    ol = OpenLibraryScraper()
    assert ol.match_url("https://openlibrary.org/books/OL123") is True
    assert ol.match_url("http://openlibrary.org/isbn/9780140328721") is True
    assert ol.match_url("https://google.com/x") is False

    gb = GoogleBooksScraper()
    assert gb.match_url("https://books.google.com/books?id=abc") is True
    assert gb.match_url("https://www.googleapis.com/books/v1/volumes/abc") is True

    bb = BookBrainzScraper()
    assert bb.match_url("https://bookbrainz.org/edition/abc") is True

    ab = AbeBooksScraper()
    assert ab.match_url("https://www.abebooks.com/products/isbn/1") is True
    assert ab.match_url("https://abebooks.com/x") is True

    ia = InternetArchiveScraper()
    assert ia.match_url("https://archive.org/details/foo") is True

    loc = LibraryOfCongressScraper()
    assert loc.match_url("https://www.loc.gov/books/x") is True

    # Unsupported domain -> no scraper handles it.
    from simurg.metadata.enricher import supported_url_domains

    assert "example.com" not in supported_url_domains()


def test_openlibrary_search_url_isbn(monkeypatch):
    sc = OpenLibraryScraper()
    isbn = "9780441172719"

    def fake_get(url, params=None, timeout=10, **kwargs):
        if "api/books" in url:
            return DummyResponse(
                {
                    f"ISBN:{isbn}": {
                        "title": "Dune",
                        "authors": [{"name": "Frank Herbert"}],
                        "publishers": [{"name": "Ace"}],
                        "publish_date": "1965",
                        "number_of_pages": 896,
                        "cover": {"large": "https://covers.example.com/dune.jpg"},
                        "url": "https://openlibrary.org/books/OL123",
                    }
                }
            )
        return DummyResponse()

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_url(f"https://openlibrary.org/isbn/{isbn}")
    assert res["title"] == "Dune"
    assert res["isbn"] == isbn


def test_openlibrary_search_url_book_olid(monkeypatch):
    sc = OpenLibraryScraper()
    olid = "OL27448M"

    def fake_get(url, params=None, timeout=10, **kwargs):
        if url.endswith(f"/books/{olid}.json"):
            return DummyResponse(
                {
                    "title": "Dune",
                    "authors": [{"name": "Frank Herbert"}],
                    "publishers": [{"name": "Ace"}],
                    "publish_date": "1965",
                    "isbn_13": ["9780441172719"],
                    "number_of_pages": 896,
                }
            )
        return DummyResponse()

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_url(f"https://openlibrary.org/books/{olid}")
    assert res["title"] == "Dune"
    assert res["isbn"] == "9780441172719"
    assert res["year"] == 1965


def test_openlibrary_search_url_unsupported_path():
    sc = OpenLibraryScraper()
    assert sc.search_url("https://openlibrary.org/author/OL1") is None
    assert sc.search_url("https://example.com/x") is None


def test_googlebooks_search_url(monkeypatch):
    sc = GoogleBooksScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        assert url == "https://www.googleapis.com/books/v1/volumes/myvolid"
        return DummyResponse(
            {
                "volumeInfo": {
                    "title": "Dune",
                    "authors": ["Frank Herbert"],
                    "publisher": "Ace",
                    "publishedDate": "1965",
                    "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780441172719"}],
                }
            }
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_url("https://books.google.com/books?id=myvolid&pg=pa1")
    assert res["title"] == "Dune"
    assert res["isbn"] == "9780441172719"


def test_bookbrainz_search_url_edition(monkeypatch):
    sc = BookBrainzScraper()
    bbid = "fb7d0a29-e03a-4d53-81ce-25c2712d4845"

    def fake_get(url, params=None, timeout=10, **kwargs):
        if "/identifiers" in url:
            return DummyResponse(BB_IDENTS)
        return DummyResponse(BB_EDITION)

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_url(f"https://bookbrainz.org/edition/{bbid}")
    assert res["title"] == "Dune"
    assert res["isbn"] == "9781101948804"


def test_bookbrainz_search_url_work(monkeypatch):
    sc = BookBrainzScraper()
    bbid = "fb7d0a29-e03a-4d53-81ce-25c2712d4845"

    def fake_get(url, params=None, timeout=10, **kwargs):
        if url.endswith("/editions"):
            return DummyResponse({"editions": [{"bbid": bbid}]})
        if "/identifiers" in url:
            return DummyResponse(BB_IDENTS)
        return DummyResponse(BB_EDITION)

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_url(f"https://bookbrainz.org/work/{bbid}")
    assert res["title"] == "Dune"


def test_abebooks_search_url_product_page(monkeypatch):
    sc = AbeBooksScraper()

    def fake_get(url, params=None, timeout=15, allow_redirects=True, **kwargs):
        return DummyResponse(
            text=ABE_PRODUCT_HTML, url="https://www.abebooks.com/products/isbn/9780316595384"
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_url("https://www.abebooks.com/products/isbn/9780316595384")
    assert res is not None
    assert res["title"] == "Ironwood: A Catalina Novel"
    assert res["isbn"] == "9780316595384"


def test_internetarchive_search_url(monkeypatch):
    from simurg.metadata.scrapers.internetarchive import InternetArchiveScraper

    sc = InternetArchiveScraper()
    ident = "some-magazine-1923"

    def fake_get(url, params=None, timeout=10, **kwargs):
        return DummyResponse(
            {
                "metadata": {
                    "title": "Wireless Age",
                    "date": "1923-05",
                    "publisher": "Wireless Press",
                    "number_of_pages": "80",
                }
            }
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_url(f"https://archive.org/details/{ident}")
    assert res["title"] == "Wireless Age"
    assert res["year"] == 1923
    assert res["publisher"] == "Wireless Press"
    # archive.org returns page counts as strings, matching search_magazine.
    assert res["page_count"] == "80"
    assert res["cover_url"].endswith(ident)


def test_libraryofcongress_search_url(monkeypatch):
    from simurg.metadata.scrapers.libraryofcongress import LibraryOfCongressScraper

    sc = LibraryOfCongressScraper()

    def fake_get(url, params=None, timeout=10, **kwargs):
        assert url.endswith("?fo=json")
        return DummyResponse(
            {
                "title": "The Dial",
                "publisher": ["J. M. Bowles"],
                "publish_date": ["1920"],
                "id": "https://www.loc.gov/item/12345/",
            }
        )

    monkeypatch.setattr(sc.session, "get", fake_get)
    res = sc.search_url("https://www.loc.gov/item/12345/")
    assert res["title"] == "The Dial"
    assert res["year"] == 1920
    assert res["publisher"] == "J. M. Bowles"
