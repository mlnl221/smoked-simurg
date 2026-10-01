"""Blank/placeholder cover detection (Magnolia Summer incident, 2026-09-30)."""

import pytest

from simurg.images.validate import (
    BLANK_COVER_SHA256,
    is_blank_cover,
    is_valid_cover,
)

pytestmark = pytest.mark.skipif(
    pytest.importorskip("PIL", reason="Pillow required") is None, reason="Pillow required"
)


def _make_jpg(path, color=None, size=(300, 450), noise=False):
    import random

    from PIL import Image

    im = Image.new("RGB", size, color or "white")
    if noise:
        px = im.load()
        for x in range(size[0]):
            for y in range(size[1]):
                v = 200 + random.randint(0, 30)
                px[x, y] = (v, v, v)
    im.save(path, "JPEG", quality=92)
    return str(path)


def test_solid_white_rejected_as_blank(tmp_path):
    p = _make_jpg(tmp_path / "white.jpg", color="white")
    blank, _ = is_blank_cover(p)
    assert blank is True
    assert is_valid_cover(p)[0] is False


def test_near_white_noise_rejected_as_blank(tmp_path):
    p = _make_jpg(tmp_path / "noise.jpg", noise=True)
    blank, _ = is_blank_cover(p)
    assert blank is True
    assert is_valid_cover(p)[0] is False


def test_solid_gray_placeholder_rejected(tmp_path):
    p = _make_jpg(tmp_path / "gray.jpg", color=(157, 165, 175))
    blank, _ = is_blank_cover(p)
    assert blank is True
    assert is_valid_cover(p)[0] is False


def test_real_cover_like_accepted(tmp_path):
    from PIL import Image

    p = str(tmp_path / "real.jpg")
    im = Image.new("RGB", (333, 500), "navy")
    im.paste(Image.new("RGB", (333, 250), "crimson"), (0, 125))
    im.save(p, "JPEG", quality=92)
    blank, _ = is_blank_cover(p)
    assert blank is False
    assert is_valid_cover(p) == (True, "ok")


def test_known_hashes_pinned():
    assert "ff0826ab8a59744b913a3c92211d732f4dc27a91affc7af6de3f7a711be18008" in (
        BLANK_COVER_SHA256
    )
    assert "d6939d785ff680cb4c17d568ebd586894f66af0f55649cd2611ea95a58f9e167" in (
        BLANK_COVER_SHA256
    )


def test_tiny_and_missing_still_rejected(tmp_path):
    p = tmp_path / "tiny.jpg"
    p.write_bytes(b"x" * 100)
    assert is_valid_cover(str(p))[0] is False
    assert is_valid_cover(str(tmp_path / "nope.jpg"))[0] is False
