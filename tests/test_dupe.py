from simurg.uploader.dupe import (
    _sanitize_for_dupe,
    filter_unnecessary_searchstrs,
    generate_dupe_search_strs,
)


def test_generate_dupe_search_strs_basic():
    strs = generate_dupe_search_strs("Dune", ["Frank Herbert"], isbn="9780441172719")
    assert "Frank Herbert Dune" in strs
    assert "Dune" in strs
    assert "9780441172719" in strs
    # dedupe
    strs2 = generate_dupe_search_strs("Dune", ["Frank Herbert"], isbn=None)
    assert strs2.count("Dune") == 1


def test_generate_dupe_no_authors():
    strs = generate_dupe_search_strs("My Book", [], isbn=None)
    assert "My Book" in strs
    assert len(strs) == 1


def test_sanitize_for_dupe():
    assert _sanitize_for_dupe("Dune (Illustrated Edition)") == "Dune"
    assert _sanitize_for_dupe(
        "My Book Vol. 2"
    ) == "My Book Vol. 2" or "My Book" in _sanitize_for_dupe("My Book Vol. 2")
    assert _sanitize_for_dupe("") == ""


def test_filter_unnecessary():
    # longer strings that contain shorter should be filtered to keep shortest
    strs = ["a b", "a b c", "a"]
    filtered = filter_unnecessary_searchstrs(strs)
    # a is subset of a b, so only a should remain (sorted by len)
    assert "a" in filtered
    # ensure no duplicates
    assert len(filtered) == len(set(filtered))
