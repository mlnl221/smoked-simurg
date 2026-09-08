import pytest

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def test_rejects_missing_file():
    from simurg.images.validate import is_valid_cover

    valid, reason = is_valid_cover("/nonexistent/x.jpg")
    assert valid is False
    assert "missing" in reason


def test_rejects_empty_and_tiny(tmp_path):
    from simurg.images.validate import is_valid_cover

    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    valid, reason = is_valid_cover(str(empty))
    assert valid is False
    assert "empty/tiny" in reason

    tiny = tmp_path / "tiny.jpg"
    tiny.write_bytes(b"x" * 100)
    valid, reason = is_valid_cover(str(tiny))
    assert valid is False
    assert "empty/tiny" in reason


@pytest.mark.skipif(not HAS_PIL, reason="Pillow not installed")
def test_rejects_html_as_jpg(tmp_path):
    from simurg.images.validate import MIN_COVER_BYTES, is_valid_cover

    p = tmp_path / "fake.jpg"
    p.write_bytes(b"<html>not an image</html>" + b"x" * (MIN_COVER_BYTES + 100))
    valid, reason = is_valid_cover(str(p))
    assert valid is False
    assert "invalid image" in reason


@pytest.mark.skipif(not HAS_PIL, reason="Pillow not installed")
def test_rejects_1px_gif(tmp_path):
    from simurg.images.validate import is_valid_cover, validate_downloaded_cover

    p = tmp_path / "tiny.gif"
    img = Image.new("RGB", (1, 1), color="red")
    img.save(p, "GIF")
    valid, reason = is_valid_cover(str(p), min_bytes=10)
    assert valid is False
    assert "dimensions too small" in reason

    path, err = validate_downloaded_cover(None)
    assert path is None
    assert err


@pytest.mark.skipif(not HAS_PIL, reason="Pillow not installed")
def test_accepts_real_cover(tmp_path):
    import os

    from simurg.images.validate import MIN_COVER_BYTES, is_valid_cover, validate_downloaded_cover

    p = tmp_path / "cover.jpg"
    raw = os.urandom(400 * 600 * 3)
    img = Image.frombytes("RGB", (400, 600), raw)
    img.save(p, "JPEG", quality=95)
    if p.stat().st_size <= MIN_COVER_BYTES:
        raw = os.urandom(800 * 1200 * 3)
        img = Image.frombytes("RGB", (800, 1200), raw)
        img.save(p, "JPEG", quality=95)
    assert p.stat().st_size > MIN_COVER_BYTES
    valid, reason = is_valid_cover(str(p))
    assert valid is True
    assert reason == "ok"
    out, err = validate_downloaded_cover(str(p))
    assert out == str(p)
    assert err is None


def test_download_rejects_non_image_contenttype(monkeypatch):
    import simurg.cli as cli

    class FakeResp:
        status_code = 200
        headers = {"Content-Type": "text/html"}

        def iter_content(self, *args, **kwargs):
            yield b"x" * 6000

    monkeypatch.setattr("simurg.cli.requests.get", lambda *_, **__: FakeResp())
    assert cli._download_url_to_temp("http://example.com/cover.jpg") is None


def test_download_rejects_tiny_body(monkeypatch):
    import simurg.cli as cli

    class FakeResp:
        status_code = 200
        headers = {"Content-Type": "image/jpeg"}

        def iter_content(self, *args, **kwargs):
            yield b"x" * 100

    monkeypatch.setattr("simurg.cli.requests.get", lambda *_, **__: FakeResp())
    assert cli._download_url_to_temp("http://example.com/cover.jpg") is None


def test_prompt_manual_cover_dry_run_returns_none():
    import simurg.cli as cli

    assert cli._prompt_manual_cover("T", ["A"], dry_run=True) is None


def test_prompt_manual_cover_skip(monkeypatch):

    import simurg.cli as cli

    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: "s")
    assert cli._prompt_manual_cover("T", ["A"]) is None

    # [a]bort now skips the file instead of aborting the batch
    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: "a")
    assert cli._prompt_manual_cover("T", ["A"]) == "skip"

    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: "d")
    assert cli._prompt_manual_cover("T", ["A"]) == "delete"


def test_confirm_keep_on_enter(tmp_path, monkeypatch):
    import simurg.cli as cli

    tmp = tmp_path / "cover.jpg"
    tmp.write_bytes(b"x" * 6000)
    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: "")
    url, out = cli._confirm_rehosted_cover("http://host/img.jpg", str(tmp))
    assert (url, out) == ("http://host/img.jpg", str(tmp))
    assert tmp.exists()


def test_confirm_skip_unlinks(tmp_path, monkeypatch):
    import simurg.cli as cli

    tmp = tmp_path / "cover.jpg"
    tmp.write_bytes(b"x" * 6000)
    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: "s")
    assert cli._confirm_rehosted_cover("http://host/img.jpg", str(tmp)) == (None, None)
    assert not tmp.exists()


def test_confirm_skip_preserves_embedded(tmp_path, monkeypatch):
    import simurg.cli as cli

    tmp = tmp_path / "cover.jpg"
    tmp.write_bytes(b"x" * 6000)
    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: "s")
    assert cli._confirm_rehosted_cover("http://host/img.jpg", str(tmp), str(tmp)) == (None, None)
    assert tmp.exists()


def test_confirm_abort_skips_file(tmp_path, monkeypatch):
    import simurg.cli as cli

    tmp = tmp_path / "cover.jpg"
    tmp.write_bytes(b"x" * 6000)
    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: "a")
    # [a]bort now skips the file instead of aborting the batch
    assert cli._confirm_rehosted_cover("http://host/img.jpg", str(tmp)) == "skip"

    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: "d")
    assert cli._confirm_rehosted_cover("http://host/img.jpg", str(tmp)) == "delete"


def test_confirm_dry_run_passthrough(tmp_path, monkeypatch):
    import click

    import simurg.cli as cli

    tmp = tmp_path / "cover.jpg"
    tmp.write_bytes(b"x" * 6000)

    def _fail(*_, **__):
        raise AssertionError("must not prompt in dry-run")

    monkeypatch.setattr(click, "prompt", _fail)
    url, out = cli._confirm_rehosted_cover("http://host/img.jpg", str(tmp), dry_run=True)
    assert (url, out) == ("http://host/img.jpg", str(tmp))
    assert tmp.exists()


def test_confirm_u_rehosts_and_keeps(tmp_path, monkeypatch):
    import types

    import simurg.cli as cli

    old = tmp_path / "old.jpg"
    old.write_bytes(b"x" * 6000)
    new_tmp = tmp_path / "new.jpg"
    new_tmp.write_bytes(b"")
    answers = iter(["u", "http://new/x.jpg", ""])
    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: next(answers))
    monkeypatch.setattr("simurg.cli._download_url_to_temp", lambda *_, **__: str(new_tmp))
    monkeypatch.setattr(
        "simurg.config.get_config",
        lambda: types.SimpleNamespace(image={"cover_uploader": "ptscreens"}),
    )

    class StubUploader:
        def upload_file(self, path):
            return ("http://host/new.jpg", None)

    monkeypatch.setattr("simurg.images.get_uploader", lambda *_, **__: StubUploader)
    url, out = cli._confirm_rehosted_cover("http://host/img.jpg", str(old))
    assert url == "http://host/new.jpg"
    assert out == str(new_tmp)
    assert not old.exists()
    assert new_tmp.exists()


def test_confirm_u_invalid_keeps_current(tmp_path, monkeypatch):
    import simurg.cli as cli

    old = tmp_path / "old.jpg"
    old.write_bytes(b"x" * 6000)
    answers = iter(["u", "http://bad/x.jpg", ""])
    monkeypatch.setattr("simurg.cli.click.prompt", lambda *_, **__: next(answers))
    monkeypatch.setattr("simurg.cli._download_url_to_temp", lambda *_, **__: None)
    url, out = cli._confirm_rehosted_cover("http://host/img.jpg", str(old))
    assert (url, out) == ("http://host/img.jpg", str(old))
    assert old.exists()
