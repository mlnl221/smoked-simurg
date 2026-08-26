"""Torrent generation wrapper per §2.1."""

from __future__ import annotations

import os
from pathlib import Path

import click
from torf import Torrent


def generate_torrent(
    gazelle_site, filepath: Path | str, dottorrents_dir: str | None = None
) -> tuple[str, Torrent]:
    """Generate private torrent with source SIM, piece 32768."""
    filepath = Path(filepath)
    if dottorrents_dir is None:
        dottorrents_dir = getattr(gazelle_site, "dot_torrents_dir", ".torrents")
    os.makedirs(dottorrents_dir, exist_ok=True)

    announce = (
        gazelle_site.announce
        if hasattr(gazelle_site, "announce")
        else "https://tracker.simurg.world/announce"
    )

    click.secho(f"Generating torrent for {filepath.name}...", fg="yellow", nl=False)

    t = Torrent(
        filepath,
        trackers=[announce],
        private=True,
        source="SIM",
        # comment will be set server-side; torf sets comment optionally
    )
    t.piece_size = 32768
    t.generate()

    basename = filepath.stem  # without extension
    # Keep original filename inside torrent? torf already does; ensure name is file name
    tpath = os.path.join(dottorrents_dir, f"{basename} - SIM.torrent")
    # Ensure overwrite
    t.write(tpath, overwrite=True)
    click.secho(" done!", fg="yellow")
    click.secho(f" Torrent: {tpath}", fg="cyan")
    return tpath, t
