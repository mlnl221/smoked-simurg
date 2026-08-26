"""Simurg magazine ISSN fallback — search existing Simurg publications for ISSNs.

Used when OpenAlex returns nothing for a consumer magazine (Playboy/Penthouse
etc.). Direct reuse (option A): surface the ISSNs found on Simurg and ask the
user whether to copy them into the draft metadata (print/electronic separately,
plus a 'use one for both' question when only one distinct ISSN exists).
"""

from __future__ import annotations

import asyncio
import csv
import re
from pathlib import Path

import click

from simurg.constants import fmt_url
from simurg.metadata.scrapers.util import clean_issn

loop = asyncio.get_event_loop()

# --- Cache: .cache/magazine_issns.csv (gitignored) ---------------------------
# Exact-title reuse: if a magazine with this exact canonical title was seen
# before, reuse its ISSNs without scraping again. CSV columns: title,
# print_issn, electronic_issn, issn, issn_l, source. `title` is matched
# case-insensitively after stripping (exact title semantics: "Playboy" ==
# "playboy" but not "Playboy USA").
CACHE_DIR = Path(".cache")
CACHE_FILE = CACHE_DIR / "magazine_issns.csv"
CACHE_HEADERS = ["title", "print_issn", "electronic_issn", "issn", "issn_l", "source"]


def _norm_title(title: str) -> str:
    return (title or "").strip().casefold()


def _ensure_cache_dir() -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def load_magazine_issn_cache() -> dict[str, dict]:
    """Load the CSV cache. Returns {norm_title: {print_issn, electronic_issn, ...}}.

    Missing file returns {}. Bad rows are skipped.
    """
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            out: dict[str, dict] = {}
            for row in reader:
                title = (row.get("title") or "").strip()
                if not title:
                    continue
                key = _norm_title(title)
                # Prefer explicit print/electronic, fallback to generic issn/issn_l
                p = clean_issn(row.get("print_issn") or row.get("issn"))
                e = clean_issn(row.get("electronic_issn") or row.get("issn_l"))
                # If issn == issn_l and only one distinct value, keep both as same
                # (example: 1019-5009 / 1019-5009 for some magazines)
                if not p and row.get("issn"):
                    p = clean_issn(row.get("issn"))
                if not e and row.get("issn_l"):
                    e = clean_issn(row.get("issn_l"))
                if not p and not e:
                    continue
                out[key] = {
                    "title": title,
                    "print_issn": p,
                    "electronic_issn": e,
                    "issn": p,
                    "issn_l": e or p,
                    "source": row.get("source") or "",
                    "raw_row": row,
                }
            return out
    except Exception:
        return {}


def lookup_magazine_issn_cache(canonical_title: str) -> dict | None:
    """Exact-title (case-insensitive) lookup in the cache. Returns None if miss."""
    canonical_title = (canonical_title or "").strip()
    if not canonical_title:
        return None
    cache = load_magazine_issn_cache()
    return cache.get(_norm_title(canonical_title))


def save_magazine_issn_cache(
    canonical_title: str,
    print_issn: str | None,
    electronic_issn: str | None,
    source: str = "",
) -> bool:
    """Append or update the cache with a new ISSN mapping.

    Normalizes ISSNs via ``clean_issn``. If an entry for this exact title
    already exists, it is updated in place (preserving other rows). Returns
    True if a write happened. Never raises.
    """
    canonical_title = (canonical_title or "").strip()
    if not canonical_title:
        return False
    p = clean_issn(print_issn) if print_issn else None
    e = clean_issn(electronic_issn) if electronic_issn else None
    if not p and not e:
        return False
    # legacy columns: issn == print, issn_l == electronic (or print if electronic missing)
    issn = p or e
    issn_l = e or p
    _ensure_cache_dir()
    try:
        # Read existing rows
        rows: list[dict] = []
        existing_keys: dict[str, int] = {}
        if CACHE_FILE.exists():
            with CACHE_FILE.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for idx, row in enumerate(reader):
                    title = (row.get("title") or "").strip()
                    if not title:
                        continue
                    rows.append(row)
                    existing_keys[_norm_title(title)] = idx
        key = _norm_title(canonical_title)
        new_row = {
            "title": canonical_title,
            "print_issn": p or "",
            "electronic_issn": e or "",
            "issn": issn or "",
            "issn_l": issn_l or "",
            "source": source or "",
        }
        if key in existing_keys:
            rows[existing_keys[key]] = {**rows[existing_keys[key]], **new_row}
        else:
            rows.append(new_row)
        # Ensure dir and write atomically
        with CACHE_FILE.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CACHE_HEADERS)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in CACHE_HEADERS})
        return True
    except Exception:
        return False


def _extract_issns(obj: dict) -> tuple[str | None, str | None]:
    """Try every plausible ISSN/ISBN key in a Simurg browse/torrentgroup object.

    Simurg is a Gazelle fork; magazine ISSNs may appear under several legacy
    names. We probe them all and normalize via ``clean_issn`` so ``1234-5678``
    is the canonical form. The first distinct value becomes print, the next
    distinct becomes electronic.
    """
    if not isinstance(obj, dict):
        return None, None

    # Collect candidates in priority order (most explicit first).
    raw_candidates: list[str] = []
    # Direct ISSN fields
    for key in (
        "magazine_print_issn",
        "magazine_electronic_issn",
        "print_issn",
        "electronic_issn",
        "issn",
        "ISSN",
        "issn_l",
        "ISSN_L",
        "catalogue_number",
        "catalogueNumber",
        "isbn",
        "ISBN",
    ):
        val = obj.get(key)
        if val:
            if isinstance(val, (list, tuple)):
                raw_candidates.extend(str(v) for v in val if v)
            else:
                raw_candidates.append(str(val))
    # Nested group object (torrentgroup response often has obj["group"])
    grp = obj.get("group")
    if isinstance(grp, dict):
        for key in (
            "magazine_print_issn",
            "magazine_electronic_issn",
            "print_issn",
            "electronic_issn",
            "issn",
            "catalogue_number",
            "catalogueNumber",
        ):
            val = grp.get(key)
            if val:
                if isinstance(val, (list, tuple)):
                    raw_candidates.extend(str(v) for v in val if v)
                else:
                    raw_candidates.append(str(val))
        # Some Gazelle APIs nest wiki / extra fields
        for key in ("wikiBody", "wiki_body", "description"):
            val = grp.get(key)
            if isinstance(val, str):
                # Look for ISSN pattern inside free text
                for m in re.finditer(r"\d{4}-\d{3}[\dX]", val):
                    raw_candidates.append(m.group(0))

    # Also scan any string values that look like ISSN in the top-level
    # (defensive: Simurg may put ISSN in a generic field).
    if not raw_candidates:
        for v in obj.values():
            if isinstance(v, str) and re.search(r"\d{4}-\d{3}[\dX]", v):
                raw_candidates.append(v)

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in raw_candidates:
        # Split on whitespace/comma/semicolon in case a field contains "1234-5678, 8765-4321"
        parts = re.split(r"[\s,;]+", raw)
        for part in parts:
            c = clean_issn(part)
            if c and c not in seen:
                seen.add(c)
                cleaned.append(c)

    if not cleaned:
        return None, None
    print_issn = cleaned[0]
    electronic_issn = None
    for c in cleaned[1:]:
        if c != print_issn:
            electronic_issn = c
            break
    return print_issn, electronic_issn


def _display_name(obj: dict) -> str:
    return (
        obj.get("groupName")
        or obj.get("group_name")
        or obj.get("name")
        or obj.get("title")
        or obj.get("book_title")
        or ""
    ).strip()


def search_simurg_magazine_issns(gazelle_site, canonical_title: str, limit: int = 10) -> list[dict]:
    """Search Simurg for existing magazine publications matching ``canonical_title``.

    Calls ``browse`` with the canonical title, then for each hit that does not
    already expose an ISSN, fetches ``torrentgroup`` for richer fields. Returns
    a list of dicts each with ``groupId``, ``title``, ``print_issn``,
    ``electronic_issn``, ``publisher``, ``year``, ``tags``, ``url``. Only
    entries with at least one ISSN are kept. Up to ``limit`` entries returned.
    """
    canonical_title = (canonical_title or "").strip()
    if not canonical_title or not gazelle_site:
        return []
    # Avoid unauthenticated dummy site
    if getattr(gazelle_site, "authkey", "dummy") == "dummy":
        return []

    # 1) Browse
    try:
        browse_resp = loop.run_until_complete(
            gazelle_site.request("browse", searchstr=canonical_title)
        )
    except Exception:
        return []

    items = []
    if isinstance(browse_resp, dict):
        items = browse_resp.get("results") or browse_resp.get("groups") or []
        # Some endpoints wrap differently
        if not items and "response" in browse_resp:
            resp = browse_resp["response"]
            if isinstance(resp, dict):
                items = resp.get("results") or resp.get("groups") or []
            elif isinstance(resp, list):
                items = resp
    elif isinstance(browse_resp, list):
        items = browse_resp

    if not items:
        return []

    candidates: list[dict] = []
    # First pass: extract ISSNs directly from browse items
    pending_fetch: list[tuple[dict, int | str]] = []
    for obj in items[: limit * 2]:  # browse may return many; cap before fetch
        gid = obj.get("groupId") or obj.get("group_id") or obj.get("id") or obj.get("groupId")
        # Also try group.id
        if not gid and isinstance(obj.get("group"), dict):
            gid = obj["group"].get("id")
        if not gid:
            continue
        title = _display_name(obj) or canonical_title
        publisher = (
            obj.get("publisher")
            or obj.get("record_label")
            or obj.get("label")
            or (obj.get("group") or {}).get("publisher")
            or ""
        )
        year = obj.get("groupYear") or obj.get("year") or obj.get("original_year") or ""
        tags = obj.get("tags") or (obj.get("group") or {}).get("tags") or []
        print_issn, electronic_issn = _extract_issns(obj)
        if print_issn or electronic_issn:
            candidates.append(
                {
                    "groupId": int(gid) if str(gid).isdigit() else gid,
                    "title": title,
                    "print_issn": print_issn,
                    "electronic_issn": electronic_issn,
                    "publisher": str(publisher).strip() if publisher else "",
                    "year": str(year).strip() if year else "",
                    "tags": tags,
                    "url": f"{gazelle_site.base_url}/torrents.php?id={gid}",
                    "_raw": obj,
                }
            )
        else:
            pending_fetch.append((obj, gid))

    # 2) For hits without ISSN, fetch torrentgroup details (only for top N to avoid hammering)
    need = limit - len(candidates)
    if need > 0 and pending_fetch:
        to_fetch = pending_fetch[: need * 2]

        # Batch fetch torrentgroup for each gid
        async def _fetch_one(gid):
            try:
                return await gazelle_site.torrentgroup(gid)
            except Exception:
                return None

        tasks = [_fetch_one(gid) for _, gid in to_fetch]
        try:
            detailed = loop.run_until_complete(asyncio.gather(*tasks))
        except Exception:
            detailed = [None] * len(tasks)

        for (browse_obj, gid), detail in zip(to_fetch, detailed, strict=False):
            if not detail:
                continue
            # detail may be {"group": {...}, "torrents": [...]} or flat
            print_issn, electronic_issn = _extract_issns(detail)
            # Also try merging browse_obj + detail
            if not print_issn and not electronic_issn:
                merged = {**browse_obj, **(detail if isinstance(detail, dict) else {})}
                if isinstance(detail.get("group"), dict):
                    merged.update(detail["group"])
                print_issn, electronic_issn = _extract_issns(merged)
            if not (print_issn or electronic_issn):
                continue
            title = _display_name(detail) or _display_name(browse_obj) or canonical_title
            # Prefer detail's publisher/year if present
            grp = detail.get("group") if isinstance(detail, dict) else None
            publisher = ""
            year = ""
            tags = []
            if isinstance(grp, dict):
                publisher = grp.get("publisher") or grp.get("record_label") or ""
                year = grp.get("year") or grp.get("original_year") or ""
                tags = grp.get("tags") or []
            if not publisher:
                publisher = browse_obj.get("publisher") or ""
            if not year:
                year = browse_obj.get("groupYear") or browse_obj.get("year") or ""
            if not tags:
                tags = browse_obj.get("tags") or []
            candidates.append(
                {
                    "groupId": int(gid) if str(gid).isdigit() else gid,
                    "title": title,
                    "print_issn": print_issn,
                    "electronic_issn": electronic_issn,
                    "publisher": str(publisher).strip() if publisher else "",
                    "year": str(year).strip() if year else "",
                    "tags": tags,
                    "url": f"{gazelle_site.base_url}/torrents.php?id={gid}",
                    "_raw": detail,
                }
            )
            if len(candidates) >= limit:
                break

    # Dedupe by ISSN pair and title
    seen_keys: set[str] = set()
    deduped: list[dict] = []
    for cand in candidates:
        key = f"{cand.get('print_issn')}|{cand.get('electronic_issn')}|{cand.get('groupId')}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(cand)

    return deduped[:limit]


def prompt_simurg_issn_reuse(
    metadata: dict,
    chosen: dict,
    dry_run: bool = False,
) -> dict:
    """Ask the user whether to copy ISSNs from a chosen Simurg entry into metadata.

    Shows both ISSN values and asks for each missing field separately. When only
    one distinct ISSN exists but both fields are missing, offers a 'use for both'
    question per the spec. Returns a dict of overrides ``{field: value}`` that the
    caller should apply (never mutates ``metadata`` directly). Empty dict means
    keep without ISSN.
    """
    print_issn = (chosen.get("print_issn") or "").strip() or None
    electronic_issn = (chosen.get("electronic_issn") or "").strip() or None
    # Normalize via clean_issn (also handles None)
    print_issn = clean_issn(print_issn) if print_issn else None
    electronic_issn = clean_issn(electronic_issn) if electronic_issn else None

    if not print_issn and not electronic_issn:
        return {}

    missing_print = not metadata.get("print_issn")
    missing_electronic = not metadata.get("electronic_issn")
    if not missing_print and not missing_electronic:
        return {}

    click.secho(
        f"\nISSN from Simurg entry '{chosen.get('title')}' (group {chosen.get('groupId')}):",
        fg="cyan",
        bold=True,
    )
    click.echo(f"  print_issn:      {print_issn or '(none)'}")
    click.echo(f"  electronic_issn: {electronic_issn or '(none)'}")
    if chosen.get("url"):
        click.echo(f"  source: {fmt_url(chosen['url'])}")
    if chosen.get("publisher"):
        click.echo(f"  publisher on Simurg: {chosen['publisher']}")
    click.echo(
        f"  Current metadata — print_issn={metadata.get('print_issn') or '(none)'}  electronic_issn={metadata.get('electronic_issn') or '(none)'}"
    )

    if dry_run:
        # In dry-run, auto-apply what would be offered, but announce it.
        overrides: dict[str, str] = {}
        if missing_print and print_issn:
            overrides["print_issn"] = print_issn
        if missing_electronic and electronic_issn:
            overrides["electronic_issn"] = electronic_issn
        # Single ISSN for both missing -> fill both with same value
        if missing_print and missing_electronic and print_issn and not electronic_issn:
            overrides["electronic_issn"] = print_issn
        if missing_print and missing_electronic and electronic_issn and not print_issn:
            overrides["print_issn"] = electronic_issn
        if overrides:
            click.secho(f"Dry-run: would apply ISSN overrides {overrides} (no prompt)", fg="yellow")
        return overrides

    overrides: dict[str, str] = {}

    # Case: both ISSNs present and both fields missing -> ask each separately,
    # but also offer a 'both' shortcut when user says yes to both it just fills both.
    # Case: only one ISSN but both missing -> explicitly ask 'use for both?'
    distinct = {x for x in (print_issn, electronic_issn) if x}

    if missing_print and missing_electronic and len(distinct) == 1:
        single = print_issn or electronic_issn
        # One distinct ISSN available for two empty slots
        ans = (
            click.prompt(
                f"Only one ISSN ({single}) found. Use it for BOTH print and electronic? [y/N]",
                type=str,
                default="n",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if ans in ("y", "yes"):
            overrides["print_issn"] = single  # type: ignore
            overrides["electronic_issn"] = single  # type: ignore
        else:
            # Offer individually: which slot should get it?
            ans2 = (
                click.prompt(
                    "Use it for [p]rint, [e]lectronic, or [n]either?",
                    type=str,
                    default="p",
                    show_default=False,
                )
                .strip()
                .lower()
            )
            if ans2.startswith("p"):
                overrides["print_issn"] = single  # type: ignore
            elif ans2.startswith("e"):
                overrides["electronic_issn"] = single  # type: ignore
        return overrides

    if missing_print and print_issn:
        ans = (
            click.prompt(
                f"Use print_issn {print_issn} for print? [Y/n]",
                type=str,
                default="y",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if ans in ("y", "yes", ""):
            overrides["print_issn"] = print_issn

    if missing_electronic and electronic_issn:
        ans = (
            click.prompt(
                f"Use electronic_issn {electronic_issn} for electronic? [Y/n]",
                type=str,
                default="y",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if ans in ("y", "yes", ""):
            overrides["electronic_issn"] = electronic_issn

    # If after the above we still have a single ISSN and the other side is
    # still empty but user declined one, offer to reuse the same ISSN?
    # e.g. print filled, electronic still empty but we have print_issn == electronic? handled.

    # Edge: one side missing but its corresponding Simurg ISSN is None while the
    # other side's ISSN exists — offer to reuse that ISSN for the missing side.
    if missing_print and not overrides.get("print_issn") and electronic_issn and not print_issn:
        ans = (
            click.prompt(
                f"No print ISSN on Simurg, but electronic is {electronic_issn}. Use it for print as well? [y/N]",
                type=str,
                default="n",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if ans in ("y", "yes"):
            overrides["print_issn"] = electronic_issn
    if (
        missing_electronic
        and not overrides.get("electronic_issn")
        and print_issn
        and not electronic_issn
    ):
        ans = (
            click.prompt(
                f"No electronic ISSN on Simurg, but print is {print_issn}. Use it for electronic as well? [y/N]",
                type=str,
                default="n",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if ans in ("y", "yes"):
            overrides["electronic_issn"] = print_issn

    return overrides
