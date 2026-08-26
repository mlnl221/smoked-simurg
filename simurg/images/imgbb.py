"""ImgBB uploader."""

import requests

from simurg.errors import ImageUploadFailed
from simurg.images.base import BaseImageUploader


class ImageUploader(BaseImageUploader):
    def _perform(self, file_, ext):
        try:
            from simurg.config import get_config

            cfg = get_config()
            key = str(cfg.image.get("imgbb_key", "") or "").strip()
            ua = str(cfg.upload.get("user_agent", "simurg/0.1.0"))
        except Exception:
            key = ""
            ua = "simurg/0.1.0"
        headers = {"referer": "https://imgbb.com/", "User-Agent": ua}
        data = {"key": key}
        url = "https://api.imgbb.com/1/upload"
        files = {"image": file_}
        resp = requests.post(url, headers=headers, data=data, files=files, timeout=20)
        if resp.status_code == requests.codes.ok:
            try:
                return resp.json()["data"]["url"], None
            except (ValueError, KeyError, TypeError) as e:
                raise ImageUploadFailed(f"Failed decoding body: {e} {resp.content[:500]}") from e
        else:
            raise ImageUploadFailed(f"Failed. Status {resp.status_code}: {resp.content[:500]}")
