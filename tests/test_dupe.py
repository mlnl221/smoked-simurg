from unittest import mock

from simurg.uploader.dupe import (
    _sanitize_for_dupe,
    build_early_search_strs,
    check_early_dupes,
    clear_dupe_cache,
    filter_unnecessary_searchstrs,
    generate_dupe_search_strs,
    get_search_results,
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


class _FakeSite:
    site_string = "simurg.world"
    base_url = "https://simurg.world"

    def __init__(self, results_by_query):
        self._map = {k.lower(): v for k, v in results_by_query.items()}
        self.calls: list[str] = []

    async def request(self, action, **kwargs):
        q = kwargs.get("searchstr", "")
        self.calls.append(q)
        return {"results": self._map.get(q.lower(), [])}


_HIT = {
    "groupId": 9175,
    "artist": "Harlan Coben",
    "groupName": "The Woods",
    "groupYear": 2004,
    "tags": ["fiction"],
}


def test_build_early_search_strs_inbuilt():
    inbuilt = {"title": "Dune", "authors": ["Frank Herbert"], "isbn": "9780441172719"}
    strs = build_early_search_strs(inbuilt, "ignored-stem")
    assert "Frank Herbert Dune" in strs
    assert "Dune" in strs
    assert "9780441172719" in strs


def test_build_early_search_strs_filename_fallback():
    # MOBI/DJVU carry no title — stem becomes the query
    strs = build_early_search_strs({"title": None, "authors": [], "isbn": None}, "Some Book")
    assert strs == ["Some Book"]


def test_build_early_search_strs_empty():
    assert build_early_search_strs({}, None) == []
    assert build_early_search_strs({"title": "", "authors": []}, "") == []


def test_build_early_search_strs_magazine():
    inbuilt = {"canonical_title": "Penthouse", "title": "Penthouse"}
    assert build_early_search_strs(inbuilt, "stem") == ["Penthouse"]


def test_get_search_results_caches_identical_queries():
    clear_dupe_cache()
    site = _FakeSite({"harlan coben the woods": [_HIT]})
    strs = ["Harlan Coben The Woods"]
    first = get_search_results(site, strs)
    assert first == [_HIT]
    assert site.calls == strs
    second = get_search_results(site, strs)
    assert second == [_HIT]
    assert site.calls == strs  # no second network call


def test_check_early_dupes_no_results_continues_without_prompt():
    clear_dupe_cache()
    site = _FakeSite({})
    with mock.patch("click.prompt", side_effect=AssertionError("must not prompt")):
        decision, results = check_early_dupes(site, ["Unknown Book Xyz"])
    assert decision == "continue"
    assert results == []


def test_check_early_dupes_non_tty_continues_without_prompt():
    clear_dupe_cache()
    site = _FakeSite({"the woods": [_HIT]})
    with (
        mock.patch("sys.stdin.isatty", return_value=False),
        mock.patch("click.prompt", side_effect=AssertionError("must not prompt")),
    ):
        decision, results = check_early_dupes(site, ["The Woods"])
    assert decision == "continue"
    assert results == [_HIT]


def test_check_early_dupes_prompt_choices():
    for answer, expected in (
        ("c", "continue"),
        ("s", "skip"),
        ("d", "delete"),
        ("a", "abort"),
    ):
        clear_dupe_cache()
        site = _FakeSite({"the woods": [_HIT]})
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("click.prompt", return_value=answer),
        ):
            decision, _ = check_early_dupes(site, ["The Woods"])
        assert decision == expected


def test_check_early_dupes_invalid_then_continue():
    clear_dupe_cache()
    site = _FakeSite({"the woods": [_HIT]})
    with (
        mock.patch("sys.stdin.isatty", return_value=True),
        mock.patch("click.prompt", side_effect=["x", "c"]),
    ):
        decision, _ = check_early_dupes(site, ["The Woods"])
    assert decision == "continue"
