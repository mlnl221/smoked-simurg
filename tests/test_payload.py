import types

from simurg.trackers.base import BaseGazelleApi
from simurg.uploader.payload import (
    build_release_desc,
    compile_data_existing_publication,
    compile_data_new_publication,
)


def test_compile_new_publication_basic():
    md = {
        "title": "Dune",
        "authors": ["Frank Herbert"],
        "year": 1965,
        "remaster_title": "Dune",
        "remaster_year": 2020,
        "tags": "fantasy, sci.fi",
        "language": "English",
        "publisher": "Ace",
        "isbn": "9780441172719",
        "page_count": 896,
        "album_desc": "desc",
        "release_notes": "",
        "format": "EPUB",
        "source": "Retail",
        "release_desc": "rd",
        "type": "E-Book",
    }
    data = compile_data_new_publication(md, "https://example.com/cover.jpg")
    assert data["book_title"] == "Dune"
    assert data["original_year"] == "1965"
    assert data["title"] == "Dune"
    assert data["year"] == "2020"
    assert data["artists[]"] == ["Frank Herbert"]
    assert data["importance[]"] == ["1"]
    assert data["image"] == "https://example.com/cover.jpg"
    assert data["format"] == "EPUB"
    assert data["bitrate"] == "Retail"
    assert data["type"] == "2"
    assert data["page_count"] == "896"
    assert data["record_label"] == "Ace"
    assert data["catalogue_number"] == "9780441172719"


def test_compile_new_publication_request_id():
    md = {"title": "T", "authors": ["A"], "year": 2020, "format": "PDF", "source": "Scan"}
    data = compile_data_new_publication(md, None, request_id=123)
    assert data["requestid"] == "123"
    data2 = compile_data_new_publication(md, None)
    assert "requestid" not in data2


def test_compile_new_translators():
    md = {
        "title": "T",
        "authors": ["A"],
        "year": 2020,
        "format": "EPUB",
        "source": "Retail",
        "translators": ["Trans"],
    }
    data = compile_data_new_publication(md, None)
    # translators are merged into artists[] with importance 3
    assert "Trans" in data["artists[]"]
    assert data["importance[]"][data["artists[]"].index("Trans")] == "3"


def test_compile_existing_publication():
    md = {
        "remaster_title": "Dune - Illustrated",
        "remaster_year": 2021,
        "format": "PDF",
        "source": "OCR",
        "release_desc": "desc",
    }
    data = compile_data_existing_publication(12345, md, "https://img")
    assert data["publicationid"] == "12345"
    # Regression: the legacy `groupid` alias must NOT be sent in the POST body.
    # Simurg resolves `groupid` as a torrent group id and rejects it with
    # "The selected torrent group does not exist."
    assert "groupid" not in data
    assert data["title"] == "Dune - Illustrated"
    assert data["year"] == "2021"
    assert data["format"] == "PDF"
    assert data["bitrate"] == "OCR"
    assert data["media"] == "OCR"


# --- Publication id validation (regression for group-id upload bug) ---

VALID_PREFILL = """
<input type="hidden" id="book_work_id" name="publicationid" value="12862" />
<input type="text" id="book_title" name="book_title" value="The Woods" />
"""

INVALID_PREFILL = """
<input type="hidden" id="book_work_id" name="publicationid" value="" />
<input type="text" id="book_title" name="book_title" value="" />
"""


def test_parse_publication_prefill_valid():
    ok, title = BaseGazelleApi._parse_publication_prefill(VALID_PREFILL, "12862")
    assert ok is True
    assert title == "The Woods"


def test_parse_publication_prefill_valid_mismatch_id():
    # id resolves to a different publication -> not a match for the requested id
    ok, _ = BaseGazelleApi._parse_publication_prefill(VALID_PREFILL, "999999")
    assert ok is False


def test_parse_publication_prefill_invalid():
    ok, _ = BaseGazelleApi._parse_publication_prefill(INVALID_PREFILL, "999999")
    assert ok is False


def test_validate_publication_id_wires_session():
    site = BaseGazelleApi.__new__(BaseGazelleApi)
    site.base_url = "https://simurg.world"

    class FakeResp:
        def __init__(self, text):
            self.text = text

    calls = {}

    def fake_get(url, params=None, timeout=None):
        calls["url"] = url
        calls["params"] = params
        return FakeResp(VALID_PREFILL)

    import types

    site.session = types.SimpleNamespace(get=fake_get)

    ok, title = site.validate_publication_id("12862")
    assert ok is True
    assert title == "The Woods"
    assert calls["url"] == "https://simurg.world/upload.php"
    assert calls["params"] == {"publicationid": "12862"}


def test_validate_publication_id_rejects_unknown():
    site = BaseGazelleApi.__new__(BaseGazelleApi)
    site.base_url = "https://simurg.world"

    class FakeResp:
        text = INVALID_PREFILL

    site.session = types.SimpleNamespace(get=lambda *a, **k: FakeResp())
    ok, _ = site.validate_publication_id("999999")
    assert ok is False


def test_build_release_desc_basic():
    md = {
        "source": "Retail",
        "format": "EPUB",
        "publisher": "Penguin",
        "isbn": "123",
        "language": "English",
        "page_count": 300,
        "source_urls": ["https://openlibrary.org/1"],
    }
    desc = build_release_desc(md)
    assert "[b]Source:[/b] Retail" in desc
    assert "[b]Format:[/b] EPUB" in desc
    assert "[b]Page count:[/b] 300" in desc
    assert "[b]Publisher:[/b] Penguin" in desc
    assert "[b]ISBN:[/b] 123" in desc
    assert "openlibrary.org" in desc
    assert "Uploaded with smoked-simurg" in desc


def test_build_release_desc_with_extra_urls():
    md = {"source": "Scan", "format": "PDF", "source_urls": ["https://a.com"]}
    desc = build_release_desc(md, source_urls=["https://b.com"])
    assert "https://a.com" in desc
    assert "https://b.com" in desc


def test_tags_list_joined_to_single_string():
    md = {"title": "T", "tags": ["fiction", "american", "west"]}
    data = compile_data_new_publication(md, None)
    assert data["tags"] == "fiction, american, west"
    data2 = compile_data_existing_publication(1, md, None)
    assert data2["tags"] == "fiction, american, west"


def test_tags_string_passthrough_and_empty():
    assert compile_data_new_publication({"title": "T"}, None)["tags"] == ""
    md = {"title": "T", "tags": "fantasy, sci.fi"}
    assert compile_data_new_publication(md, None)["tags"] == "fantasy, sci.fi"
    md = {"title": "T", "tags": ["fiction", " ", "west"]}
    assert compile_data_new_publication(md, None)["tags"] == "fiction, west"
