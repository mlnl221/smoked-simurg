"""Tests for magazine filename decoding (incl. legacy National Geographic naming)."""

from pathlib import Path

import pytest

from simurg.cli import (
    _detect_magazine_decade_packs,
    _detect_magazine_year_packs,
)
from simurg.metadata.magazine import (
    build_magazine_pack_metadata,
    decode_magazine_filename,
)


@pytest.mark.parametrize(
    "name,exp_year,exp_date,exp_prec,exp_canon",
    [
        # Legacy National Geographic: date embedded mid-stem with volume/issue junk
        (
            "National Geographic 1888-01 Oct 001-1.pdf",
            1888,
            "1888-01",
            "month",
            "National Geographic",
        ),
        ("National Geographic 1889-01 001-2.pdf", 1889, "1889-01", "month", "National Geographic"),
        (
            "National Geographic 1990-01 177-1 Jan.pdf",
            1990,
            "1990-01",
            "month",
            "National Geographic",
        ),
        # Clean end-anchored names
        ("National Geographic 2000-01.pdf", 2000, "2000-01", "month", "National Geographic"),
        ("National Geographic 2010-01 .pdf", 2010, "2010-01", "month", "National Geographic"),
        # Space-separated date + region suffix
        ("National Geographic 2020 01 US.pdf", 2020, "2020-01", "month", "National Geographic"),
        # Dash-separated canonical (user example)
        ("Playboy - 1995 Complete Year.pdf", 1995, "1995", "year", "Playboy"),
        # Simple no-dash name
        ("Penthouse 2002-02.pdf", 2002, "2002-02", "month", "Penthouse"),
    ],
)
def test_decode_magazine_filename_legacy(name, exp_year, exp_date, exp_prec, exp_canon):
    r = decode_magazine_filename(Path(name))
    assert r["year"] == exp_year, r
    assert r["issue_date"] == exp_date, r
    assert r["issue_date_precision"] == exp_prec, r
    assert r["canonical_title"] == exp_canon, r


def _make_files(tmp_path: Path, names):
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"%PDF-1.4 fake")
        out.append(p)
    return out


def test_year_pack_detection_legacy_naming(tmp_path: Path):
    files = _make_files(
        tmp_path,
        [
            # Two 1888 issues (legacy naming) -> year pack 1888
            "National Geographic 1888-01 Oct 001-1.pdf",
            "National Geographic 1888-02 Oct 001-2.pdf",
            # Two 1889 issues -> year pack 1889
            "National Geographic 1889-01 001-2.pdf",
            "National Geographic 1889-02 001-3.pdf",
        ],
    )
    packs = _detect_magazine_year_packs(files)
    keys = {(c, y) for (c, y) in packs}
    assert ("national geographic", 1888) in keys
    assert ("national geographic", 1889) in keys
    assert len(packs[("national geographic", 1888)]) == 2


def test_decade_pack_detection_legacy_naming(tmp_path: Path):
    names = []
    # 1888 + 1889 (4 files) -> decade 1880 should pass the >=4 threshold
    for m in (1, 2):
        names.append(f"National Geographic 1888-{m:02d} Oct 001-{m}.pdf")
    for m in (1, 2):
        names.append(f"National Geographic 1889-{m:02d} 002-{m}.pdf")
    files = _make_files(tmp_path, names)
    packs = _detect_magazine_decade_packs(files)
    assert ("national geographic", 1880) in packs
    assert len(packs[("national geographic", 1880)]) == 4


def test_decade_pack_excludes_single_year(tmp_path: Path):
    # Only 2 files in one year -> no decade pack (threshold >=4)
    files = _make_files(
        tmp_path,
        ["National Geographic 2000-01.pdf", "National Geographic 2000-02.pdf"],
    )
    packs = _detect_magazine_decade_packs(files)
    assert packs == {}


def _build_pack(tmp_path, names, pack_type, pack_key):
    files = _make_files(tmp_path, names)
    file_infos = []
    for p in files:
        md = decode_magazine_filename(p)
        file_infos.append((p, 100, md["issue_date"] or p.stem))
    return build_magazine_pack_metadata(
        canonical="National Geographic",
        pack_type=pack_type,
        pack_key=pack_key,
        files=files,
        scraper={},
        fmt="PDF",
        source=None,
        file_infos=file_infos,
    )


def test_pack_metadata_coverage_partial(tmp_path: Path):
    names = [f"National Geographic 1990-{m:02d}.pdf" for m in (1, 2, 3)]
    md = _build_pack(tmp_path, names, "Decade Pack", 1990)
    assert md["pack_coverage_start"] == "1990-01-01"
    assert md["pack_coverage_end"] == "1990-03-01"
    assert md["pack_issue_count"] == 3
    assert md["pack_is_complete"] is False
    assert "1990-01" in md["pack_issue_manifest"]


def test_pack_metadata_coverage_complete_year(tmp_path: Path):
    names = [f"National Geographic 1995-{m:02d}.pdf" for m in range(1, 13)]
    md = _build_pack(tmp_path, names, "Year Pack", 1995)
    assert md["pack_coverage_start"] == "1995-01-01"
    assert md["pack_coverage_end"] == "1995-12-01"
    assert md["pack_issue_count"] == 12
    assert md["pack_is_complete"] is True


def test_payload_includes_pack_coverage(tmp_path: Path):
    from simurg.uploader.payload import compile_data_new_magazine

    names = [f"National Geographic 1990-{m:02d}.pdf" for m in (1, 2, 3)]
    md = _build_pack(tmp_path, names, "Decade Pack", 1990)
    data = compile_data_new_magazine(md, cover_url=None)
    assert data["magazine_coverage_start"] == "1990-01-01"
    assert data["magazine_coverage_end"] == "1990-03-01"
    assert data["magazine_issue_count"] == "3"
    assert data["magazine_issue_manifest"]
    # Partial pack: completeness checkbox must NOT be set
    assert "magazine_is_complete" not in data
    assert data["magazine_release_type"] == "Decade Pack"


def test_payload_pack_complete_sets_is_complete(tmp_path: Path):
    from simurg.uploader.payload import compile_data_new_magazine

    names = [f"National Geographic 1995-{m:02d}.pdf" for m in range(1, 13)]
    md = _build_pack(tmp_path, names, "Year Pack", 1995)
    data = compile_data_new_magazine(md, cover_url=None)
    assert data["magazine_is_complete"] == "1"


def test_review_metadata_pack_edits_coverage(monkeypatch):
    import json

    import simurg.metadata.review as rev

    md = {
        "release_type": "Decade Pack",
        "title": "National Geographic",
        "release_title": "1990-1999 (24/120)",
        "year": 1990,
        "pack_coverage_start": "1998-01-01",
        "pack_coverage_end": "1999-12-01",
        "pack_issue_count": 24,
        "pack_is_complete": False,
    }

    def fake_edit(text, editor=None, extension=None):
        d = json.loads(text)
        d["pack_is_complete"] = 1
        d["pack_coverage_end"] = "1999-12-31"
        return json.dumps(d, indent=2)

    monkeypatch.setattr(rev.click, "edit", fake_edit)
    monkeypatch.setattr(rev.sys.stdin, "isatty", lambda: True)
    prompts = iter(["*", "n"])
    monkeypatch.setattr(rev.click, "prompt", lambda *a, **k: next(prompts))

    out = rev.review_metadata(md, is_mag=True, dry_run=False)
    assert out["pack_is_complete"] == 1
    assert out["pack_coverage_end"] == "1999-12-31"
