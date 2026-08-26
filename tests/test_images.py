from pathlib import Path

import pytest

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from simurg.images.base import downscale_image


@pytest.mark.skipif(not HAS_PIL, reason="Pillow not installed")
def test_downscale_large_png_to_jpg(tmp_path):
    # create 1000x800 png
    p = tmp_path / "large.png"
    img = Image.new("RGB", (1000, 800), color="red")
    img.save(p, "PNG")
    out = downscale_image(str(p))
    assert out != str(p)  # should create new jpg
    assert out.endswith(".jpg")
    im2 = Image.open(out)
    assert max(im2.size) == 500
    assert im2.mode == "RGB"
    Path(out).unlink(missing_ok=True)


@pytest.mark.skipif(not HAS_PIL, reason="Pillow not installed")
def test_downscale_small_no_resize(tmp_path):
    p = tmp_path / "small.jpg"
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(p, "JPEG")
    out = downscale_image(str(p))
    # small jpg should be unchanged (no resize, no convert)
    assert out == str(p)


@pytest.mark.skipif(not HAS_PIL, reason="Pillow not installed")
def test_downscale_rgba_flatten(tmp_path):
    p = tmp_path / "rgba.png"
    img = Image.new("RGBA", (600, 600), color=(255, 0, 0, 128))
    img.save(p, "PNG")
    out = downscale_image(str(p))
    assert out.endswith(".jpg")
    im2 = Image.open(out)
    assert im2.mode == "RGB"
    Path(out).unlink(missing_ok=True)


def test_downscale_no_pillow(monkeypatch, tmp_path):
    # simulate missing PIL
    import simurg.images.base as base

    monkeypatch.setattr(base, "Image", None)
    p = tmp_path / "any.jpg"
    p.write_bytes(b"fake")
    out = base.downscale_image(str(p))
    assert out == str(p)
