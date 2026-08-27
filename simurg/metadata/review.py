"""Interactive metadata review/edit, mirroring smoked-salmon-mini's editor flow.

After scraping + combining, let the user open their `$EDITOR` (nano by default)
to revise metadata fields, either per-field or as a whole JSON blob via
``click.edit`` (salmon's ``tagger/review.py`` + ``tagger/metadata.py`` pattern).
"""

from __future__ import annotations

import json
import os
import sys

import click

# User-editable projection for the whole-dict JSON edit. Internal keys
# (cover_path, source_urls, format, type, image, ...) are intentionally
# excluded so the user cannot clobber upload wiring.
EBOOK_EDITABLE = [
    "title",
    "remaster_title",
    "authors",
    "year",
    "remaster_year",
    "publisher",
    "isbn",
    "page_count",
    "language",
    "source",
    "tags",
    "album_desc",
]

MAGAZINE_EDITABLE = [
    "title",
    "release_title",
    "year",
    "original_year",
    "volume",
    "issue_number",
    "print_issn",
    "electronic_issn",
    "publisher",
    "country",
    "frequency",
    "page_count",
    "language",
    "release_type",
    "tags",
    "book_desc",
    "album_desc",
]


def _resolve_editor() -> str:
    """Mirror salmon: ``cfg.upload.default_editor`` -> $EDITOR -> nano."""
    try:
        from simurg.config import get_config

        cfg = get_config()
        ed = str(cfg.upload.get("default_editor", "") or "").strip()
        if ed:
            return ed
    except Exception:
        pass
    return os.environ.get("EDITOR") or "nano"


def _print_metadata(metadata: dict, is_mag: bool) -> None:
    click.secho("\nCurrent metadata:", fg="cyan", bold=True)
    # Show every editable field (including optional ones) so missing values are
    # visible and can be revised. Cover image is handled separately, so it is
    # intentionally omitted. Empty values are shown as dim "(empty)" instead
    # of being hidden (previous behaviour skipped them).
    # `album_desc` is a Gazelle-legacy wire name: for ebooks it is the
    # synopsis/description, for magazines `book_desc` is the canonical synopsis
    # and `album_desc` is the per-issue release notes
    # (see `simurg/uploader/payload.py:15` and `simurg/metadata/combine.py:240`).
    # Display them as `description` / `release_notes` so users are not confused.
    if is_mag:
        display_labels = {"book_desc": "description", "album_desc": "release_notes"}
    else:
        display_labels = {"album_desc": "description"}
    keys = MAGAZINE_EDITABLE if is_mag else EBOOK_EDITABLE
    for k in keys:
        v = metadata.get(k)
        if v is None or v == "" or v == []:
            display = click.style("(empty)", fg="yellow", dim=True)
        elif isinstance(v, list):
            joined = ", ".join(str(x) for x in v if str(x).strip())
            display = joined if joined else click.style("(empty)", fg="yellow", dim=True)
        else:
            display = str(v)
        label = display_labels.get(k, k)
        click.echo(f"  {label:15}: {display}")


def _edit_scalar(metadata: dict, key: str, editor: str, is_int: bool = False) -> None:
    cur = metadata.get(key)
    text = click.edit("" if cur is None else str(cur), editor=editor)
    if text is None:
        return
    text = text.strip()
    if is_int:
        if not text:
            return
        try:
            metadata[key] = int(text)
        except ValueError:
            click.secho(f"Invalid integer for {key}, keeping old value.", fg="red")
        return
    metadata[key] = text or None


def _edit_list(metadata: dict, key: str, editor: str) -> None:
    cur = metadata.get(key) or []
    if isinstance(cur, str):
        cur = [cur]
    text = click.edit("\n".join(str(x) for x in cur), editor=editor)
    if text is None:
        return
    metadata[key] = [key.strip() for key in text.split("\n") if key.strip()]


def _edit_years(metadata: dict, editor: str) -> None:
    while True:
        text = (
            f"Year         : {metadata.get('year') or ''}\n"
            f"Remaster Year: {metadata.get('remaster_year') or ''}"
        )
        text = click.edit(text, editor=editor)
        if text is None:
            return
        try:
            year_line, remaster_line = (line.strip() for line in text.strip().split("\n", 1))
            year = _match_int(year_line, r"Year *: *(\d+)")
            remaster = _match_int(remaster_line, r"Remaster Year *: *(\d+)")
            if year is not None:
                metadata["year"] = year
            if remaster is not None:
                metadata["remaster_year"] = remaster
            return
        except (TypeError, ValueError):
            if not click.confirm(
                click.style("Invalid year formatting, retry?", fg="magenta"),
                default=True,
                abort=True,
            ):
                return


def _match_int(line: str, pattern: str) -> int | None:
    import re

    m = re.match(pattern, line)
    if m:
        return int(m.group(1))
    return None


def _edit_all_json(metadata: dict, editable: list[str], editor: str) -> None:
    projection = {k: metadata.get(k) for k in editable}
    text = json.dumps(projection, indent=2, ensure_ascii=False)
    while True:
        edited = click.edit(text, extension=".json", editor=editor)
        if edited is None:
            return
        try:
            new = json.loads(edited)
        except json.JSONDecodeError as e:
            if not click.confirm(
                click.style(f"Metadata is not valid JSON ({e}), retry?", fg="magenta"),
                default=True,
                abort=True,
            ):
                return
            text = edited
            continue
        for k, v in new.items():
            if k in editable:
                # Keep list fields as lists even if the user edited them as strings.
                if k in ("authors", "tags") and isinstance(v, str):
                    v = [s.strip() for s in v.split("\n") if s.strip()]
                metadata[k] = v
        return


def review_metadata(metadata: dict, is_mag: bool = False, dry_run: bool = False) -> dict:
    """Let the user revise scraped/combined metadata in their editor.

    Per-field menu (opens nano for each) plus a ``[*]`` whole-dict JSON edit,
    exactly like smoked-salmon-mini. Returns the (mutated) metadata dict.
    """
    # Skip the interactive editor when there's no TTY (e.g. under a test
    # harness / CI) or during dry-run, where the upload is skipped anyway.
    if dry_run or not sys.stdin.isatty():
        return metadata

    editor = _resolve_editor()
    editable = MAGAZINE_EDITABLE if is_mag else EBOOK_EDITABLE

    if is_mag:
        edit_functions = {
            "t": lambda: _edit_scalar(metadata, "title", editor),
            "r": lambda: _edit_scalar(metadata, "release_title", editor),
            "y": lambda: _edit_scalar(metadata, "year", editor, is_int=True),
            "oy": lambda: _edit_scalar(metadata, "original_year", editor, is_int=True),
            "v": lambda: _edit_scalar(metadata, "volume", editor),
            "i": lambda: _edit_scalar(metadata, "issue_number", editor),
            "is": lambda: _edit_scalar(metadata, "print_issn", editor),
            "ie": lambda: _edit_scalar(metadata, "electronic_issn", editor),
            "p": lambda: _edit_scalar(metadata, "publisher", editor),
            "c": lambda: _edit_scalar(metadata, "country", editor),
            "f": lambda: _edit_scalar(metadata, "frequency", editor),
            "pg": lambda: _edit_scalar(metadata, "page_count", editor, is_int=True),
            "l": lambda: _edit_scalar(metadata, "language", editor),
            "rt": lambda: _edit_scalar(metadata, "release_type", editor),
            "g": lambda: _edit_list(metadata, "tags", editor),
            "d": lambda: _edit_scalar(metadata, "book_desc", editor),
            "rn": lambda: _edit_scalar(metadata, "album_desc", editor),
            "*": lambda: _edit_all_json(metadata, editable, editor),
        }
        menu = (
            "\nRevise metadata? [t]itle [r]elease title [y]ear [oy]riginal year [v]olume [i]ssue "
            "[is]sn [ie]lectronic issn [p]ublisher [c]ountry [f]requency [pg]pages [l]anguage "
            "[rt]release type [g]enres/tags [d]escription [rn]release notes [*]edit ALL (JSON) [n]othing"
        )
    else:
        edit_functions = {
            "t": lambda: _edit_scalar(metadata, "title", editor),
            "r": lambda: _edit_scalar(metadata, "remaster_title", editor),
            "a": lambda: _edit_list(metadata, "authors", editor),
            "y": lambda: _edit_years(metadata, editor),
            "p": lambda: _edit_scalar(metadata, "publisher", editor),
            "i": lambda: _edit_scalar(metadata, "isbn", editor),
            "g": lambda: _edit_list(metadata, "tags", editor),
            "s": lambda: _edit_scalar(metadata, "source", editor),
            "l": lambda: _edit_scalar(metadata, "language", editor),
            "d": lambda: _edit_scalar(metadata, "album_desc", editor),
            "pg": lambda: _edit_scalar(metadata, "page_count", editor, is_int=True),
            "*": lambda: _edit_all_json(metadata, editable, editor),
        }
        menu = (
            "\nRevise metadata? [t]itle [r]elease title [a]uthors [y]ears "
            "[p]ublisher [i]sbn [g]enres/tags [s]ource [l]anguage [d]escription "
            "[pg]pages [*]edit ALL (JSON) [n]othing"
        )

    while True:
        _print_metadata(metadata, is_mag)
        ans = (
            click.prompt(click.style(menu, fg="magenta"), default="n", show_default=False)
            .strip()
            .lower()
        )
        if ans in ("n", "nothing", ""):
            break
        if ans == "*":
            edit_functions["*"]()
            continue
        fn = edit_functions.get(ans)
        if fn is None:
            click.secho(f"{ans} is not a valid editing option.", fg="red")
            continue
        fn()
    return metadata
