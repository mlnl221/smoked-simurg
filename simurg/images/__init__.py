"""Images factory."""

from simurg.images import catbox, imgbb, ptscreens

HOSTS = {
    "ptscreens": ptscreens,
    "imgbb": imgbb,
    "catbox": catbox,
}


def get_uploader(name: str):
    name = name.lower().strip()
    if name not in HOSTS:
        raise ValueError(f"Unknown image host {name}, choose from {', '.join(HOSTS)}")
    return HOSTS[name].ImageUploader
