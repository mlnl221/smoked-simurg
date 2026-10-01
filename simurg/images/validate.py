"""Validate downloaded cover images."""

from __future__ import annotations

import hashlib
import os
import statistics

MIN_COVER_BYTES = 5000
MIN_COVER_DIM = 100

# Grayscale pixel standard deviation below this means a blank/placeholder
# image (solid white, near-white noise, generic "no cover" gray). Calibrated
# 2026-09-30: real covers in batch scored >=27, placeholders scored 9-16.
BLANK_COVER_MAX_STDEV = 20.0

# SHA-256 of exact bytes of known placeholder images (bypass pixel stats,
# e.g. when ptscreens serves the file bit-identical). Append new hashes here
# when a blank slips through again.
BLANK_COVER_SHA256 = {
    # White blank served by Google Books frontcover for unscanned books
    # (Magnolia Summer / Keepers / Finders / Mountain Moonlight, 2026-09-30).
    "ff0826ab8a59744b913a3c92211d732f4dc27a91affc7af6de3f7a711be18008",
    # Gray generic placeholder (Rules of Contact, 2026-09-30).
    "d6939d785ff680cb4c17d568ebd586894f66af0f55649cd2611ea95a58f9e167",
}


def file_sha256(path: str) -> str | None:
    """Return hex sha256 of file bytes, or None on error."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def is_blank_cover(path: str) -> tuple[bool, str]:
    """True when the image is a blank/placeholder (no real cover art).

    Checks the exact-byte blocklist first, then pixel variance: real covers
    always carry contrast, blanks are near-flat.
    """
    digest = file_sha256(path)
    if digest and digest in BLANK_COVER_SHA256:
        return (True, f"known placeholder hash {digest[:12]}")
    try:
        from PIL import Image
    except ImportError:
        return (False, "ok")
    try:
        with Image.open(path) as im:
            im.load()
            gray = im.convert("L")
            px = list(gray.getdata())
        if not px:
            return (True, "no pixels")
        stdev = statistics.pstdev(px)
        if stdev < BLANK_COVER_MAX_STDEV:
            return (True, f"blank (pixel stdev {stdev:.1f})")
    except Exception as e:
        return (False, f"unreadable: {e}")
    return (False, "ok")


def is_valid_cover(
    path: str, min_bytes: int = MIN_COVER_BYTES, min_dim: int = MIN_COVER_DIM
) -> tuple[bool, str]:
    """Check cover file exists, meets size/dimension minimums, is not blank."""
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
        blank, reason = is_blank_cover(path)
        if blank:
            return (False, f"blank cover: {reason}")
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


def check_rehosted_url(url: str, timeout: int = 25) -> tuple[bool, str]:
    """Download a rehosted cover URL and validate what it actually serves.

    Catches the rehoster serving a placeholder/blank instead of the real
    cover. Returns (True, reason) when the URL serves a real cover image.
    """
    import tempfile

    try:
        import requests
    except ImportError:
        return (False, "requests missing")
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return (False, f"http {resp.status_code}")
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" in ctype:
            return (False, "viewer page, not image")
        if len(resp.content) < MIN_COVER_BYTES:
            return (False, f"tiny {len(resp.content)}b")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp.write(resp.content)
        tmp.close()
        try:
            valid, reason = is_valid_cover(tmp.name)
            return (valid, reason)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    except Exception as e:
        return (False, f"fetch failed: {e}")
