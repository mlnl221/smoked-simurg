"""DuckDuckGo images fallback for covers H29."""

from __future__ import annotations

import re
import tempfile

import requests

from simurg.constants import SCRAPER_TIMEOUT


def fetch_duckduckgo_cover(
    title: str, authors: list[str], session: requests.Session | None = None
) -> str | None:
    """Query DuckDuckGo images for book cover, download to temp, return temp path or None."""
    if not title:
        return None
    query = f"{' '.join(authors[:1])} {title} book cover" if authors else f"{title} book cover"
    session = session or requests.Session()
    try:
        # Use DuckDuckGo image search API endpoint
        url = "https://duckduckgo.com/"
        params = {"q": query}
        headers = {"User-Agent": "simurg/0.1.0"}
        r = session.get(url, params=params, headers=headers, timeout=SCRAPER_TIMEOUT)
        # Try to extract vqd token if needed, but simpler: use html scrape for image urls
        # Fallback: directly search via duckduckgo image i.js
        # Get token
        m = re.search(r'vqd="([^"]+)"', r.text) or re.search(r"vqd='([^']+)'", r.text)
        vqd = m.group(1) if m else None
        if vqd:
            img_url = "https://duckduckgo.com/i.js"
            params2 = {"l": "wt-wt", "o": "json", "q": query, "vqd": vqd, "f": ",,,", "p": "1"}
            r2 = session.get(img_url, params=params2, headers=headers, timeout=SCRAPER_TIMEOUT)
            if r2.status_code == 200:
                try:
                    data = r2.json()
                    results = data.get("results", [])
                    if results:
                        image = results[0].get("image")
                        if image:
                            # download image
                            img_resp = session.get(image, headers=headers, timeout=SCRAPER_TIMEOUT)
                            if img_resp.status_code == 200 and img_resp.content[:4] not in (
                                b"<!DO",
                                b"<htm",
                            ):
                                from simurg.images.validate import MIN_COVER_BYTES, is_valid_cover

                                if len(img_resp.content) < MIN_COVER_BYTES:
                                    return None
                                ext = ".jpg"
                                ctype = img_resp.headers.get("Content-Type", "")
                                if ctype and not ctype.startswith("image/"):
                                    return None
                                if "png" in ctype:
                                    ext = ".png"
                                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                                tmp.write(img_resp.content)
                                tmp.close()
                                valid, _reason = is_valid_cover(tmp.name)
                                if not valid:
                                    import os

                                    os.unlink(tmp.name)
                                    return None
                                return tmp.name
                except Exception:
                    pass
    except Exception:
        pass
    return None
