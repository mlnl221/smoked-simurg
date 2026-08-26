"""Tests for magazine support: decode, build, payload, enricher routing, scrapers."""

from __future__ import annotations

from pathlib import Path

from simurg.metadata.enricher import (
    CrossrefScraper as _EnrCrossref,
)
from simurg.metadata.enricher import (
    InternetArchiveScraper as _EnrIA,
)
from simurg.metadata.enricher import (
    IssnPortalScraper as _EnrIssn,
)
from simurg.metadata.enricher import (
    LibraryOfCongressScraper as _EnrLOC,
)
from simurg.metadata.enricher import (
    OpenLibraryScraper as _EnrOL,
)
from simurg.metadata.enricher import (
    _all_scrapers,
    _scrapers_for,
    search_magazine_scrapers,
)
from simurg.metadata.magazine import (
    build_magazine_metadata,
    decode_magazine_filename,
    magazine_issue_label,
    validate_magazine_metadata,
)
from simurg.metadata.scrapers.crossref import CrossrefScraper
from simurg.metadata.scrapers.internetarchive import InternetArchiveScraper
from simurg.metadata.scrapers.issnportal import IssnPortalScraper
from simurg.metadata.scrapers.libraryofcongress import LibraryOfCongressScraper
from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper
from simurg.metadata.scrapers.util import clean_issn, issue_label, normalize_issue_date
from simurg.uploader.payload import compile_data_existing_magazine, compile_data_new_magazine

# --- Fake network ---


class FakeResponse:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    def __init__(self, by_substr=None, default=None):
        self.headers = {}
        self.by_substr = by_substr or {}
        self.default = default or FakeResponse(200, {})
        self.last = None

    def get(self, url, params=None, timeout=None):
        self.last = (url, params)
        for key, resp in self.by_substr.items():
            if key in url:
                return resp
            if params:
                for v in params.values():
                    if isinstance(v, str) and key in v:
                        return resp
        return self.default


# --- util helpers ---


def test_clean_issn():
    assert clean_issn("0027-9358") == "0027-9358"
    assert clean_issn("ISSN 0027-9358") == "0027-9358"
    assert clean_issn("not an issn") is None
    assert clean_issn("1234-567X") == "1234-567X"


def test_normalize_issue_date():
    assert normalize_issue_date("2020-06") == ("2020-06", "month")
    assert normalize_issue_date("2020-06-15") == ("2020-06-15", "day")
    assert normalize_issue_date("June 2020") == ("2020-06", "month")
    assert normalize_issue_date("June 15, 2020") == ("2020-06-15", "day")
    assert normalize_issue_date("2020") == ("2020", "year")
    assert normalize_issue_date("") == (None, None)


def test_issue_label():
    assert issue_label("2020-06", "month") == "June 2020"
    assert issue_label("2020-06-15", "day") == "June 15, 2020"
    assert issue_label("2020", "year") == "2020"
    assert (
        issue_label("2020-06", "month", volume="12", issue_number="3") == "June 2020 Vol 12 Issue 3"
    )
    assert issue_label(None, None, volume="12") == "Vol 12"


# --- filename decode ---


def test_decode_magazine_month_year():
    md = decode_magazine_filename(Path("National Geographic - June 2020.pdf"))
    assert md["canonical_title"] == "National Geographic"
    assert md["issue_date"] == "2020-06"
    assert md["issue_date_precision"] == "month"
    assert md["year"] == 2020
    assert md["format"] == "PDF"


def test_decode_magazine_vol_issue():
    md = decode_magazine_filename(Path("Playboy - Vol 12 Issue 3.cbz"))
    assert md["canonical_title"] == "Playboy"
    assert md["volume"] == "12"
    assert md["issue_number"] == "3"
    assert md["format"] == "CBZ"


def test_decode_magazine_iso_date():
    md = decode_magazine_filename(Path("National Geographic - 2020-06.pdf"))
    assert md["issue_date"] == "2020-06"
    assert md["issue_date_precision"] == "month"


def test_decode_magazine_year_only():
    md = decode_magazine_filename(Path("Hustler - 2005.pdf"))
    assert md["canonical_title"] == "Hustler"
    assert md["issue_date"] == "2005"
    assert md["issue_date_precision"] == "year"
    assert md["year"] == 2005


def test_decode_magazine_no_dash_yyyymm():
    """Filename without dash separator: 'Penthouse 2002-02' → title=Penthouse, issue=2002-02."""
    md = decode_magazine_filename(Path("Penthouse 2002-02.pdf"))
    assert md["canonical_title"] == "Penthouse"
    assert md["issue_date"] == "2002-02"
    assert md["issue_date_precision"] == "month"
    assert md["year"] == 2002
    assert md["format"] == "PDF"


def test_decode_magazine_no_dash_month_year():
    """Filename without dash: 'Penthouse February 2002' → title=Penthouse, issue=2002-02."""
    md = decode_magazine_filename(Path("Penthouse February 2002.pdf"))
    assert md["canonical_title"] == "Penthouse"
    assert md["issue_date"] == "2002-02"
    assert md["issue_date_precision"] == "month"
    assert md["year"] == 2002


def test_decode_magazine_no_dash_year_only():
    """Filename without dash: 'Hustler 2005' → title=Hustler, issue=2005."""
    md = decode_magazine_filename(Path("Hustler 2005.pdf"))
    assert md["canonical_title"] == "Hustler"
    assert md["issue_date"] == "2005"
    assert md["issue_date_precision"] == "year"
    assert md["year"] == 2005


# --- build + validate ---


def test_build_magazine_metadata():
    inbuilt = {
        "canonical_title": "National Geographic",
        "title": "National Geographic",
        "format": "PDF",
        "type": "Magazines",
        "issue_date": "2020-06",
        "issue_date_precision": "month",
        "year": 2020,
    }
    scraper = {
        "title": "National Geographic",
        "first_published": 1888,
        "print_issn": "0027-9358",
        "electronic_issn": "1936-6618",
        "publisher": "National Geographic Society",
        "country": "United States",
        "frequency": "Monthly",
        "cover_url": "http://x/cover.jpg",
        "source_urls": ["http://x"],
    }
    md = build_magazine_metadata(inbuilt, scraper, "PDF")
    assert md["title"] == "National Geographic"
    assert md["release_title"] == "National Geographic - June 2020"
    assert md["print_issn"] == "0027-9358"
    assert md["electronic_issn"] == "1936-6618"
    assert md["publisher"] == "National Geographic Society"
    assert md["country"] == "United States"
    assert md["frequency"] == "Monthly"
    assert md["tags"] == "magazine"
    assert "authors" not in md


def test_validate_magazine_metadata():
    ok = {
        "title": "X",
        "year": 2020,
        "issue_date": "2020-06",
        "format": "PDF",
        "source": "Retail",
    }
    assert validate_magazine_metadata(ok) == []
    missing = validate_magazine_metadata(
        {"title": "X", "year": 2020, "format": "PDF", "source": "Retail"}
    )
    assert "issue_identity" in missing


def test_magazine_issue_label_helper():
    md = {
        "issue_date": "2020-06",
        "issue_date_precision": "month",
        "volume": "1",
        "issue_number": "6",
    }
    assert magazine_issue_label(md) == "June 2020 Vol 1 Issue 6"


# --- payload ---


def test_compile_new_magazine_payload():
    md = {
        "title": "National Geographic",
        "canonical_title": "National Geographic",
        "release_title": "National Geographic - June 2020",
        "year": 2020,
        "original_year": 1888,
        "issue_date": "2020-06",
        "issue_date_precision": "month",
        "volume": "",
        "issue_number": "",
        "release_type": "Individual Issue",
        "publisher": "National Geographic Society",
        "print_issn": "0027-9358",
        "electronic_issn": "1936-6618",
        "country": "United States",
        "frequency": "Monthly",
        "language": "English",
        "page_count": 120,
        "format": "PDF",
        "source": "Retail",
        "tags": "magazine",
        "book_desc": "desc",
        "type": "Magazines",
    }
    data = compile_data_new_magazine(md, "http://cover/x.jpg")
    assert data["type"] == "7"
    # original_year is the periodical's first-publication year, NOT the issue year
    assert data["original_year"] == "1888"
    assert data["year"] == "2020"
    assert data["book_title"] == "National Geographic"
    assert data["magazine_print_issn"] == "0027-9358"
    assert data["magazine_electronic_issn"] == "1936-6618"
    assert data["magazine_publisher"] == "National Geographic Society"
    assert data["magazine_country"] == "United States"
    assert data["magazine_frequency"] == "Monthly"
    assert data["magazine_release_type"] == "Individual Issue"
    assert data["magazine_issue_date"] == "2020-06"
    assert data["magazine_issue_date_precision"] == "month"
    assert data["title"] == "National Geographic - June 2020"
    assert data["bitrate"] == "Retail"
    # Magazines must NOT send ebook-style fields
    assert "catalogue_number" not in data
    assert "record_label" not in data
    assert "artists[]" not in data
    assert "groupid" not in data


def test_build_magazine_splits_original_year_from_issue_year():
    inbuilt = {
        "canonical_title": "National Geographic",
        "title": "National Geographic",
        "year": 2020,
        "issue_date": "2020-06",
        "issue_date_precision": "month",
    }
    scraper = {"first_published": 1888, "publisher": "National Geographic Society"}
    md = build_magazine_metadata(inbuilt, scraper, "PDF", "National Geographic - June 2020.pdf")
    assert md["year"] == 2020  # issue/release year
    assert md["original_year"] == 1888  # periodical first-published year
    assert md["publisher"] == "National Geographic Society"


def test_build_magazine_unknown_publisher_blank():
    md = build_magazine_metadata(
        {"canonical_title": "X", "title": "X", "year": 2020}, {}, "PDF", "X.pdf"
    )
    assert md["publisher"] == ""


def test_compile_existing_magazine_payload():
    md = {
        "release_title": "National Geographic - June 2020",
        "year": 2020,
        "issue_date": "2020-06",
        "issue_date_precision": "month",
        "volume": "",
        "issue_number": "",
        "release_type": "Individual Issue",
        "format": "PDF",
        "source": "Retail",
        "tags": "magazine",
        "publisher": "National Geographic Society",
        "print_issn": "0027-9358",
        "electronic_issn": "1936-6618",
        "country": "United States",
        "frequency": "Monthly",
        "page_count": 120,
        "language": "English",
        "book_desc": "",
        "type": "Magazines",
    }
    data = compile_data_existing_magazine(12862, md, None)
    assert data["publicationid"] == "12862"
    assert "groupid" not in data
    assert data["magazine_release_type"] == "Individual Issue"
    assert data["magazine_issue_date"] == "2020-06"


# --- enricher routing ---


def test_scraper_categories():
    session = FakeSession()
    for sc in _all_scrapers(session):
        assert isinstance(sc.categories, set) and sc.categories


def test_ebook_path_excludes_magazine_only_scrapers():
    session = FakeSession()
    ebook = _scrapers_for(session, {"ebook"})
    names = {s.name for s in ebook}
    assert "issnportal" not in names
    assert "internetarchive" not in names
    assert "openlibrary" in names  # shared


def test_magazine_path_only_magazine_scrapers():
    session = FakeSession()
    mag = _scrapers_for(session, {"magazine"})
    names = {s.name for s in mag}
    assert "issnportal" in names
    assert "crossref" in names
    assert "openlibrary" in names  # shared
    assert "googlebooks" not in names


def _cap(s):
    return s[0].upper() + s[1:]


def test_search_magazine_scrapers_invokes_only_magazine():
    from simurg.metadata.enricher import LibraryThingScraper as _EnrLT
    from simurg.metadata.scrapers.wonderclub import WonderClubScraper as _EnrWC

    classes = {
        "issnportal": _EnrIssn,
        "internetarchive": _EnrIA,
        "libraryofcongress": _EnrLOC,
        "crossref": _EnrCrossref,
        "openlibrary": _EnrOL,
        "librarything": _EnrLT,
        "wonderclub": _EnrWC,
    }
    patched = {
        "issnportal": {"title": "Issn"},
        "internetarchive": {"title": "IA"},
        "libraryofcongress": {"title": "LOC"},
        "crossref": {"title": "CR"},
        "openlibrary": {"title": "OL"},
        "librarything": {"title": "LT"},
        "wonderclub": {"title": "WC"},
    }
    originals = {}
    for name, cls in classes.items():
        originals[name] = cls.search_magazine
        cls.search_magazine = lambda self, t, i=None, _v=patched[name]: dict(_v)
    try:
        results = search_magazine_scrapers({"canonical_title": "National Geographic", "year": 2020})
        got = {r["_scraper"] for r in results}
        assert got == {
            "issnportal",
            "internetarchive",
            "libraryofcongress",
            "crossref",
            "openlibrary",
            "librarything",
            "wonderclub",
        }
    finally:
        for name, fn in originals.items():
            classes[name].search_magazine = fn


# --- individual scraper unit tests (mocked) ---


def test_issnportal_scraper():
    sess = FakeSession(
        by_substr={
            "portal.issn.org": FakeResponse(
                200,
                {
                    "data": [
                        {
                            "title": "National Geographic",
                            "pissn": "0027-9358",
                            "eissn": "1936-6618",
                            "publisher": "National Geographic Society",
                            "country": "United States",
                            "frequency": "Monthly",
                            "startYear": 1888,
                            "@id": "http://x/1",
                        }
                    ]
                },
            ),
        }
    )
    sc = IssnPortalScraper(sess)
    res = sc.search_magazine("National Geographic")
    assert res["title"] == "National Geographic"
    assert res["print_issn"] == "0027-9358"
    assert res["electronic_issn"] == "1936-6618"
    assert res["first_published"] == 1888
    assert res["frequency"] == "Monthly"


def test_internetarchive_scraper():
    sess = FakeSession(
        by_substr={
            "archive.org": FakeResponse(
                200,
                {
                    "response": {
                        "docs": [
                            {
                                "identifier": "natgeo2020",
                                "title": "National Geographic",
                                "date": "2020-06",
                                "volume": "1",
                                "issue": "6",
                                "publisher": "NatGeo",
                                "number_of_pages": 120,
                                "language": "English",
                            }
                        ]
                    }
                },
            ),
        }
    )
    sc = InternetArchiveScraper(sess)
    res = sc.search_magazine("National Geographic", {"issue_date": "2020-06"})
    assert res["issue_date"] == "2020-06"
    assert res["issue_date_precision"] == "month"
    assert res["volume"] == "1"
    assert res["issue_number"] == "6"
    assert res["cover_url"] == "https://archive.org/services/img/natgeo2020"
    assert res["source_urls"][0] == "https://archive.org/details/natgeo2020"


def test_libraryofcongress_scraper():
    sess = FakeSession(
        by_substr={
            "loc.gov": FakeResponse(
                200,
                {
                    "results": [
                        {
                            "title": "National Geographic",
                            "publisher": ["National Geographic Society"],
                            "publish_date": ["1888"],
                            "id": "http://loc/1",
                        }
                    ]
                },
            ),
        }
    )
    sc = LibraryOfCongressScraper(sess)
    res = sc.search_magazine("National Geographic")
    assert res["title"] == "National Geographic"
    assert res["first_published"] == 1888
    assert res["publisher"] == "National Geographic Society"


def test_crossref_scraper():
    sess = FakeSession(
        by_substr={
            "api.crossref.org": FakeResponse(
                200,
                {
                    "message": {
                        "items": [
                            {
                                "title": ["National Geographic"],
                                "ISSN": ["0027-9358", "1936-6618"],
                                "publisher": "NatGeo",
                                "issued": {"date-parts": [[1888]]},
                                "URL": "http://x",
                            }
                        ]
                    }
                },
            ),
        }
    )
    sc = CrossrefScraper(sess)
    res = sc.search_magazine("National Geographic")
    assert res["print_issn"] == "0027-9358"
    assert res["electronic_issn"] == "1936-6618"
    assert res["first_published"] == 1888


def test_openlibrary_search_magazine():
    sess = FakeSession(
        by_substr={
            "openlibrary.org": FakeResponse(
                200,
                {
                    "docs": [
                        {
                            "title": "National Geographic",
                            "first_publish_year": 1888,
                            "publisher": ["NatGeo"],
                            "language": ["English"],
                            "cover_i": 1,
                            "key": "/works/OL1",
                            "isbn": ["1234567890"],
                        }
                    ]
                },
            ),
        }
    )
    sc = OpenLibraryScraper(sess)
    assert sc.categories == {"ebook", "magazine"}
    res = sc.search_magazine("National Geographic")
    assert res["title"] == "National Geographic"
    assert res["first_published"] == 1888
    assert res["language"] == "English"


# --- LibraryThing scraper (mocked) ---

LT_TALPA_RESPONSE = {
    "response": {
        "resultlist": [
            {
                "rank": 1,
                "title": "Penthouse Magazine | February 2002",
                "work_id": 27277265,
                "score": 3000,
            }
        ]
    }
}

LT_CK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response stat="ok"><ltml xmlns="http://www.librarything.com/" version="1.1">
<item id="27277265" type="work">
  <author id="970927" authorcode="penthouse">Penthouse</author>
  <title>Penthouse Magazine | February 2002</title>
  <commonknowledge><fieldList>
    <field name="originalpublicationdate"><versionList><version>
      <factList><fact>2002-02</fact></factList>
    </version></versionList></field>
  </fieldList></commonknowledge>
</item></ltml></response>"""


def test_librarything_search_magazine():
    from simurg.metadata.scrapers.librarything import LibraryThingScraper

    sess = FakeSession(
        by_substr={
            "talpa.php": FakeResponse(200, LT_TALPA_RESPONSE),
            "ck.getwork": FakeResponse(200, text=LT_CK_XML),
        }
    )
    sc = LibraryThingScraper(sess)
    sc._token = "fake_token"
    res = sc.search_magazine("Penthouse", {"issue_date": "2002-02"})
    assert res is not None
    assert res["title"] == "Penthouse Magazine"
    assert res["issue_date"] == "2002-02"
    assert res["first_published"] == 2002
    assert res["source_urls"][0] == "https://www.librarything.com/work/27277265"


def test_librarything_search_url():
    from simurg.metadata.scrapers.librarything import LibraryThingScraper

    sess = FakeSession(
        by_substr={
            "ck.getwork": FakeResponse(200, text=LT_CK_XML),
        }
    )
    sc = LibraryThingScraper(sess)
    sc._token = "fake_token"
    res = sc.search_url("https://www.librarything.com/work/27277265/t/Penthouse-Magazine")
    assert res is not None
    assert res["title"] == "Penthouse Magazine"
    assert res["issue_date"] == "2002-02"


def test_librarything_categories():
    from simurg.metadata.scrapers.librarything import LibraryThingScraper

    sc = LibraryThingScraper()
    assert sc.categories == {"ebook", "magazine"}


# --- WonderClub scraper (mocked) ---

WONDER_DETAIL_HTML = """
<html><head>
<title>OLD NEW WORLD - WonderClub</title>
<meta property="og:title" content="OLD NEW WORLD" />
<meta property="og:image" content="https://wonderclub.com/images/old-new-world.jpg" />
<meta property="og:url" content="https://wonderclub.com/books/old-new-world" />
<script type="application/ld+json">
{"@type": "Book", "name": "OLD NEW WORLD", "author": "Aldous Huxley", "isbn": "9780060850524", "publisher": "Harper Perennial"}
</script>
</head><body>
<a data-toggle="tab" href="#menu1">Details</a>
<div id="menu1">
<table>
<tr><td>Title</td><td>OLD NEW WORLD</td></tr>
<tr><td>Manufacturer</td><td>Harper Perennial</td></tr>
<tr><td>Publication Year</td><td>2000</td></tr>
<tr><td>ISBN-10</td><td>0002570939</td></tr>
<tr><td>Category</td><td><a href="/category/arts">Media &gt;&gt; Books &gt;&gt; Arts &amp; Photography</a></td></tr>
<tr><td>Image Location</td><td>/images/old-new-world.jpg</td></tr>
<tr><td>Binding</td><td>Hardcover</td></tr>
</table>
</div>
</body></html>
"""

WONDER_SEARCH_HTML = """
<html><body>
<div class="result">
  <a href="/books/old-new-world">OLD NEW WORLD</a>
  <span>Aldous Huxley</span>
</div>
<div class="result">
  <a href="/books/other-book">OTHER BOOK</a>
</div>
</body></html>
"""


def test_wonderclub_search_url():
    from simurg.metadata.scrapers.wonderclub import WonderClubScraper

    sess = FakeSession(
        by_substr={
            "wonderclub.com/books/old-new-world": FakeResponse(200, text=WONDER_DETAIL_HTML),
        }
    )
    sc = WonderClubScraper(sess)
    res = sc.search_url("https://wonderclub.com/books/old-new-world")
    assert res is not None
    assert res["title"] == "OLD NEW WORLD"
    assert res["isbn"] == "0002570939"
    assert res["year"] == 2000
    assert res["publisher"] == "Harper Perennial"
    assert res["details"]["ISBN-10"] == "0002570939"
    assert res["details"]["Binding"] == "Hardcover"
    assert res["details"]["Category"] == "Media >> Books >> Arts & Photography"
    assert res["source_urls"][0] == "https://wonderclub.com/books/old-new-world"


def test_wonderclub_search_isbn():
    from simurg.metadata.scrapers.wonderclub import WonderClubScraper

    sess = FakeSession(
        by_substr={
            "search_results.php": FakeResponse(200, text=WONDER_SEARCH_HTML),
            "wonderclub.com/books/old-new-world": FakeResponse(200, text=WONDER_DETAIL_HTML),
        }
    )
    sc = WonderClubScraper(sess)
    res = sc.search_isbn("0002570939")
    assert res is not None
    assert res["title"] == "OLD NEW WORLD"
    assert res["isbn"] == "0002570939"


def test_wonderclub_search_magazine_year_only():
    from simurg.metadata.scrapers.wonderclub import WonderClubScraper

    sess = FakeSession(
        by_substr={
            "search_results.php": FakeResponse(200, text=WONDER_SEARCH_HTML),
            "wonderclub.com/books/old-new-world": FakeResponse(200, text=WONDER_DETAIL_HTML),
        }
    )
    sc = WonderClubScraper(sess)
    res = sc.search_magazine("OLD NEW WORLD", {"issue_date": "2000"})
    assert res is not None
    assert res["issue_date"] == "2000"
    assert res["issue_date_precision"] == "year"
    assert res["first_published"] == 2000


def test_wonderclub_categories():
    from simurg.metadata.scrapers.wonderclub import WonderClubScraper

    sc = WonderClubScraper()
    assert sc.categories == {"ebook", "magazine"}


def test_wonderclub_extract_details_dynamic():
    from bs4 import BeautifulSoup

    from simurg.metadata.scrapers.wonderclub import _extract_details

    soup = BeautifulSoup(WONDER_DETAIL_HTML, "html.parser")
    details = _extract_details(soup)
    assert details["Title"] == "OLD NEW WORLD"
    assert details["Manufacturer"] == "Harper Perennial"
    assert details["Publication Year"] == "2000"
    assert details["ISBN-10"] == "0002570939"
    assert details["Binding"] == "Hardcover"
    # Future field auto-captured
    assert "Category" in details


def test_wonderclub_missing_panel():
    from bs4 import BeautifulSoup

    from simurg.metadata.scrapers.wonderclub import _extract_details

    soup = BeautifulSoup("<html><body>No details here</body></html>", "html.parser")
    assert _extract_details(soup) == {}
