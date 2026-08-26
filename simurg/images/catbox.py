"""Catbox uploader."""

import random

import requests

from simurg.errors import ImageUploadFailed
from simurg.images.base import BaseImageUploader

try:
    from simurg.constants import UAGENTS  # if exists
except Exception:
    UAGENTS = ["simurg/0.1.0"]


class ImageUploader(BaseImageUploader):
    def _perform(self, file_, ext):
        try:
            from simurg.config import get_config

            cfg = get_config()
            userhash = str(
                cfg.image.get("catbox_userhash", "") or cfg.image.get("catbox_user_hash", "") or ""
            ).strip()
        except Exception:
            userhash = ""
        headers = {
            "User-Agent": random.choice(UAGENTS)
            if isinstance(UAGENTS, list) and UAGENTS
            else "simurg/0.1.0",
            "referrer": "https://catbox.moe/",
        }
        data = {"reqtype": "fileupload", "userhash": userhash}
        url = "https://catbox.moe/user/api.php"
        files = {"fileToUpload": file_}
        resp = requests.post(url, headers=headers, data=data, files=files, timeout=20)
        if resp.status_code == requests.codes.ok:
            text = resp.text.strip()
            if text.startswith("https://"):
                return text, None
            raise ImageUploadFailed(f"Unexpected response: {text[:500]}")
        else:
            raise ImageUploadFailed(f"Failed. Status {resp.status_code}: {resp.content[:500]}")
