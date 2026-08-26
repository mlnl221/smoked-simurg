import zipfile
from pathlib import Path

from simurg.metadata.epub import extract_epub


def _make_epub(
    path: Path,
    title="My Title",
    author="Author One",
    publisher="Peng",
    year="2020",
    isbn="9780140328721",
    lang="en",
    desc="Desc",
):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:publisher>{publisher}</dc:publisher>
    <dc:language>{lang}</dc:language>
    <dc:date>{year}-06-01</dc:date>
    <dc:identifier>urn:isbn:{isbn}</dc:identifier>
    <dc:description>{desc}</dc:description>
  </metadata>
  <manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c"/></spine>
</package>"""
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/c.xhtml", "<html/>")
    return path


def test_extract_epub_basic(tmp_path):
    p = tmp_path / "book.epub"
    _make_epub(
        p, title="Dune", author="Frank Herbert", publisher="Ace", year="1965", isbn="9780441172719"
    )
    data = extract_epub(p)
    assert data["title"] == "Dune"
    assert "Frank Herbert" in data["authors"]
    assert data["publisher"] == "Ace"
    assert data["year"] == 1965
    assert data["isbn"] == "9780441172719"
    assert data["language"] == "en"
    assert data["description"] == "Desc" or "Desc" in (data["description"] or "")


def test_extract_epub_no_isbn(tmp_path):
    p = tmp_path / "noisbn.epub"
    _make_epub(p, isbn="not-an-isbn")
    data = extract_epub(p)
    # invalid isbn should not be parsed as 10/13 -> None
    assert data["isbn"] is None or data["isbn"] == "not-an-isbn"  # allow fallback


def test_extract_epub_missing_file(tmp_path):
    p = tmp_path / "missing.epub"
    # not created
    data = extract_epub(p)
    # should not crash, return empty dict with Nones
    assert data["title"] is None
    assert data["authors"] == []


def test_extract_epub_multiple_authors(tmp_path):
    p = tmp_path / "multi.epub"
    # epub with two creators
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            """<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>""",
        )
        opf = """<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>T</dc:title><dc:creator>Alice</dc:creator><dc:creator>Bob</dc:creator></metadata></package>"""
        z.writestr("content.opf", opf)
    data = extract_epub(p)
    assert "Alice" in data["authors"]
    assert "Bob" in data["authors"]


def test_extract_epub_edition_and_roles(tmp_path):
    p = tmp_path / "edition.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            """<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>""",
        )
        opf = """<package xmlns="http://www.idpf.org/2007/opf" xmlns:opf="http://www.idpf.org/2007/opf">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Dune (Illustrated Edition)</dc:title>
<dc:creator opf:role="aut">Frank Herbert</dc:creator>
<dc:creator opf:role="ill">John Schoenherr</dc:creator>
<dc:creator opf:role="edt">Editor Person</dc:creator>
<meta property="dcterms:edition" content="Illustrated Edition"/>
</metadata></package>"""
        z.writestr("content.opf", opf)
    data = extract_epub(p)
    assert "Frank Herbert" in data["authors"]
    assert data["illustrators"] == ["John Schoenherr"]
    assert data["editors"] == ["Editor Person"]
    assert data["edition"] == "Illustrated Edition"
    # author must not leak into illustrators
    assert "Frank Herbert" not in data["illustrators"]


def _make_epub_with_content(path: Path, content_html: str, opf_meta: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            """<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>""",
        )
        opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>T</dc:title><dc:creator>A</dc:creator>{opf_meta}
  </metadata>
  <manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c"/></spine>
</package>"""
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/c.xhtml", content_html)
    return path


def test_extract_epub_estimates_page_count(tmp_path):
    # ~5000 words of lorem-style prose -> ~20 pages at 250 wpp.
    words = " ".join(f"word{i}" for i in range(5000))
    para = f"<html><body><p>{words}</p></body></html>"
    p = tmp_path / "big.epub"
    _make_epub_with_content(p, para)
    data = extract_epub(p)
    assert data["page_count"] is not None
    # 5000/250 = 20, allow rounding slack.
    assert 15 <= data["page_count"] <= 25


def test_extract_epub_honors_total_page_count_meta(tmp_path):
    meta = '<meta property="dcterms:total_page_count" content="312"/>'
    p = tmp_path / "paged.epub"
    _make_epub_with_content(p, "<html><body><p>short</p></body></html>", opf_meta=meta)
    data = extract_epub(p)
    assert data["page_count"] == 312


def test_extract_epub_no_prose_no_page_count(tmp_path):
    # Image-only / tiny content -> no usable word count -> no estimate.
    p = tmp_path / "tiny.epub"
    _make_epub_with_content(p, "<html><body><p>hi</p></body></html>")
    data = extract_epub(p)
    assert data["page_count"] is None
