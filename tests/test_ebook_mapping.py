"""Validate ebook path vs docs/ebook.txt field mapping.

See docs/ebook.txt §4-§12, §5 Tags, §7 Language/Publisher/ISBN/Page,
§3 Contributors, §12 Publication vs Release decision rule.

Four critical fields:
  Publication = work: canonical title (book_title), First Published (original_year)
  Release    = edition: release title (title), Release year (year)
"""

from simurg.metadata.combine import (
    build_metadata,
    clean_tags,
    detect_edition,
    strip_edition_from_canonical,
)
from simurg.uploader.payload import compile_data_new_publication


def test_canonical_strips_edition_variants():
    # docs/ebook.txt:749 — canonical must not include edition labels
    assert strip_edition_from_canonical("Dune (Illustrated Edition)") == "Dune"
    assert strip_edition_from_canonical("Dune: 50th Anniversary Edition") == "Dune"
    assert strip_edition_from_canonical("Dune - Revised Edition") == "Dune"
    assert strip_edition_from_canonical("Dune [Special Edition]") == "Dune"
    assert strip_edition_from_canonical("Dune") == "Dune"


def test_detect_edition_covers_colon_and_bracket():
    assert detect_edition("Dune (Illustrated Edition)") == "Illustrated Edition"
    assert detect_edition("Dune: 50th Anniversary Edition") == "50th Anniversary Edition"
    assert detect_edition("Dune - Revised Edition") == "Revised Edition"
    assert detect_edition("My Book Vol. 3") == "Vol. 3"
    assert detect_edition("Clean Title") is None


def test_build_metadata_four_fields_split():
    # docs/ebook.txt:985 — Original 1965, edition 2021
    inbuilt = {"title": "Dune", "year": 2021, "authors": ["Frank Herbert"]}
    scraper = {
        "title": "Dune",
        "authors": ["Frank Herbert"],
        "first_publish_year": 1965,
        "year": 2021,
        "publish_year": 2021,
        "publisher": "Ace",
    }
    md = build_metadata(inbuilt, scraper, "epub")
    assert md["title"] == "Dune"  # canonical
    assert md["year"] == 1965  # First Published = work year
    assert md["remaster_year"] == 2021  # Release year = edition year
    assert md["remaster_title"] == "Dune"


def test_build_metadata_edition_preserved_in_release_title():
    inbuilt = {"title": "Dune", "edition": "Illustrated Edition", "authors": ["A"]}
    scraper = {"title": "Dune", "first_publish_year": 1965, "year": 2021}
    md = build_metadata(inbuilt, scraper, "epub")
    assert md["title"] == "Dune"
    assert "Illustrated Edition" in md["remaster_title"]
    assert md["remaster_title"] != md["title"]


def test_build_metadata_colon_edition_split():
    # Dune: 50th Anniversary Edition -> canonical Dune, release with edition
    inbuilt = {"title": "Dune: 50th Anniversary Edition", "authors": ["A"]}
    scraper = {"title": "Dune", "first_publish_year": 1965, "year": 2021}
    md = build_metadata(inbuilt, scraper, "epub")
    assert md["title"] == "Dune"
    assert "50th Anniversary Edition" in md["remaster_title"]


def test_first_published_backfills_from_edition_year():
    # Missing work year backfills from edition year so validate passes.
    inbuilt = {"year": 2021, "authors": ["A"], "title": "T"}
    scraper = {"year": 2021, "publish_year": 2021}  # no first_publish_year
    md = build_metadata(inbuilt, scraper, "epub")
    assert md["year"] == 2021
    assert md["remaster_year"] == 2021


def test_first_published_from_scraper_work_year_only():
    inbuilt = {"year": 2021, "authors": ["A"], "title": "T"}
    scraper = {"first_publish_year": 1965, "year": 2021, "publish_year": 2021}
    md = build_metadata(inbuilt, scraper, "epub")
    assert md["year"] == 1965
    assert md["remaster_year"] == 2021


def test_language_from_file_not_hardcoded():
    # docs/ebook.txt:351 — language from file, not hardcoded English
    md = build_metadata({"language": "fr", "first_publish_year": 2000}, {}, "epub")
    assert md["language"] == "Fr" or md["language"] == "French" or md["language"] != "English"
    # explicit en -> English
    md2 = build_metadata({"language": "en"}, {}, "epub")
    assert md2["language"] == "English"
    # scraper fallback
    md3 = build_metadata({}, {"language": "tr", "first_publish_year": 2000}, "epub")
    assert md3["language"] == "Turkish"
    # none -> default English
    md4 = build_metadata({}, {"first_publish_year": 2000}, "epub")
    assert md4["language"] == "English"


def test_publisher_file_wins_over_scraper():
    # docs/ebook.txt:372 — use actual edition publisher (file)
    inbuilt = {"publisher": "File Pub", "first_publish_year": 2000}
    scraper = {"publisher": "Scraper Pub", "first_publish_year": 2000}
    md = build_metadata(inbuilt, scraper, "epub")
    assert md["publisher"] == "File Pub"
    # fallback to scraper when file missing
    md2 = build_metadata({}, {"publisher": "Scraper Pub", "first_publish_year": 2000}, "epub")
    assert md2["publisher"] == "Scraper Pub"


def test_isbn_edition_specific_prefers_file():
    # docs/ebook.txt:391 — ISBN is edition-specific, prefer file
    inbuilt = {"isbn": "9780441172719", "first_publish_year": 2000}
    scraper = {"isbn": "9780000000000", "first_publish_year": 2000}
    md = build_metadata(inbuilt, scraper, "epub")
    assert md["isbn"] == "9780441172719"


def test_page_count_pdf_exact_vs_epub_estimate():
    # docs/ebook.txt:405 — Release-level, prefer exact
    # PDF: file wins (exact len pages)
    md_pdf = build_metadata(
        {"page_count": 100}, {"page_count": 300, "first_publish_year": 2000}, "pdf"
    )
    assert md_pdf["page_count"] == 100
    # EPUB: scraper print count wins over estimate
    md_epub = build_metadata(
        {"page_count": 100}, {"page_count": 300, "first_publish_year": 2000}, "epub"
    )
    assert md_epub["page_count"] == 300


def test_tags_forbidden_filtered():
    # docs/ebook.txt:298 — no format/source/bestseller tags
    assert "epub" not in clean_tags(["epub", "fantasy"])
    assert "pdf" not in clean_tags(["pdf"])
    assert "retail" not in clean_tags(["retail"])
    assert "ocr" not in clean_tags(["ocr"])
    assert "convert" not in clean_tags(["convert"])
    assert "bestseller" not in clean_tags(["bestseller"])
    assert "awesome" not in clean_tags(["awesome"])
    assert clean_tags(["Science Fiction", "Fantasy"]) == "science.fiction, fantasy"


def test_tags_publication_level_preserved():
    md = build_metadata(
        {}, {"subjects": ["Fantasy", "History"], "first_publish_year": 2000}, "epub"
    )
    assert "fantasy" in md["tags"]
    assert "epub" not in md["tags"]


def test_translators_editors_illustrators_merged():
    # docs/ebook.txt:107,121,135 — translator/editor/illustrator are Release-level, merge file+scraper
    inbuilt = {
        "translators": ["Trans A"],
        "editors": ["Ed A"],
        "illustrators": ["Ill A"],
        "first_publish_year": 2000,
    }
    scraper = {
        "translators": ["Trans B"],
        "editors": ["Ed B"],
        "illustrators": ["Ill B"],
        "first_publish_year": 2000,
    }
    md = build_metadata(inbuilt, scraper, "epub")
    assert "Trans A" in md["translators"] and "Trans B" in md["translators"]
    assert "Ed A" in md["editors"] and "Ed B" in md["editors"]
    assert "Ill A" in md["illustrators"] and "Ill B" in md["illustrators"]


def test_payload_maps_four_fields_correctly():
    # docs/ebook.txt verification table + payload.py:7
    md = {
        "title": "Dune",
        "remaster_title": "Dune: Illustrated Edition",
        "year": 1965,
        "remaster_year": 2021,
        "authors": ["Frank Herbert"],
        "publisher": "Ace",
        "isbn": "9780441172719",
        "page_count": 412,
        "album_desc": "Canonical synopsis of Dune",
        "release_notes": "Revised edition notes",
        "tags": "fantasy, science.fiction",
        "language": "English",
        "format": "EPUB",
        "source": "Retail",
        "type": "E-Book",
    }
    data = compile_data_new_publication(md, "https://example.com/cover.jpg")
    assert data["book_title"] == "Dune"  # Canonical Publication title
    assert data["original_year"] == "1965"  # First Published
    assert data["title"] == "Dune: Illustrated Edition"  # Release title
    assert data["year"] == "2021"  # Release year
    assert data["record_label"] == "Ace"  # Publisher
    assert data["catalogue_number"] == "9780441172719"  # ISBN
    assert data["book_desc"] == "Canonical synopsis of Dune"  # synopsis
    assert data["album_desc"] == "Revised edition notes"  # release notes
    assert data["format"] == "EPUB"
    assert data["bitrate"] == "Retail"
    assert data["type"] == "2"


def test_payload_publication_vs_release_decision_rule():
    # docs/ebook.txt:626 — Publication = work, Release = edition
    inbuilt = {
        "title": "Dune: 50th Anniversary Edition",
        "year": 2021,
        "authors": ["Frank Herbert"],
        "language": "en",
    }
    scraper = {
        "title": "Dune",
        "first_publish_year": 1965,
        "year": 2021,
        "publish_year": 2021,
        "publisher": "Ace",
        "subjects": ["Fantasy"],
    }
    md = build_metadata(inbuilt, scraper, "epub")
    data = compile_data_new_publication(md, None)
    # Publication fields stable
    assert data["book_title"] == "Dune"
    assert data["original_year"] == "1965"
    # Release fields edition-specific
    assert (
        "50th Anniversary" in data["title"]
        or "Anniversary" in data["title"]
        or data["title"] == "Dune: 50th Anniversary Edition"
    )
    assert data["year"] == "2021"
    # Tags are Publication-level (work)
    assert "fantasy" in data["tags"]
    # Publisher is Release-level (edition)
    assert data["record_label"] == "Ace"
