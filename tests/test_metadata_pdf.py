from pathlib import Path

import pytest

try:
    from pypdf import PdfWriter

    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

from simurg.metadata.pdf import extract_pdf


def _make_pdf(path: Path, title="T", author="A", subject="S"):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": title, "/Author": author, "/Subject": subject})
    with open(path, "wb") as f:
        writer.write(f)


@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
def test_extract_pdf_basic(tmp_path):
    p = tmp_path / "a.pdf"
    _make_pdf(p, title="My Book", author="Author One", subject="A description")
    data = extract_pdf(p)
    assert data["title"] == "My Book"
    assert "Author One" in data["authors"]
    assert data["description"] == "A description"
    assert data["page_count"] == 1
    assert data["is_encrypted"] is False


@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
def test_extract_pdf_encrypted(tmp_path):
    p = tmp_path / "enc.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("pass")
    with open(p, "wb") as f:
        writer.write(f)
    data = extract_pdf(p)
    assert data["is_encrypted"] is True


def test_extract_pdf_missing(tmp_path):
    p = tmp_path / "missing.pdf"
    data = extract_pdf(p)
    # should contain error or not crash
    assert "is_encrypted" in data or "error" in data


@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
def test_extract_pdf_isbn_in_text(tmp_path):
    p = tmp_path / "isbn.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    # pypdf text extraction for blank page is empty, but we test that it doesn't crash on ISBN search
    with open(p, "wb") as f:
        writer.write(f)
    data = extract_pdf(p)
    assert data["page_count"] == 1
