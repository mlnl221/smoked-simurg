from simurg.metadata.combine import (
    build_metadata,
    clean_isbn,
    clean_tags,
    detect_edition,
    fit_tags_to_limit,
    normalize_authors,
    strip_edition_from_canonical,
    suggest_tags_from_description,
    tags_are_sparse,
    validate_metadata,
)


def test_normalize_authors_string_split():
    assert normalize_authors("Frank Herbert") == ["Frank Herbert"]
    assert normalize_authors("Author A & Author B") == ["Author A", "Author B"]
    assert normalize_authors("A; B and C; D") == ["A", "B", "C", "D"]


def test_normalize_authors_flip_last_first():
    # Last, First -> First Last for natural display
    assert normalize_authors("Defoe, Daniel") == ["Daniel Defoe"]
    assert normalize_authors(["Defoe, Daniel"]) == ["Daniel Defoe"]
    assert normalize_authors("Card, Orson Scott") == ["Orson Scott Card"]
    assert normalize_authors("Daniel Defoe") == ["Daniel Defoe"]
    # Multiple authors with Last, First
    assert normalize_authors("Defoe, Daniel and Austen, Jane") == ["Daniel Defoe", "Jane Austen"]


def test_normalize_authors_dedupe():
    assert normalize_authors(["Alice", "alice ", " Bob "]) == ["Alice", "Bob"]


def test_clean_isbn():
    assert clean_isbn("978-0-13-409267-5") == "9780134092675"
    assert clean_isbn("0-306-40615-2") == "0306406152"
    assert clean_isbn("invalid") is None
    assert clean_isbn(None) is None
    assert clean_isbn("978-123") is None


def test_clean_tags_forbidden():
    assert clean_tags(["Fantasy", "Epub", "Science Fiction"]) == "fantasy, science.fiction"
    # forbidden tags skipped
    assert "epub" not in clean_tags(["epub"])
    # dots for spaces, lower
    assert clean_tags(["Science Fiction"]) == "science.fiction"
    # dedupe and limit 8
    many = [f"tag{i}" for i in range(20)]
    result = clean_tags(many)
    assert len(result.split(", ")) == 8


def test_strip_edition():
    assert strip_edition_from_canonical("Dune (Illustrated Edition)") == "Dune"
    assert strip_edition_from_canonical("My Book Vol. 3") in [
        "My Book",
        "My Book Vol. 3",
    ]  # at least not crash
    assert strip_edition_from_canonical("Clean Title") == "Clean Title"
    assert strip_edition_from_canonical("") == ""


def test_detect_edition():
    assert detect_edition("Dune (Illustrated Edition)") == "Illustrated Edition"
    assert detect_edition("Dune [Special Edition]") == "Special Edition"
    assert detect_edition("My Book Vol. 3") == "Vol. 3"
    assert detect_edition("Clean Title") is None
    assert detect_edition("") is None
    assert detect_edition(None) is None


def test_build_metadata_prefers_inbuilt():
    inbuilt = {
        "title": "Inbuilt Title",
        "authors": ["A"],
        "year": 2020,
        "publisher": "Pub",
        "isbn": "9780134092675",
    }
    scraper = {
        "title": "Scraper Title",
        "authors": ["B"],
        "year": 1999,
        "first_publish_year": 1999,
        "publisher": "Other",
    }
    md = build_metadata(inbuilt, scraper, "epub")
    assert md["title"] == "Inbuilt Title"
    assert md["authors"] == ["A"]
    # Publisher: file truth wins per docs/ebook.txt:372 (edition publisher)
    assert md["publisher"] == "Pub"
    assert md["format"] == "EPUB"
    assert md["language"] == "English"


def test_build_metadata_fallback_filepath():
    md = build_metadata({}, {}, "pdf", filepath="/some/My_Book.pdf")
    assert md["title"] == "My Book"
    assert md["format"] == "PDF"


def test_build_metadata_year_backfills_from_edition_year():
    inbuilt = {"title": "X", "authors": ["A"]}
    scraper = {"title": "X", "authors": ["A"], "year": 2022}
    md = build_metadata(inbuilt, scraper, "epub")
    assert md["year"] == 2022
    assert md["remaster_year"] == 2022


def test_build_metadata_page_count_prefers_scraper():
    # For EPUB (reflowable estimate) scraper wins; for PDF file wins (exact).
    # EPUB: scraper print count preferred over 250wpp estimate.
    inbuilt_epub = {"page_count": 100, "year": 2020}
    scraper = {"page_count": 250, "first_publish_year": 2020}
    md_epub = build_metadata(inbuilt_epub, scraper, "epub")
    assert md_epub["page_count"] == 250
    # PDF: file len(pages) is exact, so file wins.
    inbuilt_pdf = {"page_count": 100}
    md_pdf = build_metadata(inbuilt_pdf, scraper, "pdf")
    assert md_pdf["page_count"] == 100


def test_build_metadata_page_count_falls_back_to_scraper():
    # No file count (e.g. DJVU) -> use the scraper value.
    inbuilt = {"year": 2020}
    scraper = {"page_count": 250}
    md = build_metadata(inbuilt, scraper, "djvu")
    assert md["page_count"] == 250


def test_build_metadata_page_count_none_when_unavailable():
    md = build_metadata({"year": 2020}, {}, "pdf")
    assert md["page_count"] is None


def test_build_metadata_synopsis_truncate():
    long_desc = "para1\n\npara2\n\npara3\n\n" + "x" * 5000
    md = build_metadata({"description": long_desc}, {}, "epub")
    assert md["album_desc"].count("\n\n") <= 1
    assert len(md["album_desc"]) <= 2000


def test_validate_metadata_missing():
    md = {
        "title": "T",
        "authors": ["A"],
        "year": 2020,
        "publisher": "P",
        "format": "EPUB",
        "source": "Retail",
    }
    assert validate_metadata(md) == []
    md2 = {"title": "", "authors": [], "year": None, "publisher": "", "format": "", "source": ""}
    missing = validate_metadata(md2)
    assert "title" in missing
    assert "authors" in missing
    assert "year" in missing


def test_validate_metadata_year_bounds():
    md = {
        "title": "T",
        "authors": ["A"],
        "year": 999,
        "publisher": "P",
        "format": "EPUB",
        "source": "Retail",
    }
    assert "year" in validate_metadata(md)
    md2 = {
        "title": "T",
        "authors": ["A"],
        "year": 2101,
        "publisher": "P",
        "format": "EPUB",
        "source": "Retail",
    }
    assert "year" in validate_metadata(md2)
    md3 = {
        "title": "T",
        "authors": ["A"],
        "year": "2020",
        "publisher": "P",
        "format": "EPUB",
        "source": "Retail",
    }
    assert validate_metadata(md3) == []


def test_tags_are_sparse():
    assert tags_are_sparse("") is True
    assert tags_are_sparse([]) is True
    assert tags_are_sparse("non.fiction") is True
    assert tags_are_sparse("fantasy") is True
    assert tags_are_sparse("fantasy, mystery") is False
    assert tags_are_sparse(["fantasy", "mystery"]) is False


def test_suggest_tags_fantasy_from_description():
    desc = "A young wizard rides his dragon on a magical quest through an enchanted realm."
    assert "fantasy" in suggest_tags_from_description(desc)


def test_suggest_tags_skips_short_and_synth():
    assert suggest_tags_from_description("") == ""
    assert suggest_tags_from_description("short") == ""
    synth = "Dune by Frank Herbert (1965). Tags: non.fiction. Uploaded via smoked-simurg."
    assert suggest_tags_from_description(synth) == ""


def test_suggest_tags_excludes_existing_and_caps():
    desc = "A detective solves a murder; a dragon circles a haunted castle."
    existing = [f"tag{i}" for i in range(7)]
    result = suggest_tags_from_description(desc, existing=existing)
    assert len(result.split(", ")) <= 1
    full = [f"tag{i}" for i in range(8)]
    assert suggest_tags_from_description(desc, existing=full) == ""
    # existing tags never re-suggested
    assert "fantasy" not in suggest_tags_from_description(
        "A wizard casts a spell.", existing="fantasy"
    )


def test_suggest_tags_never_forbidden():
    desc = "The bestseller ebook retail pdf of the year, an awesome must-read."
    result = suggest_tags_from_description(desc)
    for bad in ("epub", "pdf", "retail", "bestseller", "awesome", "ebook"):
        assert bad not in result.split(", ")


def test_build_metadata_enriches_barren_tags():
    scraper = {
        "description": "A detective investigates a brutal murder in Victorian London.",
        "first_publish_year": 2000,
    }
    md = build_metadata({"title": "T", "authors": ["A"]}, scraper, "epub")
    assert md["tags"] != "non.fiction"
    assert "mystery" in md["tags"] or "crime" in md["tags"]


def test_build_metadata_keeps_rich_tags_untouched():
    scraper = {"subjects": ["Fantasy", "History"], "first_publish_year": 2000}
    md = build_metadata({"title": "T", "authors": ["A"]}, scraper, "epub")
    assert md["tags"] == "fantasy, history"


def test_clean_tags_collapses_dots_and_strips_edges():
    assert clean_tags(["Fiction Fantasy Collections / Anthologies"]) == (
        "fiction.fantasy.collections.anthologies"
    )
    assert clean_tags([" --Foo  Bar-- "]) == "foo.bar"


def test_clean_tags_trims_to_200_chars():
    # Regression: Djinn subjects produced 203 chars, tracker limit is 200.
    subjects = [
        "English Fantasy Fiction",
        "Fairy Tales",
        "Fiction Short Stories Single Author",
        "England Fiction",
        "Fiction Fantasy Short Stories",
        "Fiction Fantasy Collections / Anthologies",
        "Women Authors",
        "New York Times Reviewed",
    ]
    tags = clean_tags(subjects)
    assert ".." not in tags
    assert len(tags) <= 200
    assert tags.startswith("english.fantasy.fiction")


def test_fit_tags_to_limit_drops_trailing_keeps_first():
    tags = ["a" * 90, "b" * 90, "c" * 90]
    assert fit_tags_to_limit(tags) == ["a" * 90, "b" * 90]
    assert fit_tags_to_limit([]) == []
    single = fit_tags_to_limit(["x" * 250])
    assert len(single) == 1 and len(single[0]) == 200
