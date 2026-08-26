import tempfile
import zipfile
from pathlib import Path

import click
from click.testing import CliRunner

from simurg.cli import cli


def _make_epub(path: Path, title="My Title"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            """<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>""",
        )
        opf = f"""<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title><dc:creator>Author A</dc:creator></metadata></package>"""
        z.writestr("content.opf", opf)


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "up" in result.output


def test_cli_up_dry_run(tmp_path, monkeypatch):
    # need config
    cfg = tmp_path / "config.toml"
    # simurg loads config from cwd, so chdir

    # create batch dir
    batch = tmp_path / "batch"
    batch.mkdir()
    epub = batch / "Test Book - Author A.epub"
    _make_epub(epub, title="Test Book")

    cfg_src = Path(__file__).resolve().parents[1] / "config.example.toml"
    if cfg_src.exists():
        cfg.write_text(cfg_src.read_text())
    else:
        cfg.write_text('[tracker.simurg]\nsession=""\n')

    # No scraper hits -> no selection prompt, deterministic + offline.
    from simurg.metadata import enricher

    monkeypatch.setattr(enricher, "search_all_scrapers", lambda inbuilt, session=None: [])

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # copy batch into isolated fs
        import shutil

        shutil.copytree(str(batch), "batch")
        Path("config.toml").write_text(cfg.read_text())
        result = runner.invoke(cli, ["up", "batch", "--dry-run"])
        # should succeed
        assert result.exit_code == 0
        assert "Found 1 ebook file" in result.output or "Processing" in result.output
        # dry-run does everything except the upload: real torrent is generated
        torrents = list(Path(".torrents").glob("*.torrent"))
        assert len(torrents) == 1
        assert "no uploads sent" in result.output


def test_cli_up_dry_run_stages_file(tmp_path, monkeypatch):
    """Dry-run still renames/stages the file (only the POST is skipped)."""
    from simurg.metadata import enricher

    monkeypatch.setattr(enricher, "search_all_scrapers", lambda inbuilt, session=None: [])

    batch = tmp_path / "batch"
    batch.mkdir()
    epub = batch / "Test Book - Author A.epub"
    _make_epub(epub, title="Test Book")

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[directory]\ndottorrents_dir = ".torrents"\nstaging_dir = ".staging"\n\n[tracker.simurg]\nsession=""\n'
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import shutil

        shutil.copytree(str(batch), "batch")
        Path("config.toml").write_text(cfg.read_text())
        result = runner.invoke(cli, ["up", "batch", "--dry-run"])
        assert result.exit_code == 0
        # original moved out of batch/ into .staging/ with the new name
        assert list(Path("batch").glob("*.epub")) == []
        staged = list(Path(".staging").glob("*.epub"))
        assert len(staged) == 1


def test_cli_up_single_scraper_result_auto_uses(tmp_path, monkeypatch):
    """Exactly one scraper result is used without prompting (dry-run too)."""
    from simurg.metadata import enricher

    monkeypatch.setattr(
        enricher,
        "search_all_scrapers",
        lambda inbuilt, session=None: [
            {
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "publisher": "Ace",
                "year": 1965,
                "_scraper": "openlibrary",
                "_fuzzy_title": 1.0,
                "_fuzzy_author": 1.0,
            },
        ],
    )

    batch = tmp_path / "batch"
    batch.mkdir()
    epub = batch / "Test Book - Author A.epub"
    _make_epub(epub, title="Test Book")

    cfg = tmp_path / "config.toml"
    cfg_src = Path(__file__).resolve().parents[1] / "config.example.toml"
    if cfg_src.exists():
        cfg.write_text(cfg_src.read_text())
    else:
        cfg.write_text('[tracker.simurg]\nsession=""\n')

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import shutil

        shutil.copytree(str(batch), "batch")
        Path("config.toml").write_text(cfg.read_text())
        # no input provided — must NOT hang on a prompt
        result = runner.invoke(cli, ["up", "batch", "--dry-run", "--no-rename"])
        assert result.exit_code == 0
        assert "Found 1 scraper result(s)" in result.output
        assert "Single scraper result — using 'openlibrary' automatically" in result.output
        assert "Enriched via scraper: Dune | Ace" in result.output


def test_cli_up_prompts_when_multiple_results(tmp_path, monkeypatch):
    """Multiple scraper results prompt for a choice, even in dry-run."""
    from simurg.metadata import enricher

    monkeypatch.setattr(
        enricher,
        "search_all_scrapers",
        lambda inbuilt, session=None: [
            {
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "publisher": "Ace",
                "year": 1965,
                "_scraper": "openlibrary",
                "_fuzzy_title": 1.0,
                "_fuzzy_author": 1.0,
            },
            {
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "publisher": "Ace Books",
                "_scraper": "googlebooks",
                "_fuzzy_title": 0.9,
                "_fuzzy_author": 0.9,
            },
        ],
    )

    batch = tmp_path / "batch"
    batch.mkdir()
    epub = batch / "Test Book - Author A.epub"
    _make_epub(epub, title="Test Book")

    cfg = tmp_path / "config.toml"
    cfg_src = Path(__file__).resolve().parents[1] / "config.example.toml"
    if cfg_src.exists():
        cfg.write_text(cfg_src.read_text())
    else:
        cfg.write_text('[tracker.simurg]\nsession=""\n')

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import shutil

        shutil.copytree(str(batch), "batch")
        Path("config.toml").write_text(cfg.read_text())
        result = runner.invoke(cli, ["up", "batch", "--dry-run", "--no-rename"], input="2\n")
        assert result.exit_code == 0
        assert "Found 2 scraper result(s)" in result.output
        assert "Choose a result" in result.output
        assert "Using metadata from 'googlebooks' scraper" in result.output


def test_prompt_scraper_selection_pick_number():
    """User picks result #2 by number."""
    from simurg.cli import _prompt_scraper_selection

    results = [
        {
            "title": "Dune",
            "authors": ["Frank Herbert"],
            "publisher": "Ace",
            "_scraper": "openlibrary",
            "_fuzzy_title": 1.0,
            "_fuzzy_author": 1.0,
        },
        {
            "title": "Dune",
            "authors": ["Frank Herbert"],
            "publisher": "Ace Books",
            "page_count": 896,
            "_scraper": "googlebooks",
            "_fuzzy_title": 1.0,
            "_fuzzy_author": 1.0,
        },
    ]
    inbuilt = {"title": "Dune", "authors": ["Frank Herbert"]}

    @click.command()
    def cmd():
        choice = _prompt_scraper_selection(results, inbuilt)
        click.echo(
            "RESULT:"
            + (
                "skip"
                if choice == "skip"
                else (str(None) if choice is None else choice["_scraper"])
            )
        )

    r = CliRunner().invoke(cmd, input="2\n")
    assert r.exit_code == 0
    assert "RESULT:googlebooks" in r.output
    assert "[1] openlibrary" in r.output and "[2] googlebooks" in r.output


def test_prompt_scraper_selection_inbuilt_only():
    from simurg.cli import _prompt_scraper_selection

    results = [
        {"title": "Dune", "_scraper": "openlibrary", "_fuzzy_title": 1.0, "_fuzzy_author": 1.0}
    ]
    inbuilt = {"title": "Dune", "authors": ["Frank Herbert"]}

    @click.command()
    def cmd():
        choice = _prompt_scraper_selection(results, inbuilt)
        click.echo(
            "RESULT:"
            + (
                "skip"
                if choice == "skip"
                else (str(None) if choice is None else choice["_scraper"])
            )
        )

    r = CliRunner().invoke(cmd, input="i\n")
    assert r.exit_code == 0
    assert "RESULT:None" in r.output


def test_prompt_scraper_selection_skip_and_abort():

    from simurg.cli import _prompt_scraper_selection

    results = [
        {"title": "Dune", "_scraper": "openlibrary", "_fuzzy_title": 1.0, "_fuzzy_author": 1.0}
    ]
    inbuilt = {"title": "Dune", "authors": ["Frank Herbert"]}

    @click.command()
    def cmd():
        choice = _prompt_scraper_selection(results, inbuilt)
        click.echo(
            "RESULT:"
            + (
                "skip"
                if choice == "skip"
                else (str(None) if choice is None else choice["_scraper"])
            )
        )

    r = CliRunner().invoke(cmd, input="s\n")
    assert r.exit_code == 0
    assert "RESULT:skip" in r.output

    r2 = CliRunner().invoke(cmd, input="a\n")
    assert r2.exit_code != 0  # abort raises SystemExit
    assert "Aborted" in r2.output


def test_prompt_field_merge_no_difference_returns_empty():
    from simurg.cli import _prompt_field_merge

    inbuilt = {"title": "Dune", "authors": ["Frank Herbert"]}
    scraper = {"title": "Dune", "authors": ["Frank Herbert"]}
    metadata = {
        "title": "Dune",
        "remaster_title": "Dune",
        "authors": ["Frank Herbert"],
        "album_desc": "",
        "description": "",
        "illustrators": [],
    }
    assert _prompt_field_merge(inbuilt, scraper, metadata) == {}


def test_prompt_field_merge_interactive_picks():
    from simurg.cli import _prompt_field_merge

    inbuilt = {
        "title": "Dune (Illustrated)",
        "authors": ["Frank Herbert"],
        "edition": "Illustrated Edition",
        "illustrators": ["John S"],
    }
    scraper = {"title": "Dune", "authors": ["Frank Herbert"], "publisher": "Ace"}
    metadata = {
        "title": "Dune",
        "remaster_title": "Dune",
        "authors": ["Frank Herbert"],
        "publisher": "Ace",
        "album_desc": "",
        "description": "",
        "illustrators": [],
    }

    @click.command()
    def cmd():
        click.echo("OVER:" + repr(_prompt_field_merge(inbuilt, scraper, metadata)))

    # fields prompted: title, publisher, edition, illustrators
    # title -> i (file), publisher -> k (keep), edition -> i (file), illustrators -> b (append)
    r = CliRunner().invoke(cmd, input="i\nk\ni\nb\n")
    assert r.exit_code == 0
    assert "File metadata vs chosen scraper" in r.output
    overrides = eval(r.output.split("OVER:")[1])
    assert overrides["title"] == "Dune (Illustrated)"
    assert overrides["edition"] == "Illustrated Edition"
    assert overrides["illustrators"] == ["John S"]
    assert "publisher" not in overrides


def test_prompt_field_merge_dry_run_keeps_defaults():
    from simurg.cli import _prompt_field_merge

    inbuilt = {
        "title": "Dune (Illustrated)",
        "authors": ["Frank Herbert"],
        "edition": "Illustrated Edition",
    }
    scraper = {"title": "Dune", "authors": ["Frank Herbert"]}
    metadata = {
        "title": "Dune",
        "remaster_title": "Dune",
        "authors": ["Frank Herbert"],
        "album_desc": "",
        "description": "",
        "illustrators": [],
    }
    assert _prompt_field_merge(inbuilt, scraper, metadata, dry_run=True) == {}


def test_apply_field_overrides():
    from simurg.cli import _apply_field_overrides

    md = {
        "title": "Dune",
        "remaster_title": "Dune",
        "authors": ["Frank Herbert"],
        "album_desc": "scraper desc",
        "description": "scraper desc",
        "illustrators": [],
    }
    _apply_field_overrides(
        md,
        {
            "title": "Dune (Illustrated)",
            "edition": "Illustrated Edition",
            "illustrators": ["John S"],
            "description": "file desc",
        },
    )
    assert md["title"] == "Dune"  # canonical stays edition-free
    assert md["remaster_title"] == "Dune (Illustrated)"
    assert md["edition"] == "Illustrated Edition"
    assert md["album_desc"] == "file desc"
    assert md["illustrators"] == ["John S"]


def test_cli_up_field_merge_applied_in_dry_run(tmp_path, monkeypatch):
    """Field-merge overrides are applied to the built metadata in the up flow."""
    from simurg import cli as cli_mod
    from simurg.metadata import enricher

    monkeypatch.setattr(
        enricher,
        "search_all_scrapers",
        lambda inbuilt, session=None: [
            {
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "publisher": "Ace",
                "_scraper": "openlibrary",
                "_fuzzy_title": 1.0,
                "_fuzzy_author": 1.0,
            }
        ],
    )
    monkeypatch.setattr(
        cli_mod,
        "_prompt_field_merge",
        lambda inbuilt, scraper, metadata, dry_run=False: {"title": "Dune (Illustrated)"},
    )

    batch = tmp_path / "batch"
    batch.mkdir()
    _make_epub(batch / "Test Book - Author A.epub", title="Test Book")

    cfg = tmp_path / "config.toml"
    cfg.write_text('[directory]\ndottorrents_dir = ".torrents"\n\n[tracker.simurg]\nsession=""\n')

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import shutil

        shutil.copytree(str(batch), "batch")
        Path("config.toml").write_text(cfg.read_text())
        # scraper selection: single result (auto), no further prompts
        result = runner.invoke(cli, ["up", "batch", "--dry-run", "--no-rename"])
        assert result.exit_code == 0
        assert "Applied 1 manual metadata override(s)" in result.output


def test_cli_up_field_merge_skipped_when_no_missing(tmp_path, monkeypatch):
    """When all required fields are present, the file-vs-scraper review is NOT forced."""
    from simurg import cli as cli_mod
    from simurg.metadata import enricher

    calls = []

    def _fake_merge(inbuilt, scraper, metadata, dry_run=False):
        calls.append((inbuilt, scraper))
        return {}

    monkeypatch.setattr(
        enricher,
        "search_all_scrapers",
        lambda inbuilt, session=None: [
            {
                "title": "Test Book",
                "authors": ["Author A"],
                "year": 2020,
                "publisher": "Ace",
                "_scraper": "openlibrary",
                "_fuzzy_title": 1.0,
                "_fuzzy_author": 1.0,
            }
        ],
    )
    monkeypatch.setattr(cli_mod, "_prompt_field_merge", _fake_merge)

    batch = tmp_path / "batch"
    batch.mkdir()
    _make_epub(batch / "Test Book - Author A.epub", title="Test Book")

    cfg = tmp_path / "config.toml"
    cfg.write_text('[directory]\ndottorrents_dir = ".torrents"\n\n[tracker.simurg]\nsession=""\n')

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import shutil

        shutil.copytree(str(batch), "batch")
        Path("config.toml").write_text(cfg.read_text())
        result = runner.invoke(cli, ["up", "batch", "--dry-run", "--no-rename"])
        assert result.exit_code == 0
        assert calls == []  # review never offered when nothing is missing
        assert "Review file metadata vs chosen scraper" not in result.output


def test_cli_up_no_files(tmp_path):
    runner = CliRunner()
    empty = tmp_path / "empty"
    empty.mkdir()
    # need config
    import os

    cfg = tmp_path / "config.toml"
    cfg.write_text('[tracker.simurg]\nsession=""\n')
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["up", str(empty), "--dry-run"])
        assert result.exit_code != 0
        assert "No ebooks" in result.output or "No magazines" in result.output
    finally:
        os.chdir(old_cwd)


def test_cli_health():
    runner = CliRunner()
    result = runner.invoke(cli, ["health"])
    assert result.exit_code == 0
    assert "Deps" in result.output or "Python" in result.output


def test_cli_checkconf():
    runner = CliRunner()
    # need at least empty config to not crash
    import os
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "config.toml").write_text('[tracker.simurg]\nsession=""\n')
        old = Path.cwd()
        try:
            os.chdir(td)
            result = runner.invoke(cli, ["checkconf"])
            assert result.exit_code == 0
            assert "checkconf" in result.output.lower() or "Config" in result.output
        finally:
            os.chdir(old)


def test_cli_up_url_flag_uses_pasted_url(tmp_path, monkeypatch):
    """--url routes to the matching scraper and auto-uses the result (dry-run)."""
    from simurg.metadata import enricher

    # Avoid any real network: short-circuit the URL router + cross-source fill.
    monkeypatch.setattr(
        enricher,
        "search_by_url",
        lambda url, session=None: {
            "title": "Pasted Book",
            "authors": ["Pasted Author"],
            "publisher": "Pasted Press",
            "year": 2021,
            "isbn": "9780441172719",
            "_scraper": "openlibrary",
            "_fuzzy_title": 1.0,
            "_fuzzy_author": 0.0,
        },
    )
    monkeypatch.setattr(enricher, "search_all_by_isbn", lambda isbn, session=None: [])

    batch = tmp_path / "batch"
    batch.mkdir()
    epub = batch / "Test Book - Author A.epub"
    _make_epub(epub, title="Test Book")

    cfg = tmp_path / "config.toml"
    cfg_src = Path(__file__).resolve().parents[1] / "config.example.toml"
    if cfg_src.exists():
        cfg.write_text(cfg_src.read_text())
    else:
        cfg.write_text('[tracker.simurg]\nsession=""\n')

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import shutil

        shutil.copytree(str(batch), "batch")
        Path("config.toml").write_text(cfg.read_text())
        result = runner.invoke(
            cli,
            [
                "up",
                "batch",
                "--dry-run",
                "--no-rename",
                "--no-review",
                "--url",
                "https://openlibrary.org/books/OL1",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Resolved via 'openlibrary'" in result.output
        assert "Enriched via scraper: Pasted Book | Pasted Press" in result.output


def test_cli_up_url_flag_unsupported_falls_through(tmp_path, monkeypatch):
    """An unresolvable --url falls back to the normal (empty) enrichment."""
    from simurg.metadata import enricher

    monkeypatch.setattr(enricher, "search_by_url", lambda url, session=None: None)
    monkeypatch.setattr(enricher, "search_all_scrapers", lambda inbuilt, session=None: [])

    batch = tmp_path / "batch"
    batch.mkdir()
    epub = batch / "Test Book - Author A.epub"
    _make_epub(epub, title="Test Book")

    cfg = tmp_path / "config.toml"
    cfg_src = Path(__file__).resolve().parents[1] / "config.example.toml"
    if cfg_src.exists():
        cfg.write_text(cfg_src.read_text())
    else:
        cfg.write_text('[tracker.simurg]\nsession=""\n')

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import shutil

        shutil.copytree(str(batch), "batch")
        Path("config.toml").write_text(cfg.read_text())
        result = runner.invoke(
            cli,
            [
                "up",
                "batch",
                "--dry-run",
                "--no-rename",
                "--no-review",
                "--url",
                "https://example.com/book",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Could not resolve that URL" in result.output
        assert "No scraper results found" in result.output
