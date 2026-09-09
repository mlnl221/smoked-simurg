"""Auto-append of description-derived tags in review_metadata."""

import simurg.metadata.review as rev


def _tty_n(monkeypatch):
    monkeypatch.setattr(rev.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(rev.click, "prompt", lambda *a, **k: "n")


def test_review_auto_adds_tags_for_sparse_ebook(monkeypatch):
    _tty_n(monkeypatch)
    md = {
        "title": "The Dragon Book",
        "tags": "",
        "album_desc": "A young wizard rides his dragon on a magical quest.",
    }
    out = rev.review_metadata(md, is_mag=False)
    assert "fantasy" in out["tags"]


def test_review_keeps_rich_tags_untouched(monkeypatch):
    _tty_n(monkeypatch)
    md = {
        "title": "T",
        "tags": "fantasy, mystery",
        "album_desc": "A young wizard rides his dragon on a magical quest.",
    }
    out = rev.review_metadata(md, is_mag=False)
    assert out["tags"] == "fantasy, mystery"


def test_review_skips_magazines(monkeypatch):
    _tty_n(monkeypatch)
    md = {
        "title": "Mag",
        "tags": "magazine",
        "book_desc": "A detective investigates a brutal murder.",
    }
    out = rev.review_metadata(md, is_mag=True)
    assert out["tags"] == "magazine"
