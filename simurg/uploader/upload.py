"""Per-file upload preparation (stripped upload.py:21)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from simurg.constants import fmt_url
from simurg.uploader.payload import (
    build_release_desc,
    compile_data_existing_magazine,
    compile_data_existing_publication,
    compile_data_new_magazine,
    compile_data_new_publication,
)
from simurg.uploader.torrent import generate_torrent

loop = asyncio.get_event_loop()


def _stamp_torrent_comment(torrent_path, torrent, base_url, torrent_id):
    """Write the upload URL back into the .torrent's top-level ``comment`` field.

    The ``comment`` field lives outside the ``info`` dict, so re-writing the
    metafile does NOT change its info-hash — the tracker's stored torrent still
    matches while the user's local copy links back to the site. Mirrors
    smoked-salmon-mini's ``torrent_content.comment = url`` flow.
    """
    url = f"{base_url}/torrents.php?torrentid={torrent_id}"
    torrent.comment = url
    torrent.write(torrent_path, overwrite=True)
    return url


def prepare_and_upload(
    gazelle_site,
    filepath: Path,
    group_id,
    metadata: dict,
    cover_url: str | None,
    request_id=None,
    dry_run: bool = False,
    category: str = "ebooks",
):
    """Compile payload + torrent + upload. Returns torrent_id, group_id, torrent_path.

    In dry-run the real torrent is still generated and the payload compiled —
    only the POST to Simurg is skipped.

    ``category`` selects the payload compiler ("ebooks" or "magazines").
    """
    # Build release_desc if not present
    if not metadata.get("release_desc"):
        metadata["release_desc"] = build_release_desc(metadata, metadata.get("source_urls"))

    if group_id is None:
        if category == "magazines":
            data = compile_data_new_magazine(
                metadata, cover_url, metadata.get("source_urls"), request_id
            )
        else:
            data = compile_data_new_publication(
                metadata, cover_url, metadata.get("source_urls"), request_id
            )
    else:
        # Validate the --group-id against Simurg so a bad id fails fast with a
        # clear message instead of the cryptic "The selected torrent group does
        # not exist." Skip when unauthenticated (e.g. dry-run without a session).
        if getattr(gazelle_site, "authkey", "dummy") != "dummy":
            ok, _title = gazelle_site.validate_publication_id(str(group_id))
            if not ok:
                raise click.ClickException(
                    f"--group-id {group_id} is not a valid Simurg Publication id.\n"
                    f"Find the correct id on the site: open the book's Publication page "
                    f"(torrents.php?action=publication&id=<id>) and pass that <id>.\n"
                    f"The id on a plain torrents.php?id=<n> page is a different number "
                    f"(a torrent group id), not the publication id."
                )
        if category == "magazines":
            data = compile_data_existing_magazine(group_id, metadata, cover_url, request_id)
        else:
            data = compile_data_existing_publication(group_id, metadata, cover_url, request_id)

    torrent_path, torrent = generate_torrent(gazelle_site, filepath)

    if dry_run:
        click.secho("\n--- Dry-run payload ---", fg="yellow", bold=True)
        for k, v in data.items():
            # hide auth?
            click.echo(f"{k}: {v}")
        click.secho(
            f"Dry-run: torrent generated at {torrent_path} — SKIPPING upload to Simurg",
            fg="yellow",
            bold=True,
        )
        return None, group_id, torrent_path

    # Compile files
    with open(torrent_path, "rb") as tf:
        torrent_bytes = tf.read()
    basename = Path(filepath).stem
    files = [("file_input", (f"{basename}.torrent", torrent_bytes, "application/octet-stream"))]

    click.secho("Uploading torrent...", fg="yellow")
    try:
        torrent_id, ret_group_id = loop.run_until_complete(gazelle_site.upload(data, files))
        click.secho(
            f"Upload successful! groupId={ret_group_id} torrentId={torrent_id}",
            fg="green",
            bold=True,
        )
        click.echo(
            fmt_url(f"https://simurg.world/torrents.php?id={ret_group_id}&torrentid={torrent_id}")
        )
        _stamp_torrent_comment(torrent_path, torrent, gazelle_site.base_url, torrent_id)
        return torrent_id, ret_group_id, torrent_path
    except Exception as e:
        err_msg = str(e)
        # Handle duplicate torrent file - treat as success using existing IDs from error
        if "exact same torrent file already exists" in err_msg.lower():
            import re

            m_tid = re.search(r"torrentid=(\d+)", err_msg)
            m_gid = re.search(r"releaseid=(\d+)", err_msg) or re.search(
                r"groupId[^\d]*(\d+)", err_msg, re.IGNORECASE
            )
            # Also try publication id
            if m_tid:
                tid = int(m_tid.group(1))
                gid = int(m_gid.group(1)) if m_gid else tid
                click.secho(
                    f"Duplicate torrent already on site, treating as success: groupId={gid} torrentId={tid}",
                    fg="yellow",
                )
                click.echo(fmt_url(f"https://simurg.world/torrents.php?id={gid}&torrentid={tid}"))
                _stamp_torrent_comment(torrent_path, torrent, gazelle_site.base_url, tid)
                return tid, gid, torrent_path
        # Handle publication already exists - retry with existing publication
        if "publication already exists" in err_msg.lower():
            import re

            m = re.search(r"publication id (\d+)", err_msg, re.IGNORECASE)
            # Alternative pattern: "Publication ID 8197, 8198"
            if not m:
                m = re.search(r"publication id[s]? (\d+)", err_msg, re.IGNORECASE)
            # Find all numbers after that
            ids = re.findall(r"publication id (\d+)", err_msg, re.IGNORECASE)
            if not ids:
                ids = re.findall(r"(\d{3,})", err_msg)
                # Filter to likely publication ids (first two numbers are likely 8197,8198)
                # But we need to be careful - the error contains those two
                # Try to extract from the part after "Publication already exists"
                part = err_msg.split("Publication already exists")[-1]
                ids = re.findall(r"\b(\d{4,})\b", part)
            if ids:
                # Try each publication id until one succeeds
                for pid in ids[:3]:  # try first 3
                    click.secho(f"Retrying upload to existing Publication {pid}...", fg="yellow")
                    try:
                        # Build data for existing publication — category-aware (magazines use magazine payload)
                        if category == "magazines":
                            retry_data = compile_data_existing_magazine(
                                pid, metadata, cover_url, request_id
                            )
                        else:
                            retry_data = compile_data_existing_publication(
                                pid, metadata, cover_url, request_id
                            )
                        # Need to re-add auth (will be added in upload)
                        retry_tid, retry_gid = loop.run_until_complete(
                            gazelle_site.upload(retry_data, files)
                        )
                        click.secho(
                            f"Upload successful to existing Publication! groupId={retry_gid} torrentId={retry_tid}",
                            fg="green",
                            bold=True,
                        )
                        click.echo(
                            fmt_url(
                                f"https://simurg.world/torrents.php?id={retry_gid}&torrentid={retry_tid}"
                            )
                        )
                        _stamp_torrent_comment(
                            torrent_path, torrent, gazelle_site.base_url, retry_tid
                        )
                        return retry_tid, retry_gid, torrent_path
                    except Exception as retry_e:
                        if "publication already exists" in str(retry_e).lower() and pid != ids[-1]:
                            continue
                        # If retry fails for other reason, break and handle below
                        err_msg = str(retry_e)
                        break
        click.secho(f"Upload failed: {e}", fg="red", bold=True)
        # save payload to .failed?
        try:
            import json
            import os

            os.makedirs(".failed", exist_ok=True)
            with open(f".failed/{basename}.json", "w", encoding="utf-8") as f:
                json.dump({"data": data, "error": str(e)}, f, indent=2)
            click.secho(f"Saved payload to .failed/{basename}.json", fg="yellow")
        except Exception:
            pass
        raise
