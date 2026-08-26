"""Base image uploader + Pillow downscale per H28."""

from __future__ import annotations

import contextlib
import mimetypes
import os
import tempfile

mimetypes.init()

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore


def downscale_image(path: str, max_size: int = 500) -> str:
    """Downscale to ~500x500, convert PNG->JPG per H28. Returns new temp path if changed, else original."""
    if Image is None:
        return path
    try:
        im = Image.open(path)
        # Convert RGBA/P to RGB for JPG
        needs_convert = im.mode in ("RGBA", "LA", "P")
        w, h = im.size
        do_resize = max(w, h) > max_size
        do_convert = path.lower().endswith(".png") or needs_convert

        if not do_resize and not do_convert:
            return path

        if do_resize:
            im.thumbnail((max_size, max_size), Image.LANCZOS)

        if needs_convert or do_convert:
            # flatten transparent to white
            if im.mode in ("RGBA", "LA"):
                background = Image.new("RGB", im.size, (255, 255, 255))
                if im.mode == "RGBA":
                    background.paste(im, mask=im.split()[-1])
                else:
                    background.paste(im, mask=im.split()[-1])
                im = background
            elif im.mode == "P" or im.mode != "RGB":
                im = im.convert("RGB")

        suffix = ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()
        im.save(tmp_path, "JPEG", quality=92)
        return tmp_path
    except Exception:
        return path


class BaseImageUploader:
    def upload_file(self, filename: str):
        processed = downscale_image(filename)
        try:
            with contextlib.ExitStack() as stack:
                open_file = stack.enter_context(open(processed, "rb"))
                mime_type, _ = mimetypes.guess_type(processed)
                if not mime_type or mime_type.split("/")[0] != "image":
                    raise ValueError(f"Unknown image file type {mime_type} for {processed}")
                ext = os.path.splitext(processed)[1]
                # pass filename tuple: (filename, fileobj, mime)
                return self._perform((os.path.basename(processed), open_file, mime_type), ext)
        finally:
            # clean temp if we created new file
            if processed != filename and os.path.exists(processed):
                try:
                    os.unlink(processed)
                except Exception:
                    pass
