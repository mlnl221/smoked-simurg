"""Validate downloaded cover images."""

from __future__ import annotations

import os

MIN_COVER_BYTES = 5000
MIN_COVER_DIM = 100


def is_valid_cover(
    path: str, min_bytes: int = MIN_COVER_BYTES, min_dim: int = MIN_COVER_DIM
) -> tuple[bool, str]:
    """Check cover file exists, meets size and dimension minimums."""
    try:
        if not path or not os.path.isfile(path):
            return (False, f"missing file: {path}")
        size = os.path.getsize(path)
        if size < min_bytes:
            return (False, f"empty/tiny file: {size} < {min_bytes} bytes")
        try:
            from PIL import Image
        except ImportError:
            return (True, "ok")
        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                width, height = im.size
                if width < min_dim or height < min_dim:
                    return (
                        False,
                        f"dimensions too small: {width}x{height} < {min_dim}px",
                    )
        except Exception as e:
            return (False, f"invalid image: {e}")
        return (True, "ok")
    except Exception as e:
        return (False, f"invalid image: {e}")


def validate_downloaded_cover(path: str | None) -> tuple[str | None, str | None]:
    """Return (path, None) if valid else (None, reason)."""
    if not path:
        return (None, "missing path")
    valid, reason = is_valid_cover(path)
    if valid:
        return (path, None)
    return (None, reason)
