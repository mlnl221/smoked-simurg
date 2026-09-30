"""CLI for smoked-simurg: `simurg up` + checkconf/health."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import click
import requests

from simurg import __version__
from simurg.constants import (
    ALLOWED_EXTENSIONS,
    BATCH_LIMIT_DEFAULT,
    BLACKLISTED_CHARS,
    FAIL_SYMBOL,
    FORMAT_MAP,
    INFO_SYMBOL,
    MAGAZINE_EXTENSIONS,
    MAGAZINE_FORMAT_MAP,
    OK_SYMBOL,
    SKIP_SYMBOL,
    SOURCE_LABELS,
    WARN_SYMBOL,
    fmt_url,
    fmt_urls,
)
from simurg.errors import ConfigError
from simurg.metadata.scrapers.util import year_from


def _sanitize_filename(name: str) -> str:
    return re.sub(BLACKLISTED_CHARS, "_", name)


def _rate_limit_wait(seconds: int = 7, label: str = "") -> None:
    """Visible countdown wait (docs/ux-improvements.md §3.2).

    Rewrites one line in place; Ctrl+C offers to skip the remaining wait
    instead of killing the whole batch.
    """
    end = time.monotonic() + seconds
    try:
        while True:
            remaining = round(end - time.monotonic())
            if remaining <= 0:
                break
            click.echo(
                f"\r{SKIP_SYMBOL} Rate limit: next upload in {remaining:3d}s {label}   ",
                nl=False,
            )
            time.sleep(min(1.0, max(0.05, end - time.monotonic())))
        # Clear the countdown line now that it's done
        click.echo("\r" + " " * 60 + "\r", nl=False)
    except KeyboardInterrupt:
        click.echo()
        if click.confirm("Skip the rate-limit wait?", default=False):
            return
        raise click.Abort() from None


def _confirm_delete_file(filepath: Path, dry_run: bool = False) -> bool:
    """Confirm then delete a source file. Returns True if deleted (or dry-run would-delete)."""
    try:
        if not click.confirm(f"Delete file {filepath.name}?", default=False):
            return False
    except (click.Abort, EOFError):
        return False
    if dry_run:
        click.secho(f"Dry-run: would delete {filepath}", fg="yellow")
        return True
    try:
        Path(filepath).unlink(missing_ok=True)
        click.secho(f"Deleted {filepath.name}", fg="yellow")
        return True
    except Exception as e:
        click.secho(f"Delete failed for {filepath.name}: {e}", fg="red")
        return False


def _print_inbuilt_metadata(inbuilt: dict, fmt: str, is_mag: bool = False) -> None:
    """Pretty panel of the metadata read from the file, shown before scraping.

    Mirrors the UX documented in docs/ux-improvements.md: one readable block
    (consistent symbols, aligned columns) so the user can see what the file
    already carries before auto-scraping enriches it.
    """
    rows: list[tuple[str, object]] = [("Title", inbuilt.get("title"))]
    if is_mag:
        issue = inbuilt.get("issue_label") or inbuilt.get("issue_date")
        if issue:
            rows.append(("Issue", issue))
        if inbuilt.get("volume"):
            rows.append(("Volume", inbuilt.get("volume")))
    rows.append(("Author(s)", inbuilt.get("authors")))
    rows.append(("Year", inbuilt.get("year") or inbuilt.get("publish_year")))
    rows.append(("Publisher", inbuilt.get("publisher")))
    rows.append(("ISBN", inbuilt.get("isbn")))
    rows.append(("Pages", inbuilt.get("page_count")))
    rows.append(("Format", fmt))
    rows.append(("Edition", inbuilt.get("edition")))
    rows.append(("Language", inbuilt.get("language")))

    width = max((len(label) for label, _ in rows), default=8)
    bar = "─" * (width + 26)
    click.secho(bar, fg="cyan")
    click.secho(f" {INFO_SYMBOL} Metadata from file (before scraping)", fg="cyan", bold=True)
    for label, val in rows:
        if val in (None, "", [], {}):
            val = click.style("(none)", fg="yellow")
        elif isinstance(val, (list, tuple)):
            val = ", ".join(str(v) for v in val)
        else:
            val = str(val)
        click.echo(f"   {label + ':':<{width + 2}} {val}")
    click.secho(bar, fg="cyan")


def _download_url_to_temp(url: str) -> str | None:
    """Download a remote cover URL to a temp file, returning the temp path or None."""
    try:
        resp = requests.get(url, timeout=15, stream=True)
        if resp.status_code != 200:
            return None
        suffix = ".jpg"
        ctype = resp.headers.get("Content-Type", "")
        if ctype and not ctype.startswith("image/"):
            return None
        if "png" in ctype:
            suffix = ".png"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        total = 0
        for chunk in resp.iter_content(8192):
            if chunk:
                tmp.write(chunk)
                total += len(chunk)
        tmp.close()
        from simurg.images.validate import MIN_COVER_BYTES, is_valid_cover

        if total < MIN_COVER_BYTES:
            try:
                Path(tmp.name).unlink(missing_ok=True)
            except Exception:
                pass
            return None
        valid, _ = is_valid_cover(tmp.name)
        if not valid:
            try:
                Path(tmp.name).unlink(missing_ok=True)
            except Exception:
                pass
            return None
        return tmp.name
    except Exception:
        return None


def _prompt_manual_cover(title: str, authors: list, dry_run: bool = False) -> str | None:
    """Prompt for a manual cover URL after automatic sources failed validation.

    Returns the temp path, None to continue without cover, "skip" to skip the
    file, or "delete" to delete the file.
    """
    if dry_run:
        return None
    click.echo("Automatic cover sources failed validation.")
    while True:
        try:
            pasted = click.prompt(
                "Paste cover image URL ([s]kip without cover / [a]bort skips file / [d]elete file)",
                type=str,
            ).strip()
        except (click.Abort, EOFError):
            raise click.Abort() from None
        low = pasted.lower()
        if low in ("s", "skip", ""):
            return None
        if low in ("a", "abort"):
            return "skip"
        if low in ("d", "delete"):
            return "delete"
        if not pasted.startswith("http"):
            click.secho("Cover URL must start with http.", fg="yellow")
            continue
        try:
            tmp = _download_url_to_temp(pasted)
        except (click.Abort, EOFError):
            raise click.Abort() from None
        if tmp:
            click.secho("Downloaded manual cover", fg="green")
            return tmp
        click.secho(
            "That URL did not yield a valid image (too small/corrupt/non-image) — try again.",
            fg="yellow",
        )


def _confirm_rehosted_cover(
    cover_url: str | None,
    temp_cover: str | None,
    cover_path: str | None = None,
    dry_run: bool = False,
) -> tuple[str | None, str | None] | str:
    """Confirm a rehosted cover. Returns (url, path), "skip" to skip file, "delete" to delete file."""
    if not cover_url or not temp_cover or dry_run:
        return (cover_url, temp_cover)
    while True:
        try:
            ans = (
                click.prompt(
                    "Open the link above and check the image ([Enter] keep / [u] new URL / [s] skip without cover / [a]bort skips file / [d]elete file)",
                    type=str,
                    default="",
                    show_default=False,
                )
                .strip()
                .lower()
            )
            if ans in ("", "y", "yes", "keep"):
                return (cover_url, temp_cover)
            if ans in ("s", "skip"):
                if temp_cover and temp_cover != cover_path:
                    try:
                        Path(temp_cover).unlink(missing_ok=True)
                    except Exception:
                        pass
                return (None, None)
            if ans in ("a", "abort"):
                return "skip"
            if ans in ("d", "delete"):
                return "delete"
            if ans == "u":
                url = click.prompt("Paste replacement cover image URL", type=str).strip()
                if not url.startswith("http"):
                    click.secho("Cover URL must start with http.", fg="yellow")
                    continue
                tmp = _download_url_to_temp(url)
                if tmp is None:
                    click.secho(
                        "That URL did not yield a valid image (too small/corrupt/non-image) — keeping current.",
                        fg="yellow",
                    )
                    continue
                try:
                    from simurg.config import get_config
                    from simurg.images import get_uploader

                    cfg = get_config()
                    name = str(cfg.image.get("cover_uploader", "ptscreens") or "ptscreens")
                    rehost_url, _ = get_uploader(name)().upload_file(tmp)
                    if temp_cover and temp_cover != cover_path:
                        try:
                            Path(temp_cover).unlink(missing_ok=True)
                        except Exception:
                            pass
                    cover_url = rehost_url
                    temp_cover = tmp
                    click.secho("Cover rehosted: ", fg="green", bold=True, nl=False)
                    click.echo(fmt_url(cover_url))
                except Exception as e:
                    click.secho(f"{FAIL_SYMBOL} Cover rehost failed: {e}", fg="red")
                    try:
                        Path(tmp).unlink(missing_ok=True)
                    except Exception:
                        pass
                continue
            click.secho("Invalid choice — Enter keeps, u/s/a/d.", fg="yellow")
        except EOFError:
            return (cover_url, temp_cover)
        except click.Abort:
            raise


def _decode_inbuilt_quick(filepath: Path) -> dict:
    ext = filepath.suffix.lower()
    if ext == ".epub":
        from simurg.metadata.epub import extract_epub

        return extract_epub(filepath)
    elif ext == ".pdf":
        from simurg.metadata.pdf import extract_pdf

        return extract_pdf(filepath)
    elif ext in (".mobi", ".azw3"):
        from simurg.metadata.mobi import extract_mobi

        return extract_mobi(filepath)
    elif ext == ".djvu":
        from simurg.metadata.mobi import extract_djvu

        return extract_djvu(filepath)
    return {}


def _collect_batch_files(
    dir_path: Path, allowed: set[str], limit: int
) -> tuple[list[Path], list[Path], bool]:
    """Walk dir_path iteratively, stopping after `limit` allowed matches.

    Returns (ebook_files, txt_found, hit_limit). Only the collected batch is
    sorted (walk order in, sorted out) so huge directories never pay a full
    walk + full sort. Per-directory entries are sorted for a deterministic
    walk; suffix is checked before the is_file stat to cut syscalls.
    `limit <= 0` means unlimited (full walk).
    """
    import os

    cap = limit if limit and limit > 0 else 0
    ebook_files: list[Path] = []
    txt_found: list[Path] = []
    stack = [dir_path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                ext = Path(entry.name).suffix.lower()
                if ext in allowed:
                    ebook_files.append(Path(entry.path))
                    if cap and len(ebook_files) >= cap:
                        ebook_files = sorted(ebook_files, key=lambda p: p.name.lower())
                        return ebook_files, txt_found, True
                elif ext == ".txt":
                    txt_found.append(Path(entry.path))
            except OSError:
                continue
    ebook_files = sorted(ebook_files, key=lambda p: p.name.lower())
    return ebook_files, txt_found, False


def _flip_last_first(name: str) -> str:
    """Convert 'Last, First' to 'First Last'."""
    name = name.strip()
    if "," in name and name.count(",") == 1:
        last, first = [p.strip() for p in name.split(",", 1)]
        if last and first:
            return re.sub(r"\s+", " ", f"{first} {last}").strip()
    return re.sub(r"\s+", " ", name).strip()


def _normalize_authors(authors):
    if not authors:
        return []
    if isinstance(authors, str):
        # Keep single "Last, First" as one author, don't split on comma
        if (
            authors.count(",") == 1
            and ";" not in authors
            and " and " not in authors.lower()
            and "&" not in authors
        ):
            authors = [authors]
        else:
            authors = re.split(r"\s*;\s*|\s+and\s+|\s*&\s*", authors)
    out = []
    seen = set()
    for a in authors:
        a = a.strip()
        if not a:
            continue
        # Flip Last, First -> First Last
        if "," in a and a.count(",") == 1:
            a = _flip_last_first(a)
        a = re.sub(r"\s+", " ", a)
        low = a.lower()
        if low not in seen:
            seen.add(low)
            out.append(a)
    return out


def _group_detection(files: list[Path]):
    """Group by normalized title+authors lower strip edition. Returns dict grouping."""
    groups = {}
    for fp in files:
        meta = _decode_inbuilt_quick(fp)
        title = (meta.get("title") or fp.stem).lower().strip()
        # strip edition words
        title = re.sub(
            r"\s*\(.*?\)|vol\.?\s*\d+|2nd edition|illustrated|edition",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(r"\s+", " ", title).strip()
        authors = ",".join(
            sorted(a.lower().strip() for a in _normalize_authors(meta.get("authors") or []))
        )
        key = f"{title}||{authors}"
        groups.setdefault(key, []).append(fp)
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    return dups


def _detect_magazine_year_packs(files: list[Path]) -> dict:
    """Group magazine files by (canonical_lower, year) for Year Pack detection.

    Returns dict with key (canonical_lower, year) -> list[Path] sorted.
    Only groups with >=2 files are considered pack candidates.
    """
    from collections import defaultdict

    from simurg.metadata.magazine import decode_magazine_filename

    groups: dict[tuple[str, int], list[Path]] = defaultdict(list)
    for fp in files:
        try:
            meta = decode_magazine_filename(fp)
        except Exception:
            continue
        canonical = (meta.get("canonical_title") or fp.stem).strip().lower()
        year = meta.get("year")
        if year is None:
            continue
        try:
            yint = int(year)
        except Exception:
            continue
        groups[(canonical, yint)].append(fp)
    # Filter to packs with at least 2 files, sort each pack by issue date/name
    result: dict[tuple[str, int], list[Path]] = {}
    for k, flist in groups.items():
        if len(flist) >= 2:
            # Sort by issue_date if available else name
            def _sort_key(p: Path):
                try:
                    m = decode_magazine_filename(p)
                    return (m.get("issue_date") or "", p.name.lower())
                except Exception:
                    return ("", p.name.lower())

            result[k] = sorted(flist, key=_sort_key)
    return result


def _detect_magazine_decade_packs(files: list[Path]) -> dict:
    """Group magazine files by (canonical_lower, decade_start) for Decade Pack detection.

    Decade packs are 10-year spans: 2000-2009, etc. Only groups with >=4 files
    (heuristic) are considered, to avoid noisy single-year decades.
    """
    from collections import defaultdict

    from simurg.metadata.magazine import decode_magazine_filename

    groups: dict[tuple[str, int], list[Path]] = defaultdict(list)
    for fp in files:
        try:
            meta = decode_magazine_filename(fp)
        except Exception:
            continue
        canonical = (meta.get("canonical_title") or fp.stem).strip().lower()
        year = meta.get("year")
        if year is None:
            continue
        try:
            yint = int(year)
        except Exception:
            continue
        decade = (yint // 10) * 10
        groups[(canonical, decade)].append(fp)
    result: dict[tuple[str, int], list[Path]] = {}
    for k, flist in groups.items():
        if len(flist) >= 4:  # at least 4 issues to be a decade pack

            def _sort_key(p: Path):
                try:
                    m = decode_magazine_filename(p)
                    return (m.get("issue_date") or "", p.name.lower())
                except Exception:
                    return ("", p.name.lower())

            result[k] = sorted(flist, key=_sort_key)
    return result


def _prompt_magazine_collection_mode(
    year_packs: dict, decade_packs: dict, total_files: int, dry_run: bool = False
) -> str:
    """Prompt user to choose Year Pack / Decade Pack / Individual.

    Returns "year", "decade", "individual", or "abort".
    Auto-prompts when any subdirectory is detected (Year/Decade detection).
    In non-interactive dry-run without input, defaults to Individual for safety.
    """
    # Summarize packs
    click.secho(
        f"\nDetected {len(year_packs)} yearly collection(s) and {len(decade_packs)} decade collection(s)",
        fg="cyan",
        bold=True,
    )
    for (canonical, year), flist in sorted(year_packs.items()):
        # Use canonical from first file's title part if available
        try:
            from simurg.metadata.magazine import decode_magazine_filename

            canon_display = decode_magazine_filename(flist[0]).get("canonical_title") or canonical
        except Exception:
            canon_display = canonical
        click.echo(f"  Year {year}: {len(flist)} issue(s) — {canon_display}")
    for (canonical, decade), flist in sorted(decade_packs.items()):
        try:
            from simurg.metadata.magazine import decode_magazine_filename

            canon_display = decode_magazine_filename(flist[0]).get("canonical_title") or canonical
        except Exception:
            canon_display = canonical
        click.echo(f"  Decade {decade}-{decade + 9}: {len(flist)} issue(s) — {canon_display}")

    click.echo(f"  Total files: {total_files}")
    click.echo(
        "  [Y] Year Pack — one torrent per year (e.g. '1995 Complete Year' or '1995 (10/12)')"
    )
    click.echo("  [D] Decade Pack — one torrent per decade (e.g. '2000-2009 Complete Decade')")
    click.echo("  [I] Individual Issues — one torrent per file")
    click.echo("  [A] Abort")
    while True:
        try:
            ans = (
                click.prompt(
                    "Upload as collections?",
                    type=str,
                    default="i",
                    show_default=False,
                )
                .strip()
                .lower()
            )
        except (click.Abort, EOFError):
            # In dry-run / non-interactive (e.g. no TTY, no input), default to individual
            # to avoid aborting the batch. Interactive abort is still explicit via "a".
            if dry_run:
                return "individual"
            return "abort"
        if ans in ("y", "year", "year pack"):
            return "year"
        if ans in ("d", "decade", "decade pack"):
            return "decade"
        if ans in ("i", "individual", "issue", "issues"):
            return "individual"
        if ans in ("a", "abort"):
            return "abort"
        click.secho("Invalid choice — pick Y, D, I or A.", fg="yellow")


def _format_scraper_result(res: dict, idx: int) -> str:
    """One-line summary of a scraper result for the pick prompt, with its URL."""
    scraper = str(res.get("_scraper") or "?").ljust(12)
    title = str(res.get("title") or "?")
    year = res.get("year") or res.get("publish_year")
    line = f"{title}{f' ({year})' if year else ''}"
    extras = []
    authors = res.get("authors") or []
    if authors:
        extras.append(", ".join(str(a) for a in authors[:2]))
    if res.get("publisher"):
        extras.append(str(res["publisher"]))
    if res.get("page_count"):
        extras.append(f"{res['page_count']}p")
    if res.get("isbn"):
        extras.append(f"ISBN {res['isbn']}")
    if extras:
        line += " — " + " · ".join(extras)
    match = []
    if res.get("_fuzzy_title") is not None:
        match.append(f"t={res['_fuzzy_title']:.2f}")
    if res.get("_fuzzy_author") is not None:
        match.append(f"a={res['_fuzzy_author']:.2f}")
    if res.get("_fuzzy_year") is not None:
        match.append(f"y={res['_fuzzy_year']:.2f}")
    if match:
        line += f" [{' '.join(match)}]"
    prefix = f"  [{idx}] {scraper} {line}"
    urls = res.get("source_urls") or []
    if urls:
        link = fmt_url(urls[0])
        if len(urls) > 1:
            link += click.style(f" (+{len(urls) - 1} more)", fg="cyan")
        prefix += "  " + link
    return prefix


def _scrape_from_url(url: str, inbuilt: dict, dry_run: bool = False) -> dict | None:
    """Route a pasted URL to the matching scraper and (optionally) confirm.

    Returns the chosen result dict, or None if the URL is unsupported / yields
    nothing / the user declines. In dry-run we still scrape but skip the
    confirmation prompt (auto-use, mirroring the non-interactive ``--url``).
    """
    from simurg.metadata.enricher import search_by_url, supported_url_domains

    click.secho(f"\nScraping pasted URL: {url}", fg="cyan")
    try:
        with requests.Session() as sess:
            res = search_by_url(url, sess)
    except Exception as e:
        click.secho(f"URL scrape failed: {e}", fg="yellow")
        return None
    if not res:
        click.secho(
            "Could not resolve that URL to book metadata. Supported sites: "
            + ", ".join(sorted(supported_url_domains())),
            fg="yellow",
        )
        return None
    yr = res.get("year") or res.get("publish_year")
    click.secho(
        f"Resolved via '{res.get('_scraper')}': {res.get('title')}"
        + (f" ({yr})" if yr else "")
        + (
            f" — {', '.join(str(a) for a in (res.get('authors') or [])[:2])}"
            if res.get("authors")
            else ""
        ),
        fg="green",
    )
    if res.get("source_urls"):
        click.echo(" " + fmt_urls(res["source_urls"]))
    if dry_run:
        return res
    ans = (
        click.prompt(
            "Use this metadata to fill the missing fields? [Y/n]",
            type=str,
            default="y",
            show_default=False,
        )
        .strip()
        .lower()
    )
    if ans in ("y", "yes", ""):
        return res
    return None


_MAX_FALLBACK_ATTEMPTS = 3


def _prompt_no_results_fallback(
    inbuilt: dict, is_mag: bool = False, dry_run: bool = False
) -> dict | str | None:
    """Offer interactive retry options when scraper search returns empty.

    Returns the chosen result dict, None for inbuilt-only, "skip" to skip the
    file, or "delete" to delete the file.
    """
    title = (inbuilt.get("title") or "").strip()
    authors = inbuilt.get("authors") or []
    author_str = ", ".join(str(a) for a in authors) if authors else "?"
    ref = f'"{title}" by {author_str}' if title else "(unknown title)"

    for attempt in range(1, _MAX_FALLBACK_ATTEMPTS + 1):
        click.secho(
            f"\nNo scraper results found for {ref}.",
            fg="yellow",
        )
        if attempt < _MAX_FALLBACK_ATTEMPTS:
            click.echo("  [t] Search by title+author (different terms)")
            click.echo("  [c] Custom search (enter query string)")
            click.echo("  [u] Paste a URL")
        click.echo("  [i] Use inbuilt metadata only")
        click.echo("  [s] Skip this file")
        click.echo("  [a] Abort (skip this file)")
        click.echo("  [d] Delete this file")
        ans = (
            click.prompt(
                f"Retry ({attempt}/{_MAX_FALLBACK_ATTEMPTS})",
                type=str,
                default="",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if ans in ("i", "inbuilt", "n"):
            return None
        if ans in ("s", "skip", "a", "abort"):
            return "skip"
        if ans in ("d", "delete"):
            return "delete"
        if ans == "u":
            from simurg.metadata.enricher import supported_url_domains

            pasted = click.prompt(
                "Paste a book-page URL (" + ", ".join(sorted(supported_url_domains())) + ")",
                type=str,
            ).strip()
            result = _scrape_from_url(pasted, inbuilt, dry_run=dry_run)
            if result:
                return result
            click.secho("URL did not resolve to metadata — try again.", fg="yellow")
            continue
        if ans == "t":
            new_title = click.prompt("Title", type=str, default=title).strip()
            new_authors_raw = click.prompt(
                "Authors (comma-separated)", type=str, default=author_str
            ).strip()
            new_authors = [a.strip() for a in new_authors_raw.split(",") if a.strip()]
            fake_inbuilt = dict(inbuilt, title=new_title, authors=new_authors)
            if is_mag:
                from simurg.metadata.enricher import (
                    rank_results,
                    search_magazine_scrapers,
                )

                results = search_magazine_scrapers(fake_inbuilt)
            else:
                from simurg.metadata.enricher import rank_results, search_all_scrapers

                results = search_all_scrapers(fake_inbuilt)
            if results:
                click.secho(f"Found {len(results)} result(s)", fg="green")
                if len(results) == 1:
                    click.secho(
                        f"Single result — using '{results[0].get('_scraper')}' automatically",
                        fg="green",
                    )
                    return rank_results(results) or None
                return _prompt_scraper_selection(results, fake_inbuilt)
            click.secho("Still no results.", fg="yellow")
            continue
        if ans == "c":
            query = click.prompt("Search query", type=str).strip()
            if not query:
                continue
            from simurg.metadata.enricher import rank_results, search_custom

            cats = {"magazine"} if is_mag else {"ebook"}
            results = search_custom(query, categories=cats)
            if results:
                click.secho(f"Found {len(results)} result(s)", fg="green")
                if len(results) == 1:
                    click.secho(
                        f"Single result — using '{results[0].get('_scraper')}' automatically",
                        fg="green",
                    )
                    return rank_results(results) or None
                return _prompt_scraper_selection(results, inbuilt)
            click.secho("Still no results.", fg="yellow")
            continue
        click.secho("Invalid choice — pick t, c, u, i, s, a or d.", fg="yellow")

    click.secho(
        f"Max retry attempts reached — using inbuilt metadata only for {ref}",
        fg="yellow",
    )
    return None


def _prompt_scraper_selection(results: list[dict], inbuilt: dict) -> dict | str | None:
    """Let the user pick which scraper result to fill metadata from.

    Returns the chosen result dict, None for inbuilt-only, "skip" to skip the
    file, or "delete" to delete the file.
    """
    title = (inbuilt.get("title") or "").strip()
    authors = inbuilt.get("authors") or []
    from simurg.metadata.enricher import _result_score

    ranked = sorted(results, key=_result_score, reverse=True)
    click.secho(
        "\nScraper results — pick which metadata to use to fill in the missing fields:",
        fg="cyan",
        bold=True,
    )
    author_str = ", ".join(str(a) for a in authors) if authors else "?"
    click.echo(f"  Inbuilt from file: {title or '?'} by {author_str}")
    for i, res in enumerate(ranked, 1):
        click.echo(_format_scraper_result(res, i))
    click.echo("  [i] Use inbuilt metadata only (skip scraper fill)")
    click.echo("  [u] Paste a scraper URL")
    click.echo("  [s] Skip this file")
    click.echo("  [a] Abort (skip this file)")
    click.echo("  [d] Delete this file")
    while True:
        ans = (
            click.prompt("Choose a result", type=str, default="", show_default=False)
            .strip()
            .lower()
        )
        if ans in ("i", "inbuilt", "n"):
            return None
        if ans in ("s", "skip", "a", "abort"):
            return "skip"
        if ans in ("d", "delete"):
            return "delete"
        if ans in ("u", "url"):
            from simurg.metadata.enricher import supported_url_domains

            pasted = click.prompt(
                "Paste a book-page URL (" + ", ".join(sorted(supported_url_domains())) + ")",
                type=str,
            ).strip()
            res = _scrape_from_url(pasted, inbuilt)
            if res:
                return res
            click.secho("URL scrape failed or unsupported — pick a result above.", fg="yellow")
            continue
        if ans.isdigit():
            n = int(ans)
            if 1 <= n <= len(ranked):
                return ranked[n - 1]
        click.secho(f"Invalid choice — pick 1-{len(ranked)}, i, u, s, a or d.", fg="yellow")


def _norm_merge(v):
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip().lower() for x in v if str(x).strip()]
    return [str(v).strip().lower()]


def _fmt_merge(v, maxlen=80):
    if not v:
        return "(none)"
    if isinstance(v, (list, tuple)):
        s = ", ".join(str(x) for x in v)
    else:
        s = str(v).replace("\n", " ").strip()
    if len(s) > maxlen:
        s = s[: maxlen - 1].rstrip() + "…"
    return s or "(none)"


def _merge_lists(*lists):
    out = []
    seen = set()
    for lst in lists:
        for x in lst or []:
            x = str(x).strip()
            if x and x.lower() not in seen:
                seen.add(x.lower())
                out.append(x)
    return out


def _prompt_field_merge(
    inbuilt: dict, scraper: dict, metadata: dict, dry_run: bool = False
) -> dict:
    """Let the user keep/override/append fields from the file metadata vs the chosen scraper.

    Returns an overrides dict (metadata key -> value); {} means keep everything as merged.
    Only fields with a real difference between file and scraper are prompted.
    """
    from simurg.metadata.combine import detect_edition

    def _edition(d):
        return d.get("edition") or detect_edition(d.get("title") or "")

    rows = [
        (
            "title",
            "Title",
            lambda d: d.get("title"),
            lambda d: d.get("title"),
            metadata.get("title"),
            False,
        ),
        (
            "authors",
            "Author(s)",
            lambda d: d.get("authors"),
            lambda d: d.get("authors"),
            metadata.get("authors"),
            True,
        ),
        (
            "year",
            "Year",
            lambda d: d.get("year"),
            lambda d: d.get("year") or d.get("publish_year"),
            metadata.get("year"),
            False,
        ),
        (
            "publisher",
            "Publisher",
            lambda d: d.get("publisher"),
            lambda d: d.get("publisher"),
            metadata.get("publisher"),
            False,
        ),
        (
            "isbn",
            "ISBN",
            lambda d: d.get("isbn"),
            lambda d: d.get("isbn"),
            metadata.get("isbn"),
            False,
        ),
        (
            "description",
            "Description",
            lambda d: d.get("description"),
            lambda d: d.get("description") or d.get("synopsis"),
            metadata.get("album_desc") or metadata.get("description"),
            True,
        ),
        (
            "edition",
            "Edition",
            _edition,
            _edition,
            metadata.get("edition") or _edition(metadata),
            False,
        ),
        (
            "illustrators",
            "Illustrator(s)",
            lambda d: d.get("illustrators"),
            lambda d: d.get("illustrators"),
            metadata.get("illustrators"),
            True,
        ),
    ]
    to_review = []
    for key, label, iget, sget, cur, appendable in rows:
        iv, sv = iget(inbuilt), sget(scraper)
        if not _norm_merge(iv) and not _norm_merge(sv):
            continue
        if _norm_merge(iv) == _norm_merge(sv):
            continue
        to_review.append((key, label, iv, sv, cur, appendable))
    if not to_review:
        return {}

    click.secho(
        "\nFile metadata vs chosen scraper — choose what to keep/append:", fg="cyan", bold=True
    )
    click.echo(f"  {'Field':12} {'file':28} {'scraper':28} {'current'}")
    for _key, label, iv, sv, cur, _ in to_review:
        click.echo(f"  {label:12} {_fmt_merge(iv):28} {_fmt_merge(sv):28} {_fmt_merge(cur)}")

    overrides = {}
    for key, label, iv, sv, _cur, appendable in to_review:
        if dry_run:
            click.secho(f"  {label}: keeping default (dry-run, no prompt)", fg="yellow")
            continue
        opts = "[i] file [s] scraper" + (", [b] append" if appendable else "") + " [k] keep current"
        ans = click.prompt(f"  {label} — {opts}", default="k", show_default=False).strip().lower()
        if ans in ("x", "skip"):
            break
        if ans == "i":
            overrides[key] = iv
        elif ans == "s":
            overrides[key] = sv
        elif ans == "b" and appendable:
            if isinstance(iv, list) or isinstance(sv, list):
                overrides[key] = _merge_lists(iv, sv)
            else:
                parts = [x for x in (sv, iv) if x]
                overrides[key] = "\n\n".join(str(x).strip() for x in parts)
    return overrides


def _apply_field_overrides(metadata: dict, overrides: dict) -> None:
    """Apply user field-merge overrides onto the built metadata dict (in place)."""
    for key, val in overrides.items():
        if not val:
            continue
        if key == "description":
            metadata["description"] = val
            metadata["album_desc"] = val
            metadata["book_desc"] = val
        elif key == "edition":
            from simurg.metadata.combine import detect_edition

            metadata["edition"] = val
            rt = (metadata.get("remaster_title") or metadata.get("title") or "").strip()
            # Only append if the release title doesn't already carry edition wording
            if not detect_edition(rt):
                metadata["remaster_title"] = f"{rt} ({val})".strip()
        elif key == "title":
            from simurg.metadata.combine import strip_edition_from_canonical

            metadata["title"] = strip_edition_from_canonical(str(val))
            metadata["remaster_title"] = str(val)
        elif key in ("authors", "illustrators", "translators", "editors"):
            metadata[key] = val
        else:
            metadata[key] = val


@click.group()
@click.version_option(__version__, "--version", prog_name="smoked-simurg")
def cli():
    pass


@cli.command("up")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--dry-run", is_flag=True, help="Print payload, skip torrent+upload")
@click.option("--group-id", type=int, default=None, help="Force existing Publication group id")
@click.option("--cover", type=str, default=None, help="Cover URL override")
@click.option("--no-rename", is_flag=True, help="Skip filename sanitize")
@click.option("--no-review", is_flag=True, help="Skip the interactive editor metadata review step")
@click.option(
    "--category",
    type=click.Choice(["ebooks", "magazines"]),
    default="ebooks",
    help="Upload category: ebooks (default) or magazines. Selects the scraper + payload path.",
)
@click.option(
    "--source",
    type=click.Choice(sorted(SOURCE_LABELS), case_sensitive=False),
    default=None,
    help="Source label (Retail/Scan/OCR/Convert/Other). Overwrites scraped value. Never guessed; defaults to Other when unset.",
)
@click.option(
    "--format",
    "format_",
    type=click.Choice(
        sorted(set(FORMAT_MAP.values()) | set(MAGAZINE_FORMAT_MAP.values())),
        case_sensitive=False,
    ),
    default=None,
    help="Override format (ebooks: PDF/EPUB/MOBI/AZW3/DJVU; magazines: PDF/CBR/CBZ/DJVU). Overwrites scraped/file value.",
)
@click.option(
    "--language",
    type=click.Choice(["English", "Japanese", "Turkish"], case_sensitive=False),
    default=None,
    help="Override language (English/Japanese/Turkish). Overwrites scraped/file value.",
)
@click.option(
    "--url",
    type=str,
    default=None,
    help="Paste a book-page URL (openlibrary/googlebooks/bookbrainz/abebooks/archive.org/loc) to scrape metadata from directly, skipping the auto scraper search.",
)
@click.option(
    "--limit",
    type=int,
    default=BATCH_LIMIT_DEFAULT,
    show_default=True,
    help="Max files processed per run (0 = unlimited). Ebooks stop the directory walk at N; magazines scan fully for pack detection. Rerun the same command for the next batch.",
)
@click.option(
    "--no-early-dupe",
    is_flag=True,
    help="Skip the pre-enrichment Simurg dupe pre-check (file tags only).",
)
def up(
    directory,
    dry_run,
    group_id,
    cover,
    no_rename,
    category,
    source,
    format_,
    language,
    no_review,
    url,
    limit,
    no_early_dupe,
):
    """Batch upload N unrelated files -> N torrents (ebooks or magazines).

    Ebooks stop the directory walk at --limit files per run; rerun the
    same command for the next batch (processed files move to staging).
    Magazines scan the whole directory so packs stay complete.

    Examples:

    \b
      python -m simurg up ./batch --dry-run
      python -m simurg up ./batch --source Retail --limit 50
      python -m simurg up ./mags --category magazines --no-review
    """
    dir_path = Path(directory)
    # Validate directory — support nested batches (e.g. books/National Geographic - 2000/*.pdf)
    files_all = list(dir_path.iterdir())
    subdirs = [p for p in files_all if p.is_dir()]
    if subdirs:
        click.secho(
            f"Found subdirectories: {[s.name for s in subdirs]} — scanning recursively",
            fg="cyan",
        )
    is_mag = category == "magazines"
    allowed = MAGAZINE_EXTENSIONS if is_mag else ALLOWED_EXTENSIONS
    format_map = MAGAZINE_FORMAT_MAP if is_mag else FORMAT_MAP
    # Ebooks stop the directory walk at --limit (bounded scan). Magazines
    # walk everything so Year/Decade pack detection sees complete packs.
    bounded_scan = (not is_mag) and limit is not None and limit > 0
    # Recursively collect all files (covers both top-level batches and year-subfolders).
    # Skipped for bounded ebook scans — the collector below walks instead.
    files: list[Path] = []
    if not bounded_scan:
        files = [p for p in dir_path.rglob("*") if p.is_file()]

    # Explicit CLI overrides: normalize and validate per category (overwrite scraped)
    fmt_override = format_.upper() if format_ else None
    if fmt_override:
        allowed_formats = set(MAGAZINE_FORMAT_MAP.values()) if is_mag else set(FORMAT_MAP.values())
        if fmt_override not in allowed_formats:
            kind = "magazines (PDF/CBR/CBZ/DJVU)" if is_mag else "ebooks (PDF/EPUB/MOBI/AZW3/DJVU)"
            click.secho(f"{FAIL_SYMBOL} --format {fmt_override} not allowed for {kind}", fg="red")
            raise click.Abort()
    lang_override = None
    if language:
        _lang_map = {"english": "English", "japanese": "Japanese", "turkish": "Turkish"}
        lang_override = _lang_map.get(language.lower(), language)
    source_override = source  # Choice already canonical; None if not set

    # Filter allowed (bounded ebook scans stop the walk at --limit)
    ebook_files: list[Path] = []
    txt_found: list[Path] = []
    hit_limit = False
    total_files: int | None = None
    if bounded_scan:
        assert limit is not None
        ebook_files, txt_found, hit_limit = _collect_batch_files(dir_path, allowed, limit)
    else:
        for f in files:
            ext = f.suffix.lower()
            if ext in allowed:
                ebook_files.append(f)
            elif ext == ".txt":
                txt_found.append(f)
    if txt_found:
        click.secho(
            f"{FAIL_SYMBOL} Rejecting .txt files per I31 (no transcode): {[t.name for t in txt_found]}",
            fg="red",
        )
    if not ebook_files:
        kinds = (
            "magazines (PDF/CBR/CBZ/DJVU)" if is_mag else "ebooks (.pdf .epub .mobi .azw3 .djvu)"
        )
        click.secho(f"{FAIL_SYMBOL} No {kinds} files found", fg="red")
        raise click.Abort()
    if bounded_scan:
        shown = [f.name for f in ebook_files[:10]]
        suffix = f" (+{len(ebook_files) - 10} more this run)" if len(ebook_files) > 10 else ""
        click.secho(
            f"{OK_SYMBOL} Found {category} file(s), processing {len(ebook_files)} this run"
            f" (walk stopped at --limit {limit}): {shown}{suffix}",
            fg="green",
        )
        if hit_limit:
            click.secho(
                f"{SKIP_SYMBOL} Walk stopped at --limit {limit} — more files may remain,"
                " rerun the same command for the next batch"
                + (
                    " (note: --no-rename leaves files in place, so rerun repeats this batch)"
                    if no_rename
                    else ""
                ),
                fg="yellow",
            )
    else:
        ebook_files = sorted(ebook_files, key=lambda p: p.name.lower())
        total_files = len(ebook_files)
        hit_limit = bool(limit is not None and limit > 0 and total_files > limit)
    if not bounded_scan and hit_limit:
        # Magazines only reach here (ebook limits use the bounded walk above).
        # Pack-aware slice: filename parse is cheap, so detect packs on the
        # full list, then expand the cut to finish any open pack.
        assert limit is not None
        batch_set = set(ebook_files[:limit])
        try:
            for packs in (
                _detect_magazine_year_packs(ebook_files),
                _detect_magazine_decade_packs(ebook_files),
            ):
                for pack_files in packs.values():
                    if any(p in batch_set for p in pack_files):
                        batch_set.update(pack_files)
        except Exception:
            pass
        ebook_files = [p for p in ebook_files if p in batch_set]
    if not bounded_scan:
        assert total_files is not None
        shown = [f.name for f in ebook_files[:10]]
        suffix = f" (+{len(ebook_files) - 10} more this run)" if len(ebook_files) > 10 else ""
        click.secho(
            f"{OK_SYMBOL} Found {total_files} {category} file(s), processing {len(ebook_files)} this run: {shown}{suffix}",
            fg="green",
        )
        if total_files > len(ebook_files):
            click.secho(
                f"{SKIP_SYMBOL} {total_files - len(ebook_files)} file(s) remaining — rerun the same command for the next batch"
                + (
                    " (note: --no-rename leaves files in place, so rerun repeats this batch)"
                    if no_rename
                    else ""
                ),
                fg="yellow",
            )

    # Group detection pre-flight (ebooks only — magazines are individual issues).
    # Per-group decision (docs/ux-improvements.md §3.6) instead of aborting the
    # whole batch: upload separately / skip these files / abort all.
    dup_skipped: set[Path] = set()
    if not is_mag:
        dups = _group_detection(ebook_files)
        if dups:
            click.secho(
                f"{WARN_SYMBOL} {len(dups)} possible duplicate Publication group(s) found",
                fg="yellow",
                bold=True,
            )
            click.secho(
                "Tip: publisher-issued packs belong in a subdir like "
                "'Dune - Frank Herbert (2020) [EPUB Retail]/' — run on that subdir instead.",
                fg="cyan",
            )
            for key, flist in dups.items():
                click.echo(f"  '{key.split('||')[0]}':")
                for f in flist:
                    click.echo(f"      - {f.name}")
                ans = (
                    click.prompt(
                        "  Upload these separately anyway?",
                        type=str,
                        default="s",
                        show_default=False,
                    )
                    .strip()
                    .lower()
                )
                if ans in ("a", "abort"):
                    raise click.Abort()
                if ans in ("u", "upload", "y"):
                    click.secho(
                        "  Keeping files — they will still be uploaded as separate "
                        "single-file torrents.",
                        fg="yellow",
                    )
                else:
                    dup_skipped.update(f.resolve() for f in flist)
            if dup_skipped:
                ebook_files = [f for f in ebook_files if f.resolve() not in dup_skipped]
                click.secho(
                    f"{SKIP_SYMBOL} Skipping {len(dup_skipped)} grouped file(s); "
                    f"{len(ebook_files)} file(s) remain",
                    fg="yellow",
                )
            if not ebook_files:
                click.secho(f"{FAIL_SYMBOL} No files left to process", fg="red")
                raise click.Abort()

    # Load config & tracker (allow dry-run without valid session)
    gazelle_site = None
    if not dry_run:
        try:
            from simurg.config import load_config

            load_config()
            from simurg.trackers.simurg import SimurgApi

            gazelle_site = SimurgApi()
            click.echo(f"{OK_SYMBOL} Authenticated to {fmt_url(gazelle_site.base_url)}")
        except Exception as e:
            click.secho(f"{FAIL_SYMBOL} Failed to authenticate: {e}", fg="red")
            click.secho(
                "Hint: run 'make checkconf' to test the session cookie, or fix "
                "config.toml (session/announce_url). Use --dry-run to test without the tracker.",
                fg="yellow",
            )
            raise click.Abort() from e
    else:
        # Still try to load config but don't require auth
        try:
            from simurg.config import get_config, load_config

            load_config()
            cfg = get_config()
            # create dummy site for dupe search if possible? But dry-run skip dupe if no session
            try:
                from simurg.trackers.simurg import SimurgApi

                # If session empty, SimurgApi will create dummy; we can still use for dupe if session present
                gazelle_site = SimurgApi()
                if not gazelle_site.cookie or gazelle_site.cookie == "":
                    gazelle_site = None
                    click.secho(
                        "Dry-run: no session cookie, skipping Simurg dupe search", fg="yellow"
                    )
            except Exception as e:
                click.secho(
                    f"Dry-run: tracker init failed ({e}), skipping dupe search", fg="yellow"
                )
                gazelle_site = None
        except Exception:
            gazelle_site = None
            click.secho("Dry-run: no config.toml, skipping tracker dupe search", fg="yellow")

    uploaded = 0
    skipped = 0
    deleted = 0
    failed = 0

    # Determine dottorrents dir
    dottorrents_dir = ".torrents"
    try:
        from simurg.config import get_config

        cfg = get_config()
        dottorrents_dir = str(cfg.directory.get("dottorrents_dir", ".torrents"))
        # Simurg specific override
        tcfg = cfg.get_tracker_cfg("simurg")
        if tcfg.get("dottorrents_dir", ""):
            dottorrents_dir = str(tcfg.get("dottorrents_dir"))
    except Exception:
        pass
    os.makedirs(dottorrents_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Magazine Year/Decade Pack handling (auto-prompt when subdirectories)
    # per docs/rules.txt:129 and user request: "1995 Complete Year" etc.
    # ------------------------------------------------------------------
    if is_mag and subdirs:
        year_packs = _detect_magazine_year_packs(ebook_files)
        decade_packs = _detect_magazine_decade_packs(ebook_files)
        if year_packs or decade_packs:
            # Auto-prompt when any subdirectory is detected
            try:
                pack_choice = _prompt_magazine_collection_mode(
                    year_packs, decade_packs, len(ebook_files), dry_run=dry_run
                )
            except click.Abort:
                raise
            if pack_choice == "abort":
                raise click.Abort()
            if pack_choice in ("year", "decade"):
                packs = year_packs if pack_choice == "year" else decade_packs
                pack_type = "Year Pack" if pack_choice == "year" else "Decade Pack"
                click.secho(
                    f"\nProcessing {len(packs)} {pack_type}(s) from {len(ebook_files)} issue(s)...",
                    fg="cyan",
                    bold=True,
                )
                # Process each pack as one torrent (multi-file directory)
                for pack_idx, ((_, pack_key), pack_files) in enumerate(sorted(packs.items())):
                    # Canonical display from first file
                    from simurg.metadata.magazine import decode_magazine_filename
                    from simurg.metadata.scrapers.util import issue_label as _issue_label_util

                    first_meta = decode_magazine_filename(pack_files[0])
                    canonical_display = (
                        first_meta.get("canonical_title")
                        or first_meta.get("title")
                        or pack_files[0].stem
                    ).strip()
                    # Determine uniform format (CLI --format overwrites file-derived)
                    if fmt_override:
                        fmt = fmt_override
                    else:
                        fmts = set()
                        for fp in pack_files:
                            try:
                                fm = (
                                    decode_magazine_filename(fp).get("format")
                                    or fp.suffix.lstrip(".").upper()
                                )
                            except Exception:
                                fm = fp.suffix.lstrip(".").upper()
                            fmts.add(fm.upper())
                        if len(fmts) > 1:
                            click.secho(
                                f"{FAIL_SYMBOL} Skipping pack {canonical_display} {pack_key}: mixed formats {fmts} (docs/rules.txt:127)",
                                fg="red",
                            )
                            skipped += 1
                            continue
                        fmt = next(iter(fmts)) if fmts else "PDF"

                    # Collect per-file infos for manifest (pages + issue label)
                    file_infos: list[tuple[Path, int | None, str]] = []
                    for fp in sorted(pack_files, key=lambda p: p.name.lower()):
                        try:
                            _fm = _decode_inbuilt_quick(fp)
                            pages = _fm.get("page_count")
                        except Exception:
                            pages = None
                        try:
                            m = decode_magazine_filename(fp)
                            label = _issue_label_util(
                                m.get("issue_date"),
                                m.get("issue_date_precision"),
                                m.get("volume"),
                                m.get("issue_number"),
                            )
                            if not label:
                                label = fp.stem
                        except Exception:
                            label = fp.stem
                        file_infos.append((fp, pages, label))

                    # Enrich Publication-level metadata via scrapers (once per pack)
                    scraper_data: dict = {}
                    if url:
                        scraper_data = (
                            _scrape_from_url(
                                url, {"canonical_title": canonical_display}, dry_run=dry_run
                            )
                            or {}
                        )
                    if not scraper_data:
                        try:
                            from simurg.metadata.enricher import (
                                rank_results,
                                search_magazine_scrapers,
                            )

                            # Fake inbuilt for pack-level search (canonical only)
                            fake_inbuilt = {
                                "canonical_title": canonical_display,
                                "title": canonical_display,
                            }
                            results = search_magazine_scrapers(fake_inbuilt)
                            if results:
                                scraper_data = rank_results(results) or {}
                        except Exception as e:
                            click.secho(
                                f"Pack scraper failed for {canonical_display} {pack_key}: {e}",
                                fg="yellow",
                            )
                            scraper_data = {}

                    # Build pack metadata (manifest in album_desc per docs/rules.txt:131)
                    from simurg.metadata.magazine import build_magazine_pack_metadata

                    try:
                        metadata = build_magazine_pack_metadata(
                            canonical=canonical_display,
                            pack_type=pack_type,
                            pack_key=int(pack_key),
                            files=pack_files,
                            scraper=scraper_data,
                            fmt=fmt,
                            source=source_override,
                            file_infos=file_infos,
                        )
                    except Exception as e:
                        click.secho(
                            f"Failed to build pack metadata for {canonical_display} {pack_key}: {e}",
                            fg="red",
                        )
                        failed += 1
                        continue

                    # CLI overwrites (before review so user sees final values)
                    if source_override is not None:
                        metadata["source"] = source_override
                    if fmt_override:
                        metadata["format"] = fmt_override
                    if lang_override:
                        metadata["language"] = lang_override

                    # [review] Interactive metadata review for packs (user request): let the
                    # user correct Publication / release / coverage metadata before upload,
                    # even for Year/Decade packs. Mirrors the individual-issue review
                    # (cli.py:2239). review_metadata guards non-TTY (still prompts in --dry-run when TTY).
                    if not no_review:
                        try:
                            from simurg.metadata.review import review_metadata

                            reviewed = review_metadata(metadata, is_mag=True, dry_run=dry_run)
                            if isinstance(reviewed, str):
                                if reviewed == "delete":
                                    try:
                                        names = ", ".join(p.name for p in pack_files[:3])
                                        if len(pack_files) > 3:
                                            names += f" (+{len(pack_files) - 3} more)"
                                        if click.confirm(
                                            f"Delete {len(pack_files)} pack file(s) ({names})?",
                                            default=False,
                                        ):
                                            if not dry_run:
                                                for p in pack_files:
                                                    try:
                                                        Path(p).unlink(missing_ok=True)
                                                    except Exception:
                                                        pass
                                            deleted += 1
                                        else:
                                            skipped += 1
                                    except (click.Abort, EOFError):
                                        skipped += 1
                                else:
                                    click.secho(
                                        f"Skipping pack {canonical_display} {pack_key} per user choice",
                                        fg="yellow",
                                    )
                                    skipped += 1
                                continue
                            metadata = reviewed
                        except click.Abort:
                            raise
                        except Exception as e:
                            click.secho(f"Pack metadata review failed: {e}", fg="yellow")

                    # Validation (same as individual)
                    from simurg.metadata.magazine import validate_magazine_metadata

                    missing = validate_magazine_metadata(metadata)
                    if "language" in missing:
                        found = (metadata.get("language") or "").strip() or "(empty)"
                        click.secho(
                            f"{FAIL_SYMBOL} Skipping {canonical_display} {pack_key}: language '{found}' not allowed per docs/magazine.txt §10",
                            fg="red",
                            bold=True,
                        )
                        skipped += 1
                        continue
                    if "release_title" in missing:
                        click.secho(
                            f"{FAIL_SYMBOL} Skipping {canonical_display} {pack_key}: missing release_title",
                            fg="red",
                            bold=True,
                        )
                        skipped += 1
                        continue
                    if missing:
                        click.secho(
                            f"Warning: missing fields {missing} for pack {canonical_display} {pack_key}",
                            fg="yellow",
                        )

                    # Show pack metadata summary
                    click.secho("\nPack metadata:", fg="cyan")
                    for k in [
                        "title",
                        "release_title",
                        "year",
                        "publisher",
                        "release_type",
                        "page_count",
                        "format",
                        "source",
                        "language",
                    ]:
                        click.echo(f" {k:15}: {metadata.get(k)}")
                    # Show manifest preview (first 5 + total)
                    manifest_preview = metadata.get("album_desc", "").split("\n")[:6]
                    for line in manifest_preview:
                        click.echo(f"  {line}")
                    if len(metadata.get("album_desc", "").split("\n")) > 6:
                        click.echo(f"  ... ({len(pack_files)} issues total)")

                    # Cover handling — same as individual: scraper cover -> fallback -> none
                    # Priority: --cover > edited image (review [img]) > scraper > DuckDuckGo
                    cover_url = None
                    cover_scraper = metadata.get("cover_url_scraper")
                    edited_image = (metadata.get("image") or "").strip()
                    temp_cover = None
                    if cover and not temp_cover:
                        tmp = _download_url_to_temp(cover)
                        if tmp:
                            temp_cover = tmp
                            click.secho(f"Downloaded cover (override): {cover}", fg="green")
                    if edited_image and not temp_cover and edited_image.lower().startswith("http"):
                        tmp = _download_url_to_temp(edited_image)
                        if tmp:
                            temp_cover = tmp
                            click.secho(
                                f"Downloaded cover from edited image: {edited_image}", fg="green"
                            )
                        else:
                            click.secho(
                                f"Failed to download edited image URL (missing/invalid): {edited_image}",
                                fg="yellow",
                            )
                    if cover_scraper and not temp_cover:
                        tmp = _download_url_to_temp(cover_scraper)
                        if tmp:
                            temp_cover = tmp
                            click.secho(
                                f"Downloaded cover from scraper: {cover_scraper}", fg="green"
                            )
                        else:
                            click.secho(
                                f"Failed to download scraper cover (missing/invalid): {cover_scraper}",
                                fg="yellow",
                            )
                    if not temp_cover:
                        try:
                            from simurg.metadata.scrapers.duckduckgo import fetch_duckduckgo_cover

                            dd_path = fetch_duckduckgo_cover(canonical_display, [])
                            if dd_path:
                                temp_cover = dd_path
                                click.secho(f"Fallback DuckDuckGo cover: {dd_path}", fg="green")
                        except Exception:
                            pass
                    if not temp_cover and not dry_run:
                        try:
                            manual = _prompt_manual_cover(
                                metadata.get("title") or canonical_display,
                                metadata.get("authors") or [],
                                dry_run=dry_run,
                            )
                            if manual == "skip":
                                click.secho(
                                    f"Skipping pack {canonical_display} {pack_key} per user choice",
                                    fg="yellow",
                                )
                                skipped += 1
                                continue
                            if manual == "delete":
                                skipped += 1
                                continue
                            if manual:
                                temp_cover = manual
                        except click.Abort:
                            raise
                    if temp_cover:
                        try:
                            from simurg.images import get_uploader

                            cfg_img = get_config()
                            uploader_name = str(
                                cfg_img.image.get("cover_uploader", "ptscreens") or "ptscreens"
                            )
                            Uploader = get_uploader(uploader_name)
                            rehost_url, _ = Uploader().upload_file(temp_cover)
                            cover_url = rehost_url
                            click.secho("Cover rehosted: ", fg="green", bold=True, nl=False)
                            click.echo(fmt_url(cover_url))
                        except Exception as e:
                            click.secho(f"{FAIL_SYMBOL} Cover rehost failed: {e}", fg="red")
                            cover_url = None
                    else:
                        click.secho(
                            "No cover found for pack. Continuing without image.", fg="yellow"
                        )
                        cover_url = None
                    if cover_url and not dry_run:
                        try:
                            confirmed = _confirm_rehosted_cover(
                                cover_url, temp_cover, cover_path=None, dry_run=dry_run
                            )
                            if confirmed == "skip":
                                if temp_cover:
                                    try:
                                        Path(temp_cover).unlink(missing_ok=True)
                                    except Exception:
                                        pass
                                skipped += 1
                                continue
                            if confirmed == "delete":
                                if temp_cover:
                                    try:
                                        Path(temp_cover).unlink(missing_ok=True)
                                    except Exception:
                                        pass
                                skipped += 1
                                continue
                            assert isinstance(confirmed, tuple)
                            cover_url, temp_cover = confirmed
                        except click.Abort:
                            raise
                    if temp_cover:
                        try:
                            Path(temp_cover).unlink(missing_ok=True)
                        except Exception:
                            pass
                    metadata["image"] = cover_url or ""

                    # Staging — create pack directory and copy files
                    # Determine staging dir same as individual path
                    staging_dir = None
                    try:
                        from simurg.config import get_config as _get_cfg

                        _cfg_tmp = _get_cfg()
                        staging_dir = str(_cfg_tmp.directory.get("staging_dir", "") or "").strip()
                        if not staging_dir:
                            staging_dir = str(
                                _cfg_tmp.directory.get("upload_directory", "")
                                or _cfg_tmp.directory.get("download_directory", "")
                                or ""
                            ).strip()
                        if not staging_dir:
                            staging_dir = ".staging"
                    except Exception:
                        staging_dir = ".staging"
                    staging_path = Path(staging_dir)
                    if not staging_path.is_absolute():
                        staging_path = Path.cwd() / staging_path
                    try:
                        staging_path.mkdir(parents=True, exist_ok=True)
                    except Exception:
                        pass
                    # Pack dir name: "{Canonical} - {Release title}" sanitized
                    pack_dir_name = _sanitize_filename(
                        f"{canonical_display} - {metadata.get('release_title')}"
                    )
                    # Avoid double "Complete Year" duplication if canonical already contains year? Keep as is per user example "Playboy - 1995 Complete Year"
                    pack_dir = staging_path / pack_dir_name
                    try:
                        pack_dir.mkdir(parents=True, exist_ok=True)
                        # Copy files sorted
                        for src in sorted(pack_files, key=lambda p: p.name.lower()):
                            dest = pack_dir / src.name
                            if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                                shutil.copy2(str(src), str(dest))
                        click.secho(
                            f"Staged pack to {pack_dir} ({len(pack_files)} files)", fg="green"
                        )
                    except Exception as e:
                        click.secho(f"Pack staging failed for {pack_dir}: {e}", fg="red")
                        failed += 1
                        continue

                    # Build release_desc
                    from simurg.uploader.payload import build_release_desc

                    metadata["release_desc"] = build_release_desc(
                        metadata, metadata.get("source_urls")
                    )
                    if metadata.get("source") == "Other":
                        metadata["release_desc"] = (
                            metadata["release_desc"]
                            + "\n[b]Source note:[/b] marked Other (provenance unverified; please confirm)."
                        ).strip()

                    # Dupe and request check (pack-level)
                    search_gid = group_id
                    request_id = None
                    if gazelle_site:
                        from simurg.uploader.dupe import (
                            check_existing_group,
                            generate_dupe_search_strs,
                        )

                        searchstrs = generate_dupe_search_strs(metadata["title"], [], None)
                        # Include pack release title in search
                        searchstrs = [
                            *searchstrs,
                            f"{canonical_display} {metadata.get('release_title')}",
                        ]
                        click.secho(f"Searching Simurg for dupes: {searchstrs}", fg="yellow")
                        try:
                            result_gid = check_existing_group(
                                gazelle_site, searchstrs, group_id_override=group_id
                            )
                            if result_gid == "skip":
                                click.secho(
                                    f"Skipping pack {pack_dir_name} per user choice", fg="yellow"
                                )
                                skipped += 1
                                continue
                            if result_gid == "delete":
                                skipped += 1
                                continue
                            search_gid = result_gid
                        except click.Abort:
                            raise
                        except Exception as e:
                            click.secho(f"Dupe check failed: {e}", fg="yellow")

                    # Generate torrent + upload (directory)
                    try:
                        from simurg.uploader.upload import prepare_and_upload

                        if gazelle_site is None:
                            # Dry-run without session: dummy site for torrent announce
                            announce = ""
                            try:
                                from simurg.config import get_config as _get_cfg2

                                _cfg_t = _get_cfg2()
                                tcfg = _cfg_t.get_tracker_cfg("simurg")
                                announce = str(tcfg.get("announce_url") or "")
                            except Exception:
                                announce = ""
                            if not announce:
                                announce = "https://tracker.simurg.world/announce"

                            class TorrentCtx:
                                announce = ""
                                dot_torrents_dir = dottorrents_dir
                                site_string = "SIM"

                            torrent_site = TorrentCtx()
                            torrent_site.announce = announce
                        else:
                            torrent_site = gazelle_site

                        prepare_and_upload(
                            torrent_site,
                            pack_dir,
                            search_gid,
                            metadata,
                            cover_url,
                            request_id,
                            dry_run=dry_run,
                            category=category,
                        )
                        uploaded += 1
                        # Rate-limit only after an actual upload (never on skip/delete/fail),
                        # and never after the last pack.
                        if not dry_run and pack_idx < len(packs) - 1:
                            _rate_limit_wait(7, label=f"(pack {pack_idx + 1}/{len(packs)})")
                    except click.Abort:
                        raise
                    except Exception as e:
                        click.secho(f"Failed to upload {pack_dir_name}: {e}", fg="red")
                        failed += 1
                        continue

                # Summary for packs
                click.secho("\n" + "=" * 60, fg="cyan")
                if dry_run:
                    color = "green" if failed == 0 else "yellow"
                    click.secho(
                        f"{OK_SYMBOL if failed == 0 else WARN_SYMBOL} Summary: Dry-run — prepared {uploaded}/{len(packs)} pack(s) (torrents in {dottorrents_dir}/, no uploads sent), skipped {skipped}, deleted {deleted}, failed {failed}",
                        fg=color,
                        bold=True,
                    )
                else:
                    color = "green" if failed == 0 else "yellow"
                    sym = OK_SYMBOL if failed == 0 else WARN_SYMBOL
                    click.secho(
                        f"{sym} Summary: Uploaded {uploaded}/{len(packs)} pack(s), skipped {skipped}, deleted {deleted}, failed {failed} — check {dottorrents_dir}/",
                        fg=color,
                        bold=True,
                    )
                return

    for idx, filepath in enumerate(ebook_files):
        click.secho("\n" + "=" * 60, fg="cyan")
        click.secho(f"Processing: {filepath.name}", fg="cyan", bold=True)
        ext = filepath.suffix.lower()
        fmt = fmt_override if fmt_override else format_map.get(ext, ext.upper().lstrip("."))

        # [2] Decode inbuilt
        if is_mag:
            from simurg.metadata.magazine import decode_magazine_filename

            inbuilt = decode_magazine_filename(filepath)
            # Fill year from issue date when missing
            if not inbuilt.get("year") and inbuilt.get("issue_date"):
                inbuilt["year"] = year_from(inbuilt["issue_date"])
            # Fill page_count from the actual PDF/DJVU file (docs/magazine.txt §13:
            # actual PDF page count, not scraper estimate). decode_magazine_filename
            # does not read the file, so probe the file directly.
            if not inbuilt.get("page_count"):
                try:
                    _file_meta = _decode_inbuilt_quick(filepath)
                    if _file_meta.get("page_count"):
                        inbuilt["page_count"] = _file_meta["page_count"]
                except Exception:
                    pass
        else:
            inbuilt = _decode_inbuilt_quick(filepath)
            if inbuilt.get("is_encrypted"):
                click.secho(
                    f"{SKIP_SYMBOL} Skipping {filepath.name}: PDF is encrypted "
                    "(rules.txt:36 forbids encrypted uploads)",
                    fg="red",
                    bold=True,
                )
                skipped += 1
                continue
            # Normalize inbuilt authors split
            if inbuilt.get("authors"):
                inbuilt["authors"] = _normalize_authors(inbuilt["authors"])
            # Collapse whitespace title
            if inbuilt.get("title"):
                inbuilt["title"] = re.sub(r"\s+", " ", inbuilt["title"].strip())
            # Clean ISBN dashes
            if inbuilt.get("isbn"):
                inbuilt["isbn"] = re.sub(r"[^0-9Xx]", "", str(inbuilt["isbn"]))
                if len(inbuilt["isbn"]) not in (10, 13):
                    inbuilt["isbn"] = None

        # Ebook-only: parse staged filename / author-title fallback
        is_staged_file = filepath.parent.name == ".staging" or "staging" in str(filepath.parent)
        if (not is_mag) and is_staged_file and " - " in filepath.stem:
            left, right = filepath.stem.split(" - ", 1)
            left = left.strip()
            right = right.strip()
            # Staged: Title - Author (year) [ISBN]  |  legacy: Title - Author year ISBN
            m = re.search(r"^(.*?)\s+\((\d{4})\)\s+\[(\d{10,13})\]$", right)
            m2 = re.search(r"^(.*?)\s+\((\d{4})\)$", right) if not m else None
            m3 = re.search(r"^(.*?)\s+(\d{4})\s+(\d{10,13})$", right) if not m and not m2 else None
            m4 = re.search(r"^(.*?)\s+(\d{4})$", right) if not m and not m2 and not m3 else None
            if m:
                # Title - Author (year) [ISBN]
                inbuilt["title"] = left
                inbuilt["authors"] = _normalize_authors([m.group(1).strip()])
                if not inbuilt.get("year"):
                    try:
                        inbuilt["year"] = int(m.group(2))
                    except Exception:
                        pass
                if not inbuilt.get("isbn"):
                    inbuilt["isbn"] = m.group(3)
                click.secho(
                    f"Parsed staged filename: title='{left}' author='{m.group(1).strip()}' year={m.group(2)} isbn={m.group(3)}",
                    fg="cyan",
                )
            elif m2:
                # Title - Author (year)
                inbuilt["title"] = left
                inbuilt["authors"] = _normalize_authors([m2.group(1).strip()])
                if not inbuilt.get("year"):
                    try:
                        inbuilt["year"] = int(m2.group(2))
                    except Exception:
                        pass
                click.secho(
                    f"Parsed staged filename: title='{left}' author='{m2.group(1).strip()}' year={m2.group(2)}",
                    fg="cyan",
                )
            elif m3:
                # Legacy: Title - Author year ISBN
                inbuilt["title"] = left
                inbuilt["authors"] = _normalize_authors([m3.group(1).strip()])
                if not inbuilt.get("year"):
                    try:
                        inbuilt["year"] = int(m3.group(2))
                    except Exception:
                        pass
                if not inbuilt.get("isbn"):
                    inbuilt["isbn"] = m3.group(3)
                click.secho(
                    f"Parsed staged filename: title='{left}' author='{m3.group(1).strip()}' year={m3.group(2)} isbn={m3.group(3)}",
                    fg="cyan",
                )
            elif m4:
                # Legacy: Title - Author year
                inbuilt["title"] = left
                inbuilt["authors"] = _normalize_authors([m4.group(1).strip()])
                if not inbuilt.get("year"):
                    try:
                        inbuilt["year"] = int(m4.group(2))
                    except Exception:
                        pass
                click.secho(
                    f"Parsed staged filename: title='{left}' author='{m4.group(1).strip()}' year={m4.group(2)}",
                    fg="cyan",
                )
            else:
                # Staged without year/isbn: Title - Author
                inbuilt["title"] = left
                inbuilt["authors"] = _normalize_authors([right])
                click.secho(f"Parsed staged filename: title='{left}' author='{right}'", fg="cyan")
        elif (
            (not is_mag)
            and (not inbuilt.get("title") or not inbuilt.get("authors"))
            and " - " in filepath.stem
        ):
            left, right = filepath.stem.split(" - ", 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                if not inbuilt.get("authors"):
                    inbuilt["authors"] = _normalize_authors([left])
                if not inbuilt.get("title"):
                    inbuilt["title"] = right
                click.secho(f"Parsed from filename: author='{left}' title='{right}'", fg="cyan")

        # Fallback edition hint from the title (ebooks only — magazines use volume/issue)
        if (not is_mag) and not inbuilt.get("edition"):
            from simurg.metadata.combine import detect_edition

            inbuilt["edition"] = detect_edition(inbuilt.get("title") or "")

        # Print the metadata read from the file (before any scraping) so the
        # user can eyeball/confirm it (docs/ux-improvements.md: per-file preview).
        _print_inbuilt_metadata(inbuilt, fmt, is_mag=is_mag)

        # [2b] Early dupe pre-check from file tags only — alert before the
        # user pays enrichment-prompt cost. Late check ([5]) stays authoritative.
        if gazelle_site and not no_early_dupe and group_id is None:
            from simurg.uploader.dupe import build_early_search_strs, check_early_dupes

            early_strs = build_early_search_strs(inbuilt, filepath.stem)
            if early_strs:
                click.secho(f"Early dupe check (file tags only): {early_strs}", fg="yellow")
                try:
                    early_decision, _ = check_early_dupes(gazelle_site, early_strs)
                except click.Abort:
                    raise
                except Exception as e:
                    click.secho(f"Early dupe check failed: {e}", fg="yellow")
                    early_decision = "continue"
                if early_decision == "skip":
                    click.secho(
                        f"Skipping file {filepath.name} per early dupe choice",
                        fg="yellow",
                    )
                    skipped += 1
                    continue
                if early_decision == "delete":
                    if _confirm_delete_file(filepath, dry_run=dry_run):
                        deleted += 1
                    else:
                        skipped += 1
                    continue
                if early_decision == "abort":
                    raise click.Abort()

        # [3] Enrich — query scrapers for this category, let user pick which result to use.
        # A pasted URL (--url flag or 'u' at the prompt) routes to the matching
        # scraper and, when it resolves, is auto-used as the chosen result (the
        # user found a better page than our auto-search could).
        scraper_data: dict = {}
        url_used = False
        if url:
            scraper_data = _scrape_from_url(url, inbuilt, dry_run=dry_run) or {}
            url_used = bool(scraper_data)
        if not url_used:
            try:
                scraper_results = None
                if (not url_used) and (scraper_results is None):
                    if is_mag:
                        from simurg.metadata.enricher import (
                            rank_results,
                            search_magazine_scrapers,
                        )

                        scraper_results = search_magazine_scrapers(inbuilt)
                    else:
                        from simurg.metadata.enricher import rank_results, search_all_scrapers

                        scraper_results = search_all_scrapers(inbuilt)
                if (not url_used) and scraper_results:
                    click.secho(f"Found {len(scraper_results)} scraper result(s)", fg="green")
                    if len(scraper_results) == 1:
                        single = scraper_results[0]
                        scraper_data = rank_results(scraper_results) or {}
                        click.secho(
                            f"Single scraper result — using '{single.get('_scraper')}' automatically",
                            fg="green",
                        )
                    else:
                        choice = _prompt_scraper_selection(scraper_results, inbuilt)
                        if choice == "skip":
                            click.secho(
                                f"Skipping file {filepath.name} per user choice", fg="yellow"
                            )
                            skipped += 1
                            continue
                        if choice == "delete":
                            if _confirm_delete_file(filepath, dry_run=dry_run):
                                deleted += 1
                            else:
                                skipped += 1
                            continue
                        if choice is None:
                            click.secho(
                                "Using inbuilt metadata only (no scraper fill)", fg="yellow"
                            )
                        else:
                            scraper_data = choice
                            click.secho(
                                f"Using metadata from '{scraper_data.get('_scraper')}' scraper",
                                fg="green",
                            )
                elif (not url_used) and (not scraper_results):
                    if dry_run:
                        click.secho(
                            "No scraper results found (dry-run, skipping fallback prompt)",
                            fg="yellow",
                        )
                    else:
                        choice = _prompt_no_results_fallback(
                            inbuilt, is_mag=is_mag, dry_run=dry_run
                        )
                        if choice == "skip":
                            click.secho(
                                f"Skipping file {filepath.name} per user choice",
                                fg="yellow",
                            )
                            skipped += 1
                            continue
                        if choice == "delete":
                            if _confirm_delete_file(filepath, dry_run=dry_run):
                                deleted += 1
                            else:
                                skipped += 1
                            continue
                        if choice is not None:
                            scraper_data = choice
            except click.Abort:
                raise
            except Exception as e:
                click.secho(f"Enricher failed: {e}", fg="yellow")
        if scraper_data:
            click.secho(
                f"Enriched via scraper: {scraper_data.get('title')} | {scraper_data.get('publisher')} | ISBN {scraper_data.get('isbn')}",
                fg="green",
            )
            if scraper_data.get("source_urls"):
                click.echo(f" Source URLs: {fmt_urls(scraper_data['source_urls'])}")

        # [3b] Cross-source ISBN enrichment — re-query every scraper by the
        # book's ISBN and fill empty fields from those extra sources. Prompt
        # only (never silent): only fires when an ISBN is available AND the
        # extra sources would actually fill at least one gap. Primary match
        # stays authoritative for title/authors/year.
        if scraper_data:
            try:
                from simurg.metadata.enricher import merge_fill_gaps, search_all_by_isbn

                isbn = None
                for cand in (scraper_data.get("isbn"), inbuilt.get("isbn")):
                    if cand:
                        import re as _re

                        cleaned = _re.sub(r"[^0-9Xx]", "", str(cand))
                        if len(cleaned) in (10, 13):
                            isbn = cleaned
                            break
                if isbn:
                    with requests.Session() as _sess:
                        isbn_hits = search_all_by_isbn(isbn, _sess)
                    if isbn_hits:
                        merged = merge_fill_gaps(scraper_data, isbn_hits)
                        # Compute which fields would change/fill (exclude tags)
                        _diff = {}
                        for k in (
                            "publisher",
                            "year",
                            "page_count",
                            "description",
                            "cover_url",
                            "tags",
                        ):
                            mv = merged.get(k)
                            if mv not in (None, "", [], {}) and (
                                scraper_data.get(k) in (None, "", [], {})
                                or scraper_data.get(k) != mv
                            ):
                                _diff[k] = mv
                        if _diff:
                            _sources = ", ".join(
                                sorted({h.get("_scraper") for h in isbn_hits if h.get("_scraper")})
                            )
                            click.secho(
                                f"\nOther ISBN sources found ({_sources}). They could fill: "
                                + ", ".join(_diff.keys()),
                                fg="cyan",
                            )
                            _ans = (
                                click.prompt(
                                    "Cross-enrich: fill missing fields from these sources? [y/N]",
                                    type=str,
                                    default="n",
                                    show_default=False,
                                )
                                .strip()
                                .lower()
                            )
                            if _ans in ("y", "yes"):
                                scraper_data = merged
                                click.secho("Applied cross-source enrichment.", fg="green")
                            else:
                                click.secho("Skipped cross-source enrichment.", fg="yellow")
            except click.Abort:
                raise
            except Exception as e:
                click.secho(f"Cross-source enrichment failed: {e}", fg="yellow")

        # [4] Combine
        if is_mag:
            from simurg.metadata.magazine import build_magazine_metadata, validate_magazine_metadata

            metadata = build_magazine_metadata(inbuilt, scraper_data, fmt, str(filepath))
            # CLI overwrites (before review so user sees final values)
            if source_override is not None:
                metadata["source"] = source_override
            if fmt_override:
                metadata["format"] = fmt_override
            if lang_override:
                metadata["language"] = lang_override
            # [4a.0] .cache/magazine_issns.csv — exact-publisher reuse.
            # Publisher is the stable key (ISSN/country belong to publisher, not
            # issue title — e.g. "Penthouse" publisher reuses 1019-5009 across
            # variants like "Penthouse Letters"). Warm-save for next issue.
            try:
                from simurg.uploader.magazine_issn import save_magazine_issn_cache

                _pub_for_cache = (metadata.get("publisher") or "").strip()
                if _pub_for_cache and (
                    metadata.get("print_issn")
                    or metadata.get("electronic_issn")
                    or metadata.get("country")
                ):
                    save_magazine_issn_cache(
                        _pub_for_cache,
                        metadata.get("print_issn"),
                        metadata.get("electronic_issn"),
                        source="scraper",
                        country=metadata.get("country"),
                    )
            except Exception:
                pass
            # [4a] Forced OpenAlex ISSN gap-fill — magazines only, post-scrape.
            # Always try to resolve print/electronic ISSN via OpenAlex /sources after the
            # normal magazine scrapers have run. Fills missing ISSNs only (never
            # overwrites existing ones) so provenance stays intact. Shows the search
            # params and, when no match, offers to paste an OpenAlex source URL.
            if not metadata.get("print_issn") or not metadata.get("electronic_issn"):
                try:
                    from simurg.metadata.scrapers.openalex import OpenAlexScraper

                    # [4a.0a] .cache/magazine_issns.csv — exact-publisher reuse.
                    # Reuse ISSNs/country for same publisher across issues/variants.
                    try:
                        from simurg.uploader.magazine_issn import lookup_magazine_issn_cache

                        _pub_cache = (metadata.get("publisher") or "").strip()
                        if _pub_cache:
                            _cached = lookup_magazine_issn_cache(_pub_cache)
                            if _cached:
                                _filled_cache = []
                                for _k in ("print_issn", "electronic_issn", "country"):
                                    if not metadata.get(_k) and _cached.get(_k):
                                        metadata[_k] = _cached[_k]
                                        _filled_cache.append(f"{_k}={_cached[_k]}")
                                if _filled_cache:
                                    click.secho(
                                        f"Using cached ISSN/country from .cache/magazine_issns.csv for publisher '{_pub_cache}': {', '.join(_filled_cache)}",
                                        fg="green",
                                    )
                    except Exception:
                        pass
                    canonical = (
                        metadata.get("canonical_title") or metadata.get("title") or ""
                    ).strip()
                    if canonical and (
                        not metadata.get("print_issn") or not metadata.get("electronic_issn")
                    ):
                        with requests.Session() as _oa_sess:
                            _oa_scraper = OpenAlexScraper(_oa_sess)
                            _api_key = _oa_scraper._api_key()
                            _masked = (
                                "***" if _api_key else "(missing — set [metadata] openalex_api_key)"
                            )
                            # Show the exact query so the user can see why a title may miss (e.g. "Penthouse (USA)")
                            click.secho(
                                f"OpenAlex search: GET https://api.openalex.org/sources"
                                f"  search={canonical!r}  per_page=10  api_key={_masked}",
                                fg="cyan",
                            )
                            patch = _oa_scraper.search_magazine(canonical, None)
                        if patch:
                            filled = []
                            for k in ("print_issn", "electronic_issn"):
                                if not metadata.get(k) and patch.get(k):
                                    metadata[k] = patch[k]
                                    filled.append(f"{k}={patch[k]}")
                            # Also fill publisher/country if still empty and OpenAlex has them
                            for k in ("publisher", "country"):
                                if not metadata.get(k) and patch.get(k):
                                    metadata[k] = patch[k]
                            if filled:
                                click.secho(f"OpenAlex ISSN fill: {', '.join(filled)}", fg="green")
                                # Persist to .cache/magazine_issns.csv for exact-publisher reuse.
                                try:
                                    from simurg.uploader.magazine_issn import (
                                        save_magazine_issn_cache,
                                    )

                                    _pub_save = (metadata.get("publisher") or "").strip()
                                    if _pub_save:
                                        save_magazine_issn_cache(
                                            _pub_save,
                                            metadata.get("print_issn"),
                                            metadata.get("electronic_issn"),
                                            source="openalex",
                                            country=metadata.get("country"),
                                        )
                                except Exception:
                                    pass
                            else:
                                # Scraper already had ISSNs or OpenAlex had no new ones
                                pass
                            if patch.get("source_urls"):
                                click.echo(f" OpenAlex source: {patch['source_urls'][0]}")
                        else:
                            click.secho(
                                "OpenAlex: no ISSN match (consumer magazines may be hit-or-miss)",
                                fg="yellow",
                            )
                            if dry_run:
                                click.secho(
                                    "Dry-run: skipping manual OpenAlex URL prompt", fg="yellow"
                                )
                            else:
                                click.echo(
                                    "  [u] Paste an OpenAlex URL (e.g. https://openalex.org/S137355760"
                                    " or https://api.openalex.org/sources/S137355760) to fetch ISSN manually"
                                )
                                click.echo(
                                    "  [Enter] Keep without ISSN  |  [a] Abort (skip file)  |  [d] Delete file"
                                )
                                try:
                                    choice = click.prompt(
                                        "OpenAlex URL", type=str, default="", show_default=False
                                    ).strip()
                                except click.Abort:
                                    raise
                                if choice.lower() in ("a", "abort"):
                                    click.secho(
                                        f"Skipping file {filepath.name} per user choice",
                                        fg="yellow",
                                    )
                                    skipped += 1
                                    continue
                                if choice.lower() in ("d", "delete"):
                                    if _confirm_delete_file(filepath, dry_run=dry_run):
                                        deleted += 1
                                    else:
                                        skipped += 1
                                    continue
                                pasted_url = ""
                                if choice.lower() in ("u", "url"):
                                    try:
                                        pasted_url = click.prompt(
                                            "Paste OpenAlex source URL", type=str
                                        ).strip()
                                    except click.Abort:
                                        raise
                                elif choice.startswith("http") or choice.startswith("S"):
                                    pasted_url = choice
                                elif choice:
                                    # treat any non-empty non-http as possible ID/URL fragment
                                    pasted_url = choice
                                if pasted_url:
                                    # Normalize bare S ID to full URL
                                    if pasted_url.startswith("S") and not pasted_url.startswith(
                                        "http"
                                    ):
                                        pasted_url = f"https://openalex.org/{pasted_url}"
                                    try:
                                        with requests.Session() as _oa_sess2:
                                            _oa_scraper2 = OpenAlexScraper(_oa_sess2)
                                            url_patch = _oa_scraper2.search_url(pasted_url)
                                        if url_patch:
                                            filled2 = []
                                            for k in ("print_issn", "electronic_issn"):
                                                if not metadata.get(k) and url_patch.get(k):
                                                    metadata[k] = url_patch[k]
                                                    filled2.append(f"{k}={url_patch[k]}")
                                            for k in ("publisher", "country"):
                                                if not metadata.get(k) and url_patch.get(k):
                                                    metadata[k] = url_patch[k]
                                            if filled2:
                                                click.secho(
                                                    f"OpenAlex manual URL fill: {', '.join(filled2)}",
                                                    fg="green",
                                                )
                                                # Persist manual OpenAlex URL result too.
                                                try:
                                                    from simurg.uploader.magazine_issn import (
                                                        save_magazine_issn_cache,
                                                    )

                                                    _pub_save2 = (
                                                        metadata.get("publisher") or ""
                                                    ).strip()
                                                    if _pub_save2:
                                                        save_magazine_issn_cache(
                                                            _pub_save2,
                                                            metadata.get("print_issn"),
                                                            metadata.get("electronic_issn"),
                                                            source="openalex_url",
                                                            country=metadata.get("country"),
                                                        )
                                                except Exception:
                                                    pass
                                            else:
                                                click.secho(
                                                    "OpenAlex URL resolved but had no new ISSNs to fill",
                                                    fg="yellow",
                                                )
                                            if url_patch.get("source_urls"):
                                                click.echo(
                                                    f" OpenAlex source: {url_patch['source_urls'][0]}"
                                                )
                                        else:
                                            click.secho(
                                                f"Could not resolve OpenAlex URL to ISSN: {pasted_url}",
                                                fg="yellow",
                                            )
                                    except click.Abort:
                                        raise
                                    except Exception as e:
                                        click.secho(f"OpenAlex URL lookup failed: {e}", fg="yellow")
                except click.Abort:
                    raise
                except Exception as e:
                    click.secho(f"OpenAlex ISSN lookup failed: {e}", fg="yellow")
                # [4a.1] Simurg fallback when OpenAlex yields nothing — search Simurg
                # for an existing magazine (Playboy/Penthouse etc.) and offer its
                # ISSNs (+ publisher/country) for direct reuse (option A). Only when
                # still missing ISSN or publisher/country after the forced OpenAlex pass.
                if (
                    not (metadata.get("print_issn") or "").strip()
                    or not (metadata.get("electronic_issn") or "").strip()
                    or not (metadata.get("publisher") or "").strip()
                    or not (metadata.get("country") or "").strip()
                ):
                    try:
                        from simurg.uploader.magazine_issn import (
                            prompt_simurg_issn_reuse,
                            search_simurg_magazine_issns,
                        )

                        # Need an authenticated tracker session
                        _can_search = bool(
                            gazelle_site and getattr(gazelle_site, "authkey", "dummy") != "dummy"
                        )
                        if not _can_search:
                            if dry_run:
                                click.secho(
                                    "Dry-run: skipping Simurg ISSN search (no authenticated session)",
                                    fg="yellow",
                                )
                            else:
                                click.secho(
                                    "Skipping Simurg ISSN search (no authenticated session)",
                                    fg="yellow",
                                )
                        else:
                            canonical = (
                                metadata.get("canonical_title") or metadata.get("title") or ""
                            ).strip()
                            if canonical:
                                click.secho(
                                    f"OpenAlex returned nothing — searching Simurg for existing '{canonical}' magazines…",
                                    fg="cyan",
                                )
                                candidates = search_simurg_magazine_issns(
                                    gazelle_site, canonical, limit=10
                                )
                                if not candidates:
                                    click.secho(
                                        f"No existing Simurg magazine found matching '{canonical}' (no ISSN to reuse).",
                                        fg="yellow",
                                    )
                                else:
                                    click.secho(
                                        f"Found {len(candidates)} existing magazine(s) on Simurg matching '{canonical}':",
                                        fg="cyan",
                                        bold=True,
                                    )
                                    for idx, cand in enumerate(candidates, 1):
                                        issn_parts = []
                                        if cand.get("print_issn"):
                                            issn_parts.append(f"print {cand['print_issn']}")
                                        if cand.get("electronic_issn"):
                                            issn_parts.append(
                                                f"electronic {cand['electronic_issn']}"
                                            )
                                        issn_str = (
                                            " / ".join(issn_parts) if issn_parts else "no ISSN"
                                        )
                                        title = cand.get("title") or "?"
                                        year = cand.get("year") or "?"
                                        pub = cand.get("publisher") or ""
                                        country = cand.get("country") or ""
                                        line = f"  [{idx}] {title} ({year}) — {issn_str}"
                                        if pub:
                                            line += f" · {pub}"
                                        if country:
                                            line += f" · {country}"
                                        line += f"  {fmt_url(cand.get('url') or '')}"
                                        click.echo(line)
                                    if dry_run:
                                        # Dry-run: auto-apply first candidate's ISSNs without extra prompts inside helper
                                        click.secho(
                                            "Dry-run: auto-using first Simurg candidate's ISSNs (no prompts)",
                                            fg="yellow",
                                        )
                                        chosen = candidates[0]
                                        overrides = prompt_simurg_issn_reuse(
                                            metadata, chosen, dry_run=True
                                        )
                                        for k, v in overrides.items():
                                            metadata[k] = v
                                        if overrides:
                                            click.secho(
                                                f"Applied Simurg ISSN overrides (dry-run): {overrides}",
                                                fg="green",
                                            )
                                            try:
                                                from simurg.uploader.magazine_issn import (
                                                    save_magazine_issn_cache,
                                                )

                                                _pub_s = (metadata.get("publisher") or "").strip()
                                                if _pub_s:
                                                    save_magazine_issn_cache(
                                                        _pub_s,
                                                        metadata.get("print_issn"),
                                                        metadata.get("electronic_issn"),
                                                        source="simurg",
                                                        country=metadata.get("country"),
                                                    )
                                            except Exception:
                                                pass
                                    else:
                                        # Interactive pick
                                        click.echo(
                                            "  [i] Keep without Simurg ISSN  |  [s] Skip this file  |  [a] Abort (skip file)  |  [d] Delete file"
                                        )
                                        while True:
                                            ans = (
                                                click.prompt(
                                                    "Choose Simurg entry to reuse ISSN from",
                                                    type=str,
                                                    default="",
                                                    show_default=False,
                                                )
                                                .strip()
                                                .lower()
                                            )
                                            if ans in ("i", "inbuilt", "n", ""):
                                                click.secho(
                                                    "Keeping without Simurg ISSN.", fg="yellow"
                                                )
                                                break
                                            if ans in ("s", "skip", "a", "abort"):
                                                click.secho(
                                                    f"Skipping file {filepath.name} per user choice",
                                                    fg="yellow",
                                                )
                                                skipped += 1
                                                # Need to signal skip of this file to outer loop
                                                # Use a sentinel via metadata flag; handle after this block
                                                metadata["_simurg_skip_file"] = True  # type: ignore
                                                break
                                            if ans in ("d", "delete"):
                                                if _confirm_delete_file(filepath, dry_run=dry_run):
                                                    deleted += 1
                                                else:
                                                    skipped += 1
                                                metadata["_simurg_skip_file"] = True  # type: ignore
                                                break
                                            if ans.isdigit():
                                                n = int(ans)
                                                if 1 <= n <= len(candidates):
                                                    chosen = candidates[n - 1]
                                                    overrides = prompt_simurg_issn_reuse(
                                                        metadata, chosen, dry_run=False
                                                    )
                                                    for k, v in overrides.items():
                                                        metadata[k] = v
                                                    if overrides:
                                                        click.secho(
                                                            f"Applied Simurg ISSN overrides: {overrides}",
                                                            fg="green",
                                                        )
                                                        try:
                                                            from simurg.uploader.magazine_issn import (
                                                                save_magazine_issn_cache,
                                                            )

                                                            _pub_s2 = (
                                                                metadata.get("publisher") or ""
                                                            ).strip()
                                                            if _pub_s2:
                                                                save_magazine_issn_cache(
                                                                    _pub_s2,
                                                                    metadata.get("print_issn"),
                                                                    metadata.get("electronic_issn"),
                                                                    source="simurg",
                                                                    country=metadata.get("country"),
                                                                )
                                                        except Exception:
                                                            pass
                                                    else:
                                                        click.secho(
                                                            "No ISSN overrides applied.",
                                                            fg="yellow",
                                                        )
                                                    break
                                            click.secho(
                                                f"Invalid choice — pick 1-{len(candidates)}, i, s, a or d.",
                                                fg="yellow",
                                            )
                                        if metadata.get("_simurg_skip_file"):
                                            # inner loop already broke; keep flag for outer continue
                                            pass
                    except click.Abort:
                        raise
                    except Exception as e:
                        click.secho(f"Simurg ISSN fallback failed: {e}", fg="yellow")
                # If Simurg picker requested file skip, jump to next file.
                if metadata.get("_simurg_skip_file"):
                    metadata.pop("_simurg_skip_file", None)
                    continue
        else:
            from simurg.metadata.combine import build_metadata, validate_metadata

            metadata = build_metadata(inbuilt, scraper_data, fmt, str(filepath))
        # CLI overwrites (before review so user sees final values)
        if source_override is not None:
            metadata["source"] = source_override
        elif not metadata.get("source"):
            metadata["source"] = "Other"
        if fmt_override:
            metadata["format"] = fmt_override
        if lang_override:
            metadata["language"] = lang_override

        # [4b] Field review: let the user keep/override/append file metadata vs the
        # chosen scraper result (description, edition, illustrators, title, authors, ...).
        # Ebooks only — magazines have no edition/illustrator roles to review.
        #
        # We never force this choice. We only offer it when a scraper was used AND
        # the combined metadata is still missing required fields, and we ask the
        # user first whether they want to review. If nothing is missing, the merge
        # is skipped entirely.
        if (not is_mag) and scraper_data:
            missing = validate_metadata(metadata)
            if missing:
                if dry_run:
                    # Non-interactive: run the merge (it won't prompt, keeps defaults).
                    click.secho(
                        f"Dry-run: missing required field(s) {missing}; running field review (no prompts)",
                        fg="yellow",
                    )
                    try:
                        overrides = _prompt_field_merge(
                            inbuilt, scraper_data, metadata, dry_run=dry_run
                        )
                        _apply_field_overrides(metadata, overrides)
                        if overrides:
                            click.secho(
                                f"Applied {len(overrides)} manual metadata override(s)", fg="green"
                            )
                    except click.Abort:
                        raise
                    except Exception as e:
                        click.secho(f"Field merge review failed: {e}", fg="yellow")
                else:
                    # Interactive: only offer the review when something is actually
                    # missing, and ask first — never force the file-vs-scraper choice.
                    click.secho(f"Missing required field(s): {', '.join(missing)}", fg="yellow")
                    ans = (
                        click.prompt(
                            "Review file metadata vs chosen scraper to fill the gaps? [Y/n]",
                            type=str,
                            default="y",
                            show_default=False,
                        )
                        .strip()
                        .lower()
                    )
                    if ans in ("y", "yes"):
                        try:
                            overrides = _prompt_field_merge(
                                inbuilt, scraper_data, metadata, dry_run=dry_run
                            )
                            _apply_field_overrides(metadata, overrides)
                            if overrides:
                                click.secho(
                                    f"Applied {len(overrides)} manual metadata override(s)",
                                    fg="green",
                                )
                        except click.Abort:
                            raise
                        except Exception as e:
                            click.secho(f"Field merge review failed: {e}", fg="yellow")

        # [4c] Interactive editor review — open nano (or $EDITOR) so the user can
        # freely revise any field after scraping. Skippable per file; suppressed by
        # --no-review for automated batch runs. Mirrors smoked-salmon-mini's
        # review_metadata / click.edit flow.
        if not no_review:
            try:
                from simurg.metadata.review import review_metadata

                reviewed = review_metadata(metadata, is_mag=is_mag, dry_run=dry_run)
                if isinstance(reviewed, str):
                    if reviewed == "delete":
                        if _confirm_delete_file(filepath, dry_run=dry_run):
                            deleted += 1
                        else:
                            skipped += 1
                    else:
                        click.secho(f"Skipping file {filepath.name} per user choice", fg="yellow")
                        skipped += 1
                    continue
                metadata = reviewed
            except click.Abort:
                raise
            except Exception as e:
                click.secho(f"Editor review failed: {e}", fg="yellow")

        # [4c.1] Persist ISSN/country after manual editor edits — publisher is
        # the stable key (e.g. Penthouse). Save after review so hand-typed ISSN/
        # country is reused for next issue with same publisher.
        if is_mag:
            try:
                from simurg.uploader.magazine_issn import save_magazine_issn_cache

                _pub_after_review = (metadata.get("publisher") or "").strip()
                if _pub_after_review and (
                    metadata.get("print_issn")
                    or metadata.get("electronic_issn")
                    or metadata.get("country")
                ):
                    saved = save_magazine_issn_cache(
                        _pub_after_review,
                        metadata.get("print_issn"),
                        metadata.get("electronic_issn"),
                        source="manual",
                        country=metadata.get("country"),
                    )
                    if saved:
                        click.secho(
                            f"Saved ISSN/country to .cache/magazine_issns.csv for publisher '{_pub_after_review}'",
                            fg="green",
                        )
            except Exception:
                pass

        # Build tags: clean
        # Ensure tags not forbidden praise etc already handled

        # [5] Dupe search (interactive in dry-run too — only the upload is skipped)
        search_gid = group_id  # forced override
        request_id = None
        if gazelle_site:
            from simurg.uploader.dupe import check_existing_group, generate_dupe_search_strs

            searchstrs = generate_dupe_search_strs(
                metadata["title"], metadata.get("authors") or [], metadata.get("isbn")
            )
            click.secho(f"Searching Simurg for dupes: {searchstrs}", fg="yellow")
            try:
                result_gid = check_existing_group(
                    gazelle_site, searchstrs, group_id_override=group_id
                )
                if result_gid == "skip":
                    click.secho(f"Skipping file {filepath.name} per user choice", fg="yellow")
                    skipped += 1
                    continue
                if result_gid == "delete":
                    if _confirm_delete_file(filepath, dry_run=dry_run):
                        deleted += 1
                    else:
                        skipped += 1
                    continue
                search_gid = result_gid
            except click.Abort:
                click.secho("Aborted by user", fg="red")
                raise
            except Exception as e:
                click.secho(f"Dupe check failed: {e}", fg="yellow")

            # Check requests
            try:
                from simurg.uploader.requests import check_requests

                request_id = check_requests(gazelle_site, searchstrs, dry_run=dry_run)
            except Exception as e:
                click.secho(f"Request check failed: {e}", fg="yellow")
        elif not dry_run:
            click.secho("Skipping Simurg dupe search (no gazelle_site)", fg="yellow")

        # [7] Validate metadata
        if is_mag:
            from simurg.metadata.magazine import validate_magazine_metadata

            missing = validate_magazine_metadata(metadata)
            # Abort on language violation per docs/magazine.txt §10: form only
            # has English/Turkish/Japanese — Russian etc. must never silently
            # become English. Also abort if release_title is empty.
            if "language" in missing:
                found = (metadata.get("language") or "").strip() or "(empty)"
                click.secho(
                    f"{FAIL_SYMBOL} Skipping {filepath.name}: language '{found}' "
                    f"not in allowed {{English, Turkish, Japanese}} per docs/magazine.txt §10 — aborting upload",
                    fg="red",
                    bold=True,
                )
                skipped += 1
                continue
            if "release_title" in missing:
                click.secho(
                    f"{FAIL_SYMBOL} Skipping {filepath.name}: missing release_title/issue identity — cannot derive issue label",
                    fg="red",
                    bold=True,
                )
                skipped += 1
                continue
        else:
            from simurg.metadata.combine import validate_metadata

            missing = validate_metadata(metadata)
        if missing:
            click.secho(
                f"Warning: missing required fields {missing} (continuing anyway)", fg="yellow"
            )

        # Show metadata table minimal
        click.secho("\nMetadata:", fg="cyan")
        for k in [
            "title",
            "remaster_title",
            "authors",
            "year",
            "remaster_year",
            "publisher",
            "isbn",
            "page_count",
            "format",
            "source",
            "tags",
            "language",
        ]:
            v = metadata.get(k)
            click.echo(f" {k:15}: {v}")

        # [8] Cover handling — always download the cover and REHOST it via
        # ptscreens/imgbb/catbox (never hotlink the source URL).
        # Priority: --cover override > edited image [img] > scraper cover > DuckDuckGo fallback > embedded file cover
        cover_url = None
        cover_path = metadata.get("cover_path")  # embedded file cover (last resort)
        cover_scraper = metadata.get("cover_url_scraper")
        edited_image = (metadata.get("image") or "").strip()
        temp_cover = None
        # 1) --cover override (rehost it too) — highest priority
        if cover and not temp_cover:
            tmp = _download_url_to_temp(cover)
            if tmp:
                temp_cover = tmp
                click.secho(f"Downloaded cover (override): {cover}", fg="green")
            else:
                click.secho(
                    f"Failed to download override cover (missing/invalid): {cover}",
                    fg="yellow",
                )
        # 2) Edited image via review menu [img] — manual URL override (rehost it)
        if edited_image and not temp_cover and edited_image.lower().startswith("http"):
            tmp = _download_url_to_temp(edited_image)
            if tmp:
                temp_cover = tmp
                click.secho(f"Downloaded cover from edited image: {edited_image}", fg="green")
            else:
                click.secho(
                    f"Failed to download edited image URL (missing/invalid): {edited_image}",
                    fg="yellow",
                )
        # 3) Scraper cover (best quality — always scrape per user decision)
        if cover_scraper and not temp_cover:
            tmp = _download_url_to_temp(cover_scraper)
            if tmp:
                temp_cover = tmp
                click.secho(f"Downloaded cover from scraper: {cover_scraper}", fg="green")
            else:
                click.secho(
                    f"Failed to download scraper cover (missing/invalid): {cover_scraper}",
                    fg="yellow",
                )
        # 4) DuckDuckGo fallback if no scraper cover
        if not temp_cover:
            try:
                from simurg.config import get_config

                cfg_dd = get_config()
                if cfg_dd.metadata.get("cover_fallback_duckduckgo", True):
                    from simurg.metadata.scrapers.duckduckgo import fetch_duckduckgo_cover

                    dd_path = fetch_duckduckgo_cover(
                        metadata.get("title") or "", metadata.get("authors") or []
                    )
                    if dd_path:
                        temp_cover = dd_path
                        click.secho(f"Fallback DuckDuckGo cover: {dd_path}", fg="green")
            except Exception as e:
                click.secho(f"DuckDuckGo fallback failed: {e}", fg="yellow")
        # 5) Embedded file cover as last resort
        if not temp_cover and cover_path and Path(cover_path).exists():
            from simurg.images.validate import is_valid_cover

            valid, reason = is_valid_cover(str(cover_path))
            if valid:
                temp_cover = cover_path
                click.secho(f"Using cover from file: {cover_path}", fg="green")
            else:
                click.secho(f"Embedded cover invalid ({reason}), ignoring", fg="yellow")
        if not temp_cover and not dry_run:
            try:
                manual = _prompt_manual_cover(
                    metadata.get("title") or "",
                    metadata.get("authors") or [],
                    dry_run=dry_run,
                )
                if manual == "skip":
                    click.secho(f"Skipping file {filepath.name} per user choice", fg="yellow")
                    skipped += 1
                    continue
                if manual == "delete":
                    if _confirm_delete_file(filepath, dry_run=dry_run):
                        deleted += 1
                    else:
                        skipped += 1
                    continue
                if manual:
                    temp_cover = manual
            except click.Abort:
                raise

        if temp_cover:
            # Always rehost via image host (download -> ptscreens/imgbb/catbox)
            try:
                from simurg.config import get_config
                from simurg.images import get_uploader

                cfg_img = get_config()
                uploader_name = str(cfg_img.image.get("cover_uploader", "ptscreens") or "ptscreens")
                Uploader = get_uploader(uploader_name)
                # keys already handled inside uploader via config
                rehost_url, _ = Uploader().upload_file(temp_cover)
                cover_url = rehost_url
                click.secho("Cover rehosted: ", fg="green", bold=True, nl=False)
                click.echo(fmt_url(cover_url))
            except Exception as e:
                click.secho(f"{FAIL_SYMBOL} Cover rehost failed: {e}", fg="red")
                click.secho(
                    "Hint: try another image host — set [image] cover_uploader = "
                    "'imgbb' or 'catbox' in config.toml",
                    fg="yellow",
                )
                cover_url = None
        else:
            click.secho("No cover found. Continuing without image.", fg="yellow")
            cover_url = None
        if cover_url and not dry_run:
            try:
                confirmed = _confirm_rehosted_cover(
                    cover_url, temp_cover, cover_path=cover_path, dry_run=dry_run
                )
                if confirmed == "skip":
                    if temp_cover and temp_cover != cover_path:
                        try:
                            Path(temp_cover).unlink(missing_ok=True)
                        except Exception:
                            pass
                    click.secho(f"Skipping file {filepath.name} per user choice", fg="yellow")
                    skipped += 1
                    continue
                if confirmed == "delete":
                    if temp_cover and temp_cover != cover_path:
                        try:
                            Path(temp_cover).unlink(missing_ok=True)
                        except Exception:
                            pass
                    if _confirm_delete_file(filepath, dry_run=dry_run):
                        deleted += 1
                    else:
                        skipped += 1
                    continue
                assert isinstance(confirmed, tuple)
                cover_url, temp_cover = confirmed
            except click.Abort:
                raise

        # Clean up downloaded temp cover (no longer needed after rehost)
        if temp_cover and temp_cover != cover_path:
            try:
                Path(temp_cover).unlink(missing_ok=True)
            except Exception:
                pass

        metadata["image"] = cover_url or ""

        # [9] Filename sanitize & staging move (before torrent creation)
        # New format: {Title} - {Author} (year) [ISBN].ext  -> staged before torrent
        if not no_rename:
            # Determine staging directory: staging_dir > upload_directory > .staging
            staging_dir = None
            try:
                from simurg.config import get_config

                cfg_tmp = get_config()
                staging_dir = str(cfg_tmp.directory.get("staging_dir", "") or "").strip()
                if not staging_dir:
                    staging_dir = str(
                        cfg_tmp.directory.get("upload_directory", "")
                        or cfg_tmp.directory.get("download_directory", "")
                        or ""
                    ).strip()
                if not staging_dir:
                    staging_dir = ".staging"
            except Exception:
                staging_dir = ".staging"
            # Resolve staging_dir relative to cwd if not absolute
            staging_path = Path(staging_dir)
            if not staging_path.is_absolute():
                staging_path = Path.cwd() / staging_path
            # Build new filename
            title_part = _sanitize_filename(str(metadata.get("title") or filepath.stem).strip())
            if is_mag:
                # Magazines: {Title} - {Issue label} (year).ext
                from simurg.metadata.magazine import magazine_issue_label

                issue_label = magazine_issue_label(metadata)
                year_part = str(metadata.get("year") or "").strip()
                base_name = f"{title_part} - {issue_label}" if issue_label else title_part
                if year_part:
                    base_name += f" ({year_part})"
            else:
                # Ebooks: {Title} - {Author} (year) [ISBN].ext
                author_part = _sanitize_filename(
                    str((metadata.get("authors") or ["Unknown"])[0]).strip()
                )
                year_part = str(metadata.get("year") or metadata.get("remaster_year") or "").strip()
                isbn_part = str(metadata.get("isbn") or "").strip()
                base_name = f"{title_part} - {author_part}"
                if year_part:
                    base_name += f" ({year_part})"
                if isbn_part:
                    isbn_clean = re.sub(r"[^0-9Xx]", "", isbn_part)
                    if isbn_clean:
                        base_name += f" [{isbn_clean}]"
            expected = f"{base_name}{filepath.suffix}"
            # Ensure staging dir exists (even for dry-run, show intent)
            try:
                staging_path.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            # Destination is staging_path / expected (if staging != source, else in-place)
            # If staging_path resolves to same as source parent, keep in place
            try:
                source_parent = filepath.parent.resolve()
                staging_resolved = staging_path.resolve()
                is_same_dir = source_parent == staging_resolved
            except Exception:
                is_same_dir = False
            dest_path = (filepath.parent / expected) if is_same_dir else (staging_path / expected)

            if filepath.resolve() == dest_path.resolve():
                click.secho(f"Filename already staged correctly: {dest_path}", fg="green")
                filepath = dest_path
            elif filepath.name != expected or not is_same_dir:
                click.secho(f"Rename & stage: {filepath} -> {dest_path}", fg="yellow")
                try:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(filepath), str(dest_path))
                    click.secho(f"Auto-staged to {dest_path}", fg="green")
                    filepath = dest_path
                except Exception as e:
                    click.secho(f"Rename/move failed: {e}", fg="red")
            else:
                click.secho(f"Filename already clean: {filepath.name}", fg="green")
        else:
            click.secho("Skipping rename/stage (--no-rename)", fg="yellow")

        # Build release_desc
        from simurg.uploader.payload import build_release_desc

        metadata["release_desc"] = build_release_desc(metadata, metadata.get("source_urls"))
        # When source is "Other" (unverified), disclose it in the description (rules.txt:65).
        if metadata.get("source") == "Other":
            metadata["release_desc"] = (
                metadata["release_desc"]
                + "\n[b]Source note:[/b] marked Other (provenance unverified; please confirm)."
            ).strip()
        # Use per-file remaster etc already in metadata

        # [10-12] Generate torrent + upload (dry-run generates the real torrent, skips the POST)
        try:
            from simurg.uploader.upload import prepare_and_upload

            if gazelle_site is None:
                # Dry-run without a session: still build a real .torrent.
                announce = ""
                try:
                    from simurg.config import get_config

                    cfg_t = get_config()
                    tcfg = cfg_t.get_tracker_cfg("simurg")
                    announce = str(tcfg.get("announce_url") or "")
                except Exception:
                    announce = ""
                if not announce:
                    announce = "https://tracker.simurg.world/announce"

                class TorrentCtx:
                    announce = ""
                    dot_torrents_dir = dottorrents_dir
                    site_string = "SIM"

                torrent_site = TorrentCtx()
                torrent_site.announce = announce
            else:
                torrent_site = gazelle_site

            prepare_and_upload(
                torrent_site,
                filepath,
                search_gid,
                metadata,
                cover_url,
                request_id,
                dry_run=dry_run,
                category=category,
            )
            uploaded += 1
            # Rate-limit only after an actual upload (never on skip/delete/fail),
            # and never after the last file.
            if not dry_run and idx < len(ebook_files) - 1:
                _rate_limit_wait(7, label=f"(file {idx + 1}/{len(ebook_files)})")
        except click.Abort:
            raise
        except Exception as e:
            click.secho(f"Failed to upload {filepath.name}: {e}", fg="red")
            failed += 1
            continue

    click.secho("\n" + "=" * 60, fg="cyan")
    if total_files is None:
        # Bounded scan: total unknown, only whether the walk hit the cap.
        remaining_note = " More files may remain — rerun the same command." if hit_limit else ""
    else:
        remaining = total_files - len(ebook_files)
        remaining_note = (
            f" {remaining} file(s) remaining — rerun the same command." if remaining > 0 else ""
        )
    if dry_run:
        color = "green" if failed == 0 else "yellow"
        click.secho(
            f"{OK_SYMBOL if failed == 0 else WARN_SYMBOL} Summary: Dry-run — prepared "
            f"{uploaded}/{len(ebook_files)} (torrents in {dottorrents_dir}/, no uploads sent), "
            f"skipped {skipped}, deleted {deleted}, failed {failed}.{remaining_note}",
            fg=color,
            bold=True,
        )
    else:
        color = "green" if failed == 0 else "yellow"
        sym = OK_SYMBOL if failed == 0 else WARN_SYMBOL
        click.secho(
            f"{sym} Summary: Uploaded {uploaded}/{len(ebook_files)}, skipped {skipped}, "
            f"deleted {deleted}, failed {failed} — check {dottorrents_dir}/.{remaining_note}",
            fg=color,
            bold=True,
        )


@cli.command("checkconf")
def checkconf():
    """Check tracker/auth + image hosts + scrapers."""
    click.secho("=== checkconf ===", fg="cyan", bold=True)
    # Config
    try:
        from simurg.config import get_config, load_config

        cfg = load_config()
        click.secho("Config: OK", fg="green")
        # show non-sensitive
        try:
            dott = str(cfg.directory.get("dottorrents_dir", ".torrents"))
            click.echo(f" dottorrents_dir: {dott}")
            uploader = str(cfg.image.get("cover_uploader", "ptscreens"))
            click.echo(f" cover_uploader: {uploader}")
        except Exception:
            pass
    except ConfigError as e:
        click.secho(f"Config: FAILED - {e}", fg="red")
        return
    except Exception as e:
        click.secho(f"Config: FAILED - {e}", fg="red")
        return

    # Tracker
    try:
        from simurg.trackers.simurg import SimurgApi

        site = SimurgApi()
        click.secho(
            f"Tracker simurg: OK (authkey {site.authkey[:6]}... passkey {site.passkey[:6]}...)",
            fg="green",
        )
        click.echo(f" announce: {site.announce[:60]}...")
        # test browse
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            resp = loop.run_until_complete(site.request("browse", searchstr="test"))
            click.secho(f" Browse test: OK ({len(resp.get('results', []))} results)", fg="green")
        except Exception as e:
            click.secho(f" Browse test: FAILED - {e}", fg="yellow")
    except Exception as e:
        click.secho(f"Tracker simurg: FAILED - {e}", fg="red")

    # Image hosts
    try:
        from simurg.config import get_config

        cfg = get_config()
        uploader_name = str(cfg.image.get("cover_uploader", "ptscreens"))
        from simurg.images import get_uploader

        try:
            get_uploader(uploader_name)
            click.secho(f"Image host {uploader_name}: OK (class loaded)", fg="green")
            # Check keys
            key = str(
                cfg.image.get(f"{uploader_name}_key", "")
                or cfg.image.get(f"{uploader_name}_api_key", "")
                or ""
            )
            if key:
                click.secho(f"  key present ({len(key)} chars)", fg="green")
            else:
                click.secho("  no key set (may fail on upload)", fg="yellow")
        except Exception as e:
            click.secho(f"Image host {uploader_name}: FAILED - {e}", fg="red")
    except Exception as e:
        click.secho(f"Image host check: FAILED - {e}", fg="yellow")

    # Torrent client (qBittorrent push after upload)
    try:
        from simurg.config import get_config as _cfg3

        _cc = _cfg3().client
        _enabled = bool(_cc.get("enabled", False))
        _url = str(_cc.get("torrent_client", "") or "").strip()
        if not _enabled or not _url:
            click.secho("Torrent client: SKIPPED (disabled, see [client] in config)", fg="yellow")
        else:
            from simurg.uploader.client import QBittorrentClient, masked_url

            click.echo(f" url: {masked_url(_url)}")
            _sp = str(_cc.get("save_path", "") or "")
            _lp = str(_cc.get("local_path", "") or "")
            click.echo(f" save_path (qbit view): {_sp or '(missing)'}")
            click.echo(f" local_path (WSL mount): {_lp or '(missing)'}")
            if not _sp or not _lp:
                click.secho("Torrent client: FAILED - set save_path + local_path", fg="red")
            else:
                _client = QBittorrentClient(_url)
                if _client.client:
                    click.secho("Torrent client: OK (qBittorrent login)", fg="green")
                else:
                    click.secho("Torrent client: FAILED - login failed", fg="red")
    except Exception as e:
        click.secho(f"Torrent client: FAILED - {e}", fg="yellow")

    # Scrapers actually exercised by checkconf (docs/ux-improvements.md §3.1).
    # Anything not probed below is reported as SKIPPED so coverage is honest.
    scrapers = [
        "openlibrary",
        "googlebooks",
        "bookbrainz",
        "abebooks",
        "goodreads",
    ]
    for name in scrapers:
        try:
            if name == "openlibrary":
                from simurg.metadata.scrapers.openlibrary import OpenLibraryScraper

                sc = OpenLibraryScraper()
                r = sc.search_isbn("9780140328721")  # Matilda ISBN test
                if r:
                    click.secho(f"Scraper openlibrary: OK (found {r.get('title')})", fg="green")
                else:
                    click.secho("Scraper openlibrary: OK (no result but reachable)", fg="green")
            elif name == "googlebooks":
                from simurg.metadata.scrapers.googlebooks import GoogleBooksScraper

                sc = GoogleBooksScraper()
                r = sc.search_isbn("9780140328721")
                if r:
                    click.secho(f"Scraper googlebooks: OK (found {r.get('title')})", fg="green")
                else:
                    click.secho("Scraper googlebooks: OK (no result but reachable)", fg="green")
            elif name == "bookbrainz":
                from simurg.metadata.scrapers.bookbrainz import BookBrainzScraper

                sc = BookBrainzScraper()
                r = sc.search_isbn("9780140328721")
                if r:
                    click.secho(f"Scraper bookbrainz: OK (found {r.get('title')})", fg="green")
                else:
                    click.secho("Scraper bookbrainz: OK (no result but reachable)", fg="green")
            elif name == "abebooks":
                from simurg.metadata.scrapers.abebooks import AbeBooksScraper

                sc = AbeBooksScraper()
                r = sc.search_isbn("9780140328721")
                if r:
                    click.secho(f"Scraper abebooks: OK (found {r.get('title')})", fg="green")
                else:
                    click.secho("Scraper abebooks: OK (no result but reachable)", fg="green")
            elif name == "goodreads":
                from simurg.metadata.scrapers.goodreads import GoodreadsScraper

                sc = GoodreadsScraper()
                r = sc.search_isbn("9780140328721")
                if r:
                    click.secho(f"Scraper goodreads: OK (found {r.get('title')})", fg="green")
                else:
                    # Detail pages are WAF-gated; None is the graceful path.
                    click.secho(
                        "Scraper goodreads: OK (no result — WAF challenge or no token)",
                        fg="green",
                    )
        except Exception as e:
            click.secho(f"Scraper {name}: FAILED - {e}", fg="yellow")

    # OpenAlex probe (magazine-only ISSN via /sources)
    try:
        from simurg.config import get_config as _cfg2

        _oa_key = str(_cfg2().metadata.get("openalex_api_key", "") or "").strip()
        if not _oa_key:
            click.secho("Scraper openalex: SKIPPED (no openalex_api_key in config)", fg="yellow")
        else:
            from simurg.metadata.scrapers.openalex import OpenAlexScraper

            sc = OpenAlexScraper()
            r = sc.search_magazine("National Geographic")
            if r and (r.get("print_issn") or r.get("electronic_issn")):
                click.secho(
                    f"Scraper openalex: OK (found ISSN {r.get('print_issn') or r.get('electronic_issn')} for {r.get('title')})",
                    fg="green",
                )
            elif r:
                click.secho(
                    f"Scraper openalex: OK (found {r.get('title')} but no ISSN)", fg="yellow"
                )
            else:
                click.secho("Scraper openalex: OK (no result but reachable)", fg="green")
    except Exception as e:
        click.secho(f"Scraper openalex: FAILED - {e}", fg="yellow")

    # Declared elsewhere in the pipeline but not probed by checkconf
    for name in ("librarything", "internetarchive", "libraryofcongress", "crossref"):
        click.secho(f"Scraper {name}: SKIPPED (not exercised by checkconf)", fg="yellow")

    click.secho("=== checkconf done ===", fg="cyan")


@cli.command("health")
def health():
    """Check local deps per salmon/commands.py:361 health."""
    import platform
    import shutil

    click.secho("=== health ===", fg="cyan", bold=True)
    try:
        from simurg.config import get_config_path

        p = get_config_path()
        click.echo(f"Config path: {p} ({'exists' if p.exists() else 'missing'})")
    except Exception as e:
        click.secho(f"Config path: error {e}", fg="red")

    # torrents dir
    try:
        from simurg.config import get_config

        cfg = get_config()
        dott = Path(str(cfg.directory.get("dottorrents_dir", ".torrents")))
        click.echo(f"Dottorrents dir: {dott} ({'exists' if dott.exists() else 'missing'})")
    except Exception:
        dott = Path(".torrents")
        click.echo(f"Dottorrents dir: {dott} ({'exists' if dott.exists() else 'missing'})")

    click.echo(f"Python: {platform.python_version()}")
    click.echo("Deps:")
    deps = ["click", "requests", "ratelimit", "bs4", "torf", "PIL", "ebooklib", "pypdf"]
    for dep in deps:
        try:
            __import__(dep if dep != "bs4" else "bs4")
            click.secho(f" {dep} ✓", fg="green")
        except ImportError:
            try:
                if dep == "PIL":
                    import PIL  # noqa

                    click.secho(f" {dep} ✓", fg="green")
                elif dep == "bs4":
                    import bs4  # noqa

                    click.secho(f" {dep} ✓", fg="green")
                else:
                    raise
            except ImportError:
                click.secho(f" {dep} ✘", fg="red")

    # external utils optional
    click.secho("\nOptional external:", fg="cyan")
    for dep in ["git"]:
        present = shutil.which(dep)
        if present:
            click.secho(f" {dep} ✓ ({present})", fg="green")
        else:
            click.secho(f" {dep} ✘", fg="yellow")
