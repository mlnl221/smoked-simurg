from pathlib import Path

import pytest

from simurg.config import load_config
from simurg.uploader import client as client_mod
from simurg.uploader.client import (
    copy_to_seed_dir,
    masked_url,
    maybe_push_to_client,
    parse_qbit_url,
)


def test_parse_qbit_url():
    base, user, pw = parse_qbit_url("qbittorrent+http://admin:secret@192.168.1.10:8080")
    assert base == "http://192.168.1.10:8080"
    assert user == "admin"
    assert pw == "secret"


def test_parse_qbit_url_no_auth():
    base, user, pw = parse_qbit_url("qbittorrent+http://192.168.1.10:8080/")
    assert base == "http://192.168.1.10:8080/"
    assert user is None
    assert pw is None


def test_parse_qbit_url_bad_scheme():
    with pytest.raises(ValueError):
        parse_qbit_url("transmission+http://127.0.0.1:9091")


def test_masked_url_hides_creds():
    assert "secret" not in masked_url("qbittorrent+http://admin:secret@host:8080")


def test_masked_url_invalid_port_leaks_nothing():
    # parsed.port raises on bad port; fallback must never echo auth
    out = masked_url("qbittorrent+http://admin:s3cret@host:badport")
    assert "admin" not in out
    assert "s3cret" not in out


def test_masked_url_garbage():
    assert masked_url("not a url") == "****"


def test_masked_url_no_auth_passthrough():
    url = "qbittorrent+http://192.168.1.10:8080/path"
    assert masked_url(url) == url


def test_parse_qbit_url_username_only():
    base, user, pw = parse_qbit_url("qbittorrent+http://admin@host:8080")
    assert base == "http://host:8080"
    assert user == "admin"
    assert pw is None


def test_parse_qbit_url_encoded_and_subpath():
    base, user, pw = parse_qbit_url("qbittorrent+http://us%40er:p%40ss@host:8080/qbt")
    assert base == "http://host:8080/qbt"
    assert user == "us@er"
    assert pw == "p@ss"


def test_copy_to_seed_dir_file(tmp_path: Path):
    src = tmp_path / "book.epub"
    src.write_bytes(b"data")
    dest = copy_to_seed_dir(src, str(tmp_path / "seed"))
    assert dest.read_bytes() == b"data"


def test_copy_to_seed_dir_dir(tmp_path: Path):
    src = tmp_path / "pack"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.epub").write_bytes(b"x")
    dest = copy_to_seed_dir(src, str(tmp_path / "seed"))
    assert (dest / "sub" / "f.epub").read_bytes() == b"x"


def test_copy_to_seed_dir_windows_path(tmp_path: Path, monkeypatch):
    import subprocess as sp

    src = tmp_path / "book (2021) [123].epub"
    src.write_bytes(b"data")
    calls = []

    class Proc:
        def __init__(self, out="", rc=0):
            self.stdout = out
            self.stderr = ""
            self.returncode = rc

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "wslpath":
            return Proc(out="C:\\x\\book.epub\n")
        return Proc(out="1 file(s) copied.")

    monkeypatch.setattr(sp, "run", fake_run)
    dest = copy_to_seed_dir(src, "W:\\Downloads\\Torrents\\qbit")
    assert dest.name == src.name
    assert calls[1][:3] == ["cmd.exe", "/c", "copy"]
    assert calls[1][-1] == "W:\\Downloads\\Torrents\\qbit\\"


def _write_cfg(tmp_path: Path, body: str):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(body)
    return load_config(cfg_file)


def test_push_disabled_noop(tmp_path: Path, monkeypatch):
    _write_cfg(tmp_path, '[client]\nenabled = false\ntorrent_client = ""\n')
    called = []

    class NoClient:
        def __init__(self, url):
            called.append(url)
            raise AssertionError("must not construct client when disabled")

    monkeypatch.setattr(client_mod, "QBittorrentClient", NoClient)
    assert maybe_push_to_client(tmp_path / "a.torrent", tmp_path / "b.epub") is False
    assert called == []


def test_push_dry_run_noop(tmp_path: Path):
    _write_cfg(
        tmp_path,
        '[client]\nenabled = true\ntorrent_client = "qbittorrent+http://u:p@h:8080"\n'
        'save_path = "/dl"\nlocal_path = "/mnt/nas"\n',
    )
    assert maybe_push_to_client(tmp_path / "a.torrent", tmp_path / "b.epub", dry_run=True) is False


def test_push_success(tmp_path: Path, monkeypatch):
    seed = tmp_path / "seed"
    _write_cfg(
        tmp_path,
        '[client]\nenabled = true\ntorrent_client = "qbittorrent+http://u:p@h:8080"\n'
        f'save_path = "/dl"\nlocal_path = "{seed}"\ncategory = "simurg"\n',
    )
    content = tmp_path / "book.epub"
    content.write_bytes(b"book")
    torrent = tmp_path / "book.torrent"
    torrent.write_bytes(b"torrent-bytes")

    seen = {}

    class FakeQbit:
        def __init__(self, url):
            seen["url"] = url

        def add_to_downloader(self, save_path, blob, is_paused=False, category=""):
            seen.update(save_path=save_path, blob=blob, paused=is_paused, category=category)
            return True

    monkeypatch.setattr(client_mod, "QBittorrentClient", FakeQbit)
    assert maybe_push_to_client(torrent, content) is True
    assert (seed / "book.epub").read_bytes() == b"book"
    assert seen["save_path"] == "/dl"
    assert seen["blob"] == b"torrent-bytes"
    assert seen["category"] == "simurg"


def test_push_client_failure_nonfatal(tmp_path: Path, monkeypatch):
    seed = tmp_path / "seed"
    _write_cfg(
        tmp_path,
        '[client]\nenabled = true\ntorrent_client = "qbittorrent+http://u:p@h:8080"\n'
        f'save_path = "/dl"\nlocal_path = "{seed}"\n',
    )
    content = tmp_path / "book.epub"
    content.write_bytes(b"book")
    torrent = tmp_path / "book.torrent"
    torrent.write_bytes(b"t")

    class FailQbit:
        def __init__(self, url):
            pass

        def add_to_downloader(self, *a, **k):
            raise RuntimeError("conn refused")

    monkeypatch.setattr(client_mod, "QBittorrentClient", FailQbit)
    assert maybe_push_to_client(torrent, content) is False  # no raise


def test_copy_to_seed_dir_windows_failure(tmp_path: Path, monkeypatch):
    import subprocess as sp

    src = tmp_path / "book.epub"
    src.write_bytes(b"data")

    class Proc:
        stdout = "C:\\x\\book.epub\n"
        stderr = "Access denied."
        returncode = 1

    monkeypatch.setattr(sp, "run", lambda cmd, **kw: Proc())
    with pytest.raises(RuntimeError):
        copy_to_seed_dir(src, "W:\\seed")


def test_copy_to_seed_dir_windows_timeout_bounded(tmp_path: Path, monkeypatch):
    import subprocess as sp

    from simurg.uploader.client import _COPY_TIMEOUT_FILE

    src = tmp_path / "book.epub"
    src.write_bytes(b"data")
    seen = {}

    class Proc:
        stdout = "C:\\x\\book.epub\n"
        stderr = ""
        returncode = 0

    def fake_run(cmd, **kw):
        seen[cmd[0]] = kw.get("timeout")
        return Proc()

    monkeypatch.setattr(sp, "run", fake_run)
    copy_to_seed_dir(src, "W:\\seed")
    assert seen["wslpath"] == 15
    assert seen["cmd.exe"] == _COPY_TIMEOUT_FILE


def test_copy_to_seed_dir_windows_dir_uses_xcopy(tmp_path: Path, monkeypatch):
    import subprocess as sp

    src = tmp_path / "pack"
    src.mkdir()
    (src / "f.epub").write_bytes(b"x")
    calls = []

    class Proc:
        stdout = "C:\\x\\pack\n"
        stderr = ""
        returncode = 0

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(sp, "run", fake_run)
    dest = copy_to_seed_dir(src, "W:\\seed")
    assert dest.name == "pack"
    assert calls[1][2] == "xcopy"
    assert "/E" in calls[1] and "/I" in calls[1] and "/Y" in calls[1]


def test_copy_to_seed_dir_windows_wslpath_failure(tmp_path: Path, monkeypatch):
    import subprocess as sp

    src = tmp_path / "book.epub"
    src.write_bytes(b"data")

    def fake_run(cmd, **kw):
        raise sp.CalledProcessError(1, cmd)

    monkeypatch.setattr(sp, "run", fake_run)
    with pytest.raises(sp.CalledProcessError):
        copy_to_seed_dir(src, "W:\\seed")


def test_copy_to_seed_dir_unc_path_uses_interop(tmp_path: Path, monkeypatch):
    import subprocess as sp

    src = tmp_path / "book.epub"
    src.write_bytes(b"data")
    calls = []

    class Proc:
        stdout = "C:\\x\\book.epub\n"
        stderr = ""
        returncode = 0

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(sp, "run", fake_run)
    dest = copy_to_seed_dir(src, "\\\\NAS\\share\\qbit")
    assert dest.name == "book.epub"
    assert calls[1][0] == "cmd.exe"


def test_copy_to_seed_dir_skips_identical(tmp_path: Path):
    src = tmp_path / "book.epub"
    src.write_bytes(b"12345678")
    seed = tmp_path / "seed"
    first = copy_to_seed_dir(src, str(seed))
    first.write_bytes(b"XXXXXXXX")  # same size, different bytes
    second = copy_to_seed_dir(src, str(seed))
    assert second.read_bytes() == b"XXXXXXXX"  # untouched: skip works


def test_add_to_downloader_no_client():
    from simurg.uploader.client import QBittorrentClient

    c = QBittorrentClient.__new__(QBittorrentClient)
    c.client = None
    assert c.add_to_downloader("/dl", b"t") is False


def test_add_to_downloader_empty_category_passes_none():
    from simurg.uploader.client import QBittorrentClient

    seen = {}

    class Inner:
        def torrents_add(self, **kw):
            seen.update(kw)

    c = QBittorrentClient.__new__(QBittorrentClient)
    c.client = Inner()
    assert c.add_to_downloader("/dl", b"t", category="") is True
    assert seen["category"] is None
    assert seen["save_path"] == "/dl"


def test_add_to_downloader_error_false():
    from simurg.uploader.client import QBittorrentClient

    class Inner:
        def torrents_add(self, **kw):
            raise RuntimeError("boom")

    c = QBittorrentClient.__new__(QBittorrentClient)
    c.client = Inner()
    assert c.add_to_downloader("/dl", b"t") is False


def test_login_missing_lib_returns_none(monkeypatch):
    import sys

    from simurg.uploader.client import QBittorrentClient

    monkeypatch.setitem(sys.modules, "qbittorrentapi", None)
    c = QBittorrentClient.__new__(QBittorrentClient)
    c.base_url, c.username, c.password = "http://h:8080", None, None
    assert c.login() is None


def test_push_missing_paths_skipped(tmp_path: Path):
    _write_cfg(
        tmp_path,
        '[client]\nenabled = true\ntorrent_client = "qbittorrent+http://u:p@h:8080"\n',
    )
    assert maybe_push_to_client(tmp_path / "a.torrent", tmp_path / "b.epub") is False


def test_push_unreadable_torrent_false(tmp_path: Path, monkeypatch):
    seed = tmp_path / "seed"
    _write_cfg(
        tmp_path,
        '[client]\nenabled = true\ntorrent_client = "qbittorrent+http://u:p@h:8080"\n'
        f'save_path = "/dl"\nlocal_path = "{seed}"\n',
    )
    content = tmp_path / "book.epub"
    content.write_bytes(b"book")
    assert maybe_push_to_client(tmp_path / "missing.torrent", content) is False


def _upload_harness(tmp_path: Path, monkeypatch):
    """Stub generate_torrent + site for prepare_and_upload hook tests."""
    from types import SimpleNamespace

    from simurg.uploader import upload as upload_mod

    content = tmp_path / "book.epub"
    content.write_bytes(b"book")
    torrent_path = tmp_path / "book - SIM.torrent"
    torrent_path.write_bytes(b"torrent-bytes")
    fake_torrent = SimpleNamespace(comment=None, write=lambda *a, **k: None)
    monkeypatch.setattr(
        upload_mod, "generate_torrent", lambda site, fp: (str(torrent_path), fake_torrent)
    )
    monkeypatch.setattr(upload_mod, "compile_data_new_publication", lambda *a, **k: {"title": "t"})
    pushed = []
    monkeypatch.setattr(
        upload_mod, "maybe_push_to_client", lambda *a, **k: pushed.append((a, k)) or True
    )

    class FakeSite:
        authkey = "dummy"
        base_url = "https://simurg.world"

        def __init__(self, exc=None):
            self.exc = exc

        async def upload(self, data, files):
            if self.exc:
                raise self.exc
            return (111, 222)

    return upload_mod, content, torrent_path, pushed, FakeSite


def test_upload_success_pushes(tmp_path: Path, monkeypatch):
    upload_mod, content, torrent_path, pushed, FakeSite = _upload_harness(tmp_path, monkeypatch)
    tid, gid, tpath = upload_mod.prepare_and_upload(
        FakeSite(), content, None, {"release_desc": "d"}, None
    )
    assert (tid, gid) == (111, 222)
    assert tpath == str(torrent_path)
    assert len(pushed) == 1
    assert pushed[0][0][:2] == (str(torrent_path), content)


def test_upload_dry_run_skips_push(tmp_path: Path, monkeypatch):
    upload_mod, content, _tpath, pushed, FakeSite = _upload_harness(tmp_path, monkeypatch)
    tid, _gid, _tp = upload_mod.prepare_and_upload(
        FakeSite(), content, None, {"release_desc": "d"}, None, dry_run=True
    )
    assert tid is None
    assert pushed == []


def test_upload_dupe_pushes(tmp_path: Path, monkeypatch):
    upload_mod, content, _tpath, pushed, FakeSite = _upload_harness(tmp_path, monkeypatch)
    site = FakeSite(
        exc=RuntimeError("fail: exact same torrent file already exists (torrentid=55 releaseid=66)")
    )
    tid, gid, _tp = upload_mod.prepare_and_upload(site, content, None, {"release_desc": "d"}, None)
    assert (tid, gid) == (55, 66)
    assert len(pushed) == 1


def test_upload_failure_skips_push(tmp_path: Path, monkeypatch):
    upload_mod, content, _tpath, pushed, FakeSite = _upload_harness(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    site = FakeSite(exc=RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        upload_mod.prepare_and_upload(site, content, None, {"release_desc": "d"}, None)
    assert pushed == []
    assert (tmp_path / ".failed" / "book.json").exists()
