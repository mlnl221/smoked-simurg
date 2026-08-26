from pathlib import Path

from torf import Torrent

from simurg.uploader.torrent import generate_torrent
from simurg.uploader.upload import _stamp_torrent_comment


class DummySite:
    announce = "https://tracker.simurg.world/abcd/announce"
    dot_torrents_dir = ""
    site_string = "SIM"


def test_generate_torrent_single_file(tmp_path):
    # create dummy ebook file
    f = tmp_path / "book.epub"
    f.write_bytes(b"fake epub content " * 100)
    out_dir = tmp_path / "torrents"
    site = DummySite()
    site.dot_torrents_dir = str(out_dir)
    tpath, _t = generate_torrent(site, f)
    assert Path(tpath).exists()
    assert tpath.endswith(" - SIM.torrent")
    # verify with torf
    from torf import Torrent

    tor = Torrent.read(tpath)
    assert tor.private is True
    assert tor.source == "SIM"
    assert tor.piece_size == 32768
    assert tor.name == "book.epub"
    assert site.announce in tor.trackers[0][0]


def test_generate_torrent_creates_dir(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello")
    out_dir = tmp_path / "newdir" / "torrents"
    site = DummySite()
    site.dot_torrents_dir = str(out_dir)
    tpath, _ = generate_torrent(site, f)
    assert out_dir.exists()
    assert Path(tpath).exists()


def test_stamp_torrent_comment(tmp_path):
    f = tmp_path / "book.epub"
    f.write_bytes(b"fake epub content " * 100)
    out_dir = tmp_path / "torrents"
    site = DummySite()
    site.dot_torrents_dir = str(out_dir)
    site.base_url = "https://simurg.world"
    tpath, torrent = generate_torrent(site, f)
    url = _stamp_torrent_comment(tpath, torrent, site.base_url, 12345)
    assert url == "https://simurg.world/torrents.php?torrentid=12345"
    # Re-read and confirm the comment is present, but info-hash fields are unchanged.
    reread = Torrent.read(tpath)
    assert reread.comment == "https://simurg.world/torrents.php?torrentid=12345"
    assert reread.private is True
    assert reread.source == "SIM"
    assert reread.piece_size == 32768
    assert reread.name == "book.epub"
