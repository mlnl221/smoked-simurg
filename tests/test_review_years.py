"""Year editor buffer defaults: empty Year pre-fills from remaster_year."""

import simurg.metadata.review as rev


def _save_unchanged(monkeypatch, seen):
    def fake_edit(text, editor=None, extension=None):
        seen.append(text)
        return text

    monkeypatch.setattr(rev.click, "edit", fake_edit)


def test_year_prefills_from_remaster(monkeypatch):
    seen = []
    _save_unchanged(monkeypatch, seen)
    md = {"year": None, "remaster_year": 2018}
    rev._edit_years(md, editor="true")
    assert "Year         : 2018" in seen[0]
    assert md["year"] == 2018
    assert md["remaster_year"] == 2018


def test_year_keeps_own_value(monkeypatch):
    seen = []
    _save_unchanged(monkeypatch, seen)
    md = {"year": 1965, "remaster_year": 2021}
    rev._edit_years(md, editor="true")
    assert "Year         : 1965" in seen[0]
    assert md == {"year": 1965, "remaster_year": 2021}


def test_year_stays_empty_when_both_missing(monkeypatch):
    seen = []
    _save_unchanged(monkeypatch, seen)
    md = {"year": None, "remaster_year": None}
    rev._edit_years(md, editor="true")
    assert "Year         : \n" in seen[0]
    assert md["year"] is None
