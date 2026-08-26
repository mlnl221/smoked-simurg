import re

from simurg.constants import (
    ALLOWED_EXTENSIONS,
    BLACKLISTED_CHARS,
    CANONICAL_STRIP_RE,
    EDITION_RE,
    FORBIDDEN_TAGS,
    FORMAT_MAP,
    SOURCE_LABELS,
    UPLOAD_TYPE,
)


def test_allowed_extensions_contains_ebooks():
    assert ".epub" in ALLOWED_EXTENSIONS
    assert ".pdf" in ALLOWED_EXTENSIONS
    assert ".mobi" in ALLOWED_EXTENSIONS
    assert ".azw3" in ALLOWED_EXTENSIONS
    assert ".djvu" in ALLOWED_EXTENSIONS
    assert ".txt" not in ALLOWED_EXTENSIONS


def test_format_map_uppercase():
    for ext, fmt in FORMAT_MAP.items():
        assert fmt == fmt.upper()
        assert fmt in {"EPUB", "PDF", "MOBI", "AZW3", "DJVU"}


def test_source_labels():
    assert {"Retail", "Scan", "OCR", "Convert", "Other"} == SOURCE_LABELS


def test_forbidden_tags_subset():
    assert "epub" in FORBIDDEN_TAGS
    assert "pdf" in FORBIDDEN_TAGS
    assert "retail" in FORBIDDEN_TAGS


def test_blacklisted_chars_replaces():
    sample = 'a:b?c<d>e\\f*g|h"i/j'
    replaced = re.sub(BLACKLISTED_CHARS, "_", sample)
    assert ":" not in replaced
    assert "?" not in replaced
    assert "<" not in replaced
    assert "_" in replaced


def test_edition_re_strips():
    title = "Dune (Illustrated Edition) Vol. 2"
    stripped = EDITION_RE.sub("", title)
    assert "Illustrated" not in stripped

    title2 = "My Book 2nd edition"
    assert EDITION_RE.search(title2) is not None


def test_canonical_strip_re():
    assert CANONICAL_STRIP_RE.search("Title (Illustrated)") is not None
    assert CANONICAL_STRIP_RE.search("Plain Title") is None
    cleaned = CANONICAL_STRIP_RE.sub("", "My Book (Deluxe Edition)")
    assert "Deluxe" not in cleaned


def test_upload_type():
    assert UPLOAD_TYPE == "E-Book"
