"""Dupe checker stripped for Simurg ebooks - title first, then ISBN fallback, 10 limit."""

from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher as SM
from urllib import parse

import click

from simurg.constants import fmt_url

loop = asyncio.get_event_loop()


def _sanitize_for_dupe(title: str) -> str:
    if not title:
        return ""
    title = re.sub(
        r"[\(\[][^\)\]]*(Edition|Version|Deluxe|Original|Reissue|Remaster|Vol|Mix|Edit|Illustrated|Annotated)[^\)\]]*[\)\]]",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+", " ", title).strip()
    return title


def generate_dupe_search_strs(title: str, authors: list[str], isbn: str | None = None) -> list[str]:
    strs = []
    title = _sanitize_for_dupe(title)
    # normalize lower, remove accents? simplest lower
    # make_searchstrs like salmon: artists + album
    # For books, we use "Author Title" and "Title"
    if authors:
        for a in authors[:2]:
            strs.append(f"{a} {title}".strip())
    strs.append(title)
    if isbn:
        strs.append(isbn)
    # dedupe and filter
    seen = set()
    out = []
    for s in strs:
        low = s.lower().strip()
        if low and low not in seen:
            seen.add(low)
            out.append(s)
    return out


def filter_unnecessary_searchstrs(searchstrs):
    past_strs = []
    new_strs = []
    for stri in sorted(searchstrs, key=len):
        word_set = set(stri.split())
        for prev_word_set in past_strs:
            if all(p in word_set for p in prev_word_set):
                break
        else:
            new_strs.append(stri)
            past_strs.append(word_set)
    return new_strs


def get_search_results(gazelle_site, searchstrs):
    """Title first, then ISBN fallback per E16. Limit display 10 but return all."""
    results = []
    # Do title search first; if results, return them; else fallback to isbn
    # searchstrs includes title+author and isbn last; we will query each individually via browse
    tasks = [gazelle_site.request("browse", searchstr=s) for s in searchstrs]
    for releases in loop.run_until_complete(asyncio.gather(*tasks)):
        # releases may be dict with "results" key
        items = releases.get("results") if isinstance(releases, dict) else releases
        if not items:
            continue
        for release in items:
            if release not in results:
                results.append(release)
    return results


def print_search_results(gazelle_site, results, searchstr):
    if not results:
        click.secho(
            f"\nNo groups found on {gazelle_site.site_string} matching this release.",
            fg="green",
            nl=False,
        )
    else:
        click.secho(
            f"\nResults matching this release were found on {gazelle_site.site_string}: ",
            fg="red",
            nl=False,
        )
        click.secho(f" (searchstrs: {searchstr})", bold=True)
        for r_index, r in enumerate(results[:10]):
            try:
                gid = r.get("groupId") or r.get("group_id") or r.get("id")
                artist = r.get("artist") or r.get("author") or ""
                group_name = r.get("groupName") or r.get("group_name") or r.get("name") or ""
                group_year = r.get("groupYear") or r.get("year") or ""
                tags = r.get("tags") or []
                url = f"{gazelle_site.base_url}/torrents.php?id={gid}"
                release_type = r.get("releaseType") or r.get("type") or ""
                click.echo(f" {r_index + 1:02d} >> {gid} | ", nl=False)
                click.secho(f"{artist} - {group_name} ", fg="cyan", nl=False)
                click.secho(f"({group_year}) [{release_type}] ", fg="yellow", nl=False)
                if tags:
                    click.echo(f"[Tags: {', '.join(tags)}] | {fmt_url(url)}")
                else:
                    click.echo(f"| {fmt_url(url)}")
            except (KeyError, TypeError):
                continue
    if len(results) > 10:
        click.secho(f"... and {len(results) - 10} more (limit 10 displayed)", fg="yellow")


def _prompt_for_group_id(gazelle_site, results, offer_deletion=False):
    while True:
        group_id = click.prompt(
            click.style(
                "\nWould you like to upload to an existing Publication?\n"
                f"Paste a URL{', pick from groups found ' if results else ''}"
                "or [N]ew Publication / [a]bort / [s]kip file",
                fg="magenta",
            ),
            default="N",
        )
        s = group_id.strip()
        if not s or s.lower().startswith("n"):
            click.echo("Uploading to a new Publication.")
            return None
        if s.lower() in ("s", "skip"):
            return "skip"
        if s.lower().startswith("a"):
            raise click.Abort
        if s.isdigit():
            idx = int(s) - 1
            if idx < 0:
                idx = 0
            if idx < len(results):
                gid = (
                    results[idx].get("groupId")
                    or results[idx].get("group_id")
                    or results[idx].get("id")
                )
                return int(gid)
            else:
                # interpret as direct group id
                click.echo(f"Interpreting {s} as a group ID")
                return int(s)
        elif s.lower().startswith(gazelle_site.base_url + "/torrents.php"):
            parsed = parse.urlparse(s)
            qs = parse.parse_qs(parsed.query)
            if "id" in qs:
                return int(qs["id"][0])
            elif "torrentid" in qs:
                tid = qs["torrentid"][0]
                gid = loop.run_until_complete(gazelle_site.get_redirect_torrentgroupid(tid))
                return int(gid) if gid else None
            else:
                click.echo("Could not find group ID in URL.")
                continue
        else:
            click.echo("Invalid input.")
            continue


def print_torrents(gazelle_site, group_id, rset=None, highlight_torrent_id=None):
    if rset is None:
        try:
            rset = loop.run_until_complete(gazelle_site.torrentgroup(group_id))
            # Normalize
            if "group" in rset:
                grp = rset["group"]
                rset["groupName"] = grp.get("name", "")
                rset["artist"] = " ".join(
                    a.get("name", "") for a in grp.get("musicInfo", {}).get("artists", []) or []
                )
                if not rset["artist"]:
                    # try authors? For books, use authors field
                    rset["artist"] = grp.get("author", "") or ""
                rset["groupId"] = grp.get("id", group_id)
                rset["groupYear"] = grp.get("year", "")
        except Exception as e:
            click.secho(f"{group_id} does not exist. ({e})", fg="red")
            raise click.Abort from e

    click.secho(f"\nSelected Publication: {rset.get('groupId')} ", nl=False)
    click.secho(f"| {rset.get('artist', '')} - {rset.get('groupName', '')} ", fg="cyan", nl=False)
    click.secho(f"({rset.get('groupYear', '')})", fg="yellow")
    torrents = rset.get("torrents") or rset.get("torrent") or []
    if torrents:
        click.secho("Releases in this Publication:", fg="yellow", bold=True)
        for t in torrents if isinstance(torrents, list) else [torrents]:
            color = (
                "yellow" if highlight_torrent_id and t.get("id") == highlight_torrent_id else None
            )
            # Book fields: remasterYear, media/source, format, etc.
            year = t.get("remasterYear") or t.get("year") or ""
            media = t.get("media") or t.get("source") or ""
            fmt = t.get("format") or ""
            click.secho(f"> {year} / {media} / {fmt}", fg=color)
    else:
        click.secho("No torrents detail available.", fg="yellow")


def _confirm_group_id(gazelle_site, group_id, results):
    rset = None
    for r in results:
        gid = r.get("groupId") or r.get("group_id") or r.get("id")
        if str(gid) == str(group_id):
            rset = r
            break
    print_torrents(gazelle_site, group_id, rset)
    while True:
        resp = click.prompt(
            click.style(
                "\nAre you sure you want to upload to this Publication? [Y]es, [n]ew, [a]bort",
                fg="magenta",
            ),
            default="Y",
        )
        c = resp.strip().lower()
        if c.startswith("a"):
            raise click.Abort
        elif c.startswith("y") or c == "":
            return True
        elif c.startswith("n"):
            return False


def check_existing_group(gazelle_site, searchstrs, offer_deletion=False, group_id_override=None):
    """High-level dupe check: returns group_id or None or 'skip'."""
    if group_id_override is not None:
        return group_id_override

    results = get_search_results(gazelle_site, searchstrs)
    print_search_results(gazelle_site, results, " / ".join(searchstrs))

    # also check recent uploads if no results and cfg enables it?
    try:
        from simurg.config import get_config

        cfg = get_config()
        check_recent = cfg.upload.get("check_recent_uploads", False)
        tolerance = float(cfg.upload.get("log_dupe_tolerance", 0.5))
    except Exception:
        check_recent = False
        tolerance = 0.5

    if not results and check_recent:
        try:
            # dupe_check_recent_torrents
            recent = gazelle_site.get_uploads_from_log(max_pages=3)
            # Filter by fuzzy > tolerance
            hits = []
            searchstr = searchstrs[0] if searchstrs else ""
            for upload in recent:
                # upload is (torrent_id, artist, title)
                _tid, artist, title = upload
                comp = f"{artist} {title}".strip()
                ratio = SM(None, searchstr.lower(), comp.lower()).ratio()
                if ratio > tolerance:
                    hits.append(upload)
            if hits:
                click.secho(
                    f"\nFound similar recent uploads in the {gazelle_site.site_string} log: ",
                    fg="red",
                    nl=False,
                )
                click.secho(f" (searchstrs: {searchstr})", bold=True)
                for idx, u in enumerate(hits[:5]):
                    click.echo(
                        f" {idx + 1:02d} >> {u[1]} - {u[2]} | {fmt_url(f'{gazelle_site.base_url}/torrents.php?torrentid={u[0]}')}"
                    )
        except Exception:
            pass

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()

    # Auto-select: only reuse an existing Publication if the first match is a
    # high-confidence title+author match. Otherwise prompt instead of silently
    # creating a new Publication (which the tracker will reject as
    # "Publication already exists" if a similar title already exists).
    if results:
        our_title = searchstrs[0] if searchstrs else ""
        # 1) Exact normalized title match anywhere in results → auto-select immediately.
        #    This catches magazine canonical titles ("Penthouse" == "Penthouse") even when
        #    fuzzy ratio is diluted by an author prefix in our_title ("Author Title").
        norm_our = _norm(our_title)
        # Also try bare title (last searchstr without author) for ebooks.
        norm_bare = _norm(searchstrs[-2] if len(searchstrs) >= 2 else our_title)
        # collect titles from all results for exact check
        for r in results:
            cand_title_raw = r.get("groupName") or r.get("name") or ""
            cand_artist_raw = r.get("artist") or r.get("author") or ""
            gid_cand = r.get("groupId") or r.get("group_id") or r.get("id")
            if gid_cand is None:
                continue
            norm_cand = _norm(cand_title_raw)
            # exact title equality
            if norm_cand and norm_cand == norm_our:
                click.secho(
                    f"Auto-selecting existing Publication {gid_cand} ({cand_title_raw}) [exact title match]",
                    fg="yellow",
                )
                return int(gid_cand)
            if norm_cand and norm_cand == norm_bare:
                click.secho(
                    f"Auto-selecting existing Publication {gid_cand} ({cand_title_raw}) [exact title match]",
                    fg="yellow",
                )
                return int(gid_cand)
            # exact "artist title" combined match
            if cand_artist_raw:
                norm_combined = _norm(f"{cand_artist_raw} {cand_title_raw}")
                if norm_combined and norm_combined == norm_our:
                    click.secho(
                        f"Auto-selecting existing Publication {gid_cand} ({cand_title_raw}) [exact author+title match]",
                        fg="yellow",
                    )
                    return int(gid_cand)

        best = results[0]
        gid = best.get("groupId") or best.get("group_id") or best.get("id")
        cand_title = best.get("groupName") or best.get("name") or ""
        cand_artist = best.get("artist") or best.get("author") or best.get("groupYear") or ""
        # Compute title similarity and optional artist similarity
        t_ratio = SM(None, our_title.lower(), cand_title.lower()).ratio()
        a_ratio = 0.0
        # searchstrs like "Harlan Coben The Woods" include author; compare against artist if present
        if cand_artist:
            a_ratio = SM(None, our_title.lower(), (cand_artist + " " + cand_title).lower()).ratio()
        ratio = max(t_ratio, a_ratio)
        # High-confidence threshold: exact or near-exact title
        if ratio >= 0.85 and gid is not None:
            click.secho(
                f"Auto-selecting existing Publication {gid} ({cand_title}) [similarity {ratio:.2f}]",
                fg="yellow",
            )
            return int(gid)
        # Low-confidence but results exist — prompt interactively instead of
        # silently creating a new Publication that the site may reject.
        click.secho(
            f"Results not a confident match ({ratio:.2f}) — please confirm.",
            fg="yellow",
        )
        try:
            chosen = _prompt_for_group_id(gazelle_site, results)
        except click.Abort:
            raise
        if chosen == "skip":
            return "skip"
        if chosen is None:
            click.secho("Creating new Publication.", fg="green")
            return None
        # Confirm the chosen id shows correct publication
        try:
            if _confirm_group_id(gazelle_site, chosen, results):
                return int(chosen)
            click.secho("Creating new Publication.", fg="green")
            return None
        except click.Abort:
            raise
        except Exception:
            return int(chosen)
    click.secho("No existing Publication found - creating new.", fg="green")
    return None
