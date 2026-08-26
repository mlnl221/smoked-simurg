"""PTScreens uploader."""

import requests

from simurg.errors import ImageUploadFailed
from simurg.images.base import BaseImageUploader


class ImageUploader(BaseImageUploader):
    def _perform(self, file_, ext):
        try:
            from simurg.config import get_config

            cfg = get_config()
            key = cfg.image.get("ptscreens_key", "") or cfg.image.get("ptscreens_api_key", "")
            key = str(key).strip() if key else ""
        except Exception:
            key = ""
        headers = {"X-API-Key": key} if key else {}
        url = "https://ptscreens.com/api/1/upload"
        files = {"source": file_}
        resp = requests.post(url, headers=headers, files=files, timeout=20)
        if resp.status_code == requests.codes.ok:
            try:
                r = resp.json()
                if "image" in r:
                    url_out = r["image"].get("url")
                    return url_out, None
                raise ImageUploadFailed("Missing image data in response")
            except ValueError as e:
                raise ImageUploadFailed(f"Failed decoding body: {e} {resp.content[:500]}") from e
        else:
            raise ImageUploadFailed(f"Failed. Status {resp.status_code}: {resp.content[:500]}")
