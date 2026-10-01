"""Cover candidate ordering: chosen first, alternates ranked, deduped."""

from simurg.cli import _ranked_cover_candidates


def _hit(scraper, url, score):
    return {"_scraper": scraper, "cover_url": url, "_score": score}


def test_chosen_first_then_ranked():
    hits = [
        _hit("goodreads", "https://g/1.jpg", 0.9),
        _hit("openlibrary", "https://o/1.jpg", 0.7),
    ]
    out = _ranked_cover_candidates("https://c/1.jpg", hits)
    assert [u for u, _ in out] == ["https://c/1.jpg", "https://g/1.jpg", "https://o/1.jpg"]


def test_dedupe_and_none_cover():
    hits = [
        _hit("a", "https://c/1.jpg", 0.9),  # same as chosen
        _hit("librarything", None, 0.8),  # never has covers
        _hit("b", "https://b/1.jpg", 0.5),
        _hit("b2", "https://b/1.jpg", 0.4),  # dup
    ]
    out = _ranked_cover_candidates("https://c/1.jpg", hits)
    assert [u for u, _ in out] == ["https://c/1.jpg", "https://b/1.jpg"]


def test_no_chosen_no_hits():
    assert _ranked_cover_candidates(None, None) == []
    assert _ranked_cover_candidates("", []) == []
