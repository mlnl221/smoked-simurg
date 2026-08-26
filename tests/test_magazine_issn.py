"""Tests for Simurg magazine ISSN fallback (Playboy/Penthouse direct reuse)."""

from __future__ import annotations

import pytest

from simurg.uploader.magazine_issn import (
    _extract_issns,
    prompt_simurg_issn_reuse,
    search_simurg_magazine_issns,
)

# --- _extract_issns ---


def test_extract_issns_direct_keys():
    obj = {"magazine_print_issn": "1234-5678", "magazine_electronic_issn": "8765-4321"}
    assert _extract_issns(obj) == ("1234-5678", "8765-4321")


def test_extract_issns_catalogue_number():
    obj = {"catalogue_number": "0027-9358"}
    assert _extract_issns(obj) == ("0027-9358", None)


def test_extract_issns_list_and_comma():
    obj = {"issn": ["1234-5678", "8765-4321"]}
    assert _extract_issns(obj) == ("1234-5678", "8765-4321")
    obj2 = {"issn": "1234-5678, 8765-4321"}
    assert _extract_issns(obj2) == ("1234-5678", "8765-4321")


def test_extract_issns_nested_group():
    obj = {"group": {"magazine_print_issn": "1111-2222", "issn": "3333-4444"}}
    p, e = _extract_issns(obj)
    assert p == "1111-2222"
    assert e == "3333-4444"


def test_extract_issns_none():
    assert _extract_issns({}) == (None, None)
    assert _extract_issns({"publisher": "Penthouse"}) == (None, None)


def test_extract_issns_wiki_body():
    obj = {"group": {"wikiBody": "Some text ISSN 1234-5678 more"}}
    assert _extract_issns(obj) == ("1234-5678", None)


# --- search_simurg_magazine_issns with mocked gazelle ---


class FakeGazelle:
    def __init__(self, browse_resp=None, torr_resp_map=None, authkey="realkey"):
        self.base_url = "https://simurg.world"
        self.authkey = authkey
        self._browse = browse_resp or {"results": []}
        self._torr = torr_resp_map or {}

    async def request(self, action, **kwargs):
        assert action == "browse"
        return self._browse

    async def torrentgroup(self, gid):
        data = self._torr.get(str(gid))
        if data is None:
            raise ValueError(f"no torr group {gid}")
        return data


def test_search_simurg_magazine_issns_browse_direct():
    browse = {
        "results": [
            {
                "groupId": 1,
                "groupName": "Playboy",
                "year": "1953",
                "magazine_print_issn": "0032-1478",
                "magazine_electronic_issn": "1939-1234",
                "publisher": "Playboy Enterprises",
                "tags": ["magazine"],
            },
            {
                "groupId": 2,
                "groupName": "Penthouse",
                "year": "1965",
                "catalogue_number": "0090-1234",
                "publisher": "Penthouse",
            },
        ]
    }
    gz = FakeGazelle(browse_resp=browse)
    res = search_simurg_magazine_issns(gz, "Playboy", limit=10)
    assert len(res) == 2
    assert res[0]["print_issn"] == "0032-1478"
    assert res[0]["electronic_issn"] == "1939-1234"
    assert res[0]["url"] == "https://simurg.world/torrents.php?id=1"
    assert res[1]["print_issn"] == "0090-1234"


def test_search_simurg_via_torrentgroup_fallback():
    # Browse has no ISSN, but torrentgroup does
    browse = {
        "results": [
            {"groupId": 99, "groupName": "Penthouse", "year": "1965", "publisher": "Penthouse"},
        ]
    }
    torr_map = {
        "99": {
            "group": {
                "id": 99,
                "name": "Penthouse",
                "year": 1965,
                "magazine_print_issn": "0032-1478",
            }
        },
    }
    gz = FakeGazelle(browse_resp=browse, torr_resp_map=torr_map)
    res = search_simurg_magazine_issns(gz, "Penthouse", limit=10)
    assert len(res) == 1
    assert res[0]["print_issn"] == "0032-1478"
    assert res[0]["title"] == "Penthouse"


def test_search_dummy_auth_skipped():
    gz = FakeGazelle(
        browse_resp={"results": [{"groupId": 1, "magazine_print_issn": "1234-5678"}]},
        authkey="dummy",
    )
    res = search_simurg_magazine_issns(gz, "Playboy")
    assert res == []


def test_search_empty():
    gz = FakeGazelle(browse_resp={"results": []})
    assert search_simurg_magazine_issns(gz, "Unknown") == []


def test_search_none_title():
    gz = FakeGazelle()
    assert search_simurg_magazine_issns(gz, "") == []
    assert search_simurg_magazine_issns(None, "Playboy") == []


# --- prompt_simurg_issn_reuse ---


def test_prompt_both_present_both_missing_yes(monkeypatch):
    metadata = {"print_issn": None, "electronic_issn": None}
    chosen = {
        "title": "Penthouse",
        "groupId": 1,
        "print_issn": "1234-5678",
        "electronic_issn": "8765-4321",
        "url": "https://simurg.world/torrents.php?id=1",
    }
    # Simulate user answering Y for both
    answers = iter(["y", "y"])
    monkeypatch.setattr("simurg.uploader.magazine_issn.click.prompt", lambda *a, **k: next(answers))
    overrides = prompt_simurg_issn_reuse(metadata, chosen, dry_run=False)
    assert overrides == {"print_issn": "1234-5678", "electronic_issn": "8765-4321"}


def test_prompt_single_for_both_yes(monkeypatch):
    metadata = {"print_issn": None, "electronic_issn": None}
    chosen = {"title": "Playboy", "groupId": 2, "print_issn": "0032-1478", "electronic_issn": None}
    # First prompt asks use for BOTH? -> y
    monkeypatch.setattr("simurg.uploader.magazine_issn.click.prompt", lambda *a, **k: "y")
    overrides = prompt_simurg_issn_reuse(metadata, chosen, dry_run=False)
    assert overrides == {"print_issn": "0032-1478", "electronic_issn": "0032-1478"}


def test_prompt_single_for_both_no_then_print(monkeypatch):
    metadata = {"print_issn": None, "electronic_issn": None}
    chosen = {"title": "Playboy", "groupId": 2, "print_issn": "0032-1478", "electronic_issn": None}
    answers = iter(["n", "p"])  # no to both, yes to print
    monkeypatch.setattr("simurg.uploader.magazine_issn.click.prompt", lambda *a, **k: next(answers))
    overrides = prompt_simurg_issn_reuse(metadata, chosen, dry_run=False)
    assert overrides == {"print_issn": "0032-1478"}


def test_prompt_already_has_print_only_electronic(monkeypatch):
    metadata = {"print_issn": "1234-5678", "electronic_issn": None}
    chosen = {"title": "X", "groupId": 1, "print_issn": "1234-5678", "electronic_issn": "8765-4321"}
    monkeypatch.setattr("simurg.uploader.magazine_issn.click.prompt", lambda *a, **k: "y")
    overrides = prompt_simurg_issn_reuse(metadata, chosen, dry_run=False)
    assert overrides == {"electronic_issn": "8765-4321"}


def test_prompt_no_missing_returns_empty(monkeypatch):
    metadata = {"print_issn": "1234-5678", "electronic_issn": "8765-4321"}
    chosen = {"print_issn": "9999-9999", "electronic_issn": "8888-8888", "title": "X", "groupId": 1}
    # Should not prompt at all
    monkeypatch.setattr(
        "simurg.uploader.magazine_issn.click.prompt",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )
    overrides = prompt_simurg_issn_reuse(metadata, chosen, dry_run=False)
    assert overrides == {}


def test_prompt_dry_run_auto():
    metadata = {"print_issn": None, "electronic_issn": None}
    chosen = {
        "title": "Penthouse",
        "groupId": 1,
        "print_issn": "1234-5678",
        "electronic_issn": "8765-4321",
    }
    overrides = prompt_simurg_issn_reuse(metadata, chosen, dry_run=True)
    assert overrides == {"print_issn": "1234-5678", "electronic_issn": "8765-4321"}


def test_prompt_dry_run_single_fills_both():
    metadata = {"print_issn": None, "electronic_issn": None}
    chosen = {"title": "Playboy", "groupId": 2, "print_issn": "0032-1478", "electronic_issn": None}
    overrides = prompt_simurg_issn_reuse(metadata, chosen, dry_run=True)
    assert overrides == {"print_issn": "0032-1478", "electronic_issn": "0032-1478"}


def test_prompt_decline(monkeypatch):
    metadata = {"print_issn": None, "electronic_issn": None}
    chosen = {"title": "X", "groupId": 1, "print_issn": "1234-5678", "electronic_issn": "8765-4321"}
    answers = iter(["n", "n"])
    monkeypatch.setattr("simurg.uploader.magazine_issn.click.prompt", lambda *a, **k: next(answers))
    overrides = prompt_simurg_issn_reuse(metadata, chosen, dry_run=False)
    assert overrides == {}
