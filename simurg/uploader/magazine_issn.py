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
# Exact-publisher reuse: if a magazine with this exact publisher was seen
# before, reuse its ISSNs/country without scraping again. CSV columns: publisher,
# print_issn, electronic_issn, issn, issn_l, country, source. `publisher` is matched
# case-insensitively after stripping (exact publisher semantics: "Penthouse" ==
# "penthouse" but not "Penthouse Letters" vs "Penthouse" if publisher differs).
# Title varies per issue, but publisher/ISSN/country is stable per periodical.
# Use repo-root absolute path so cache is at repo_root/.cache regardless of
# cwd (user may run `python -m simurg up /abs/path` from elsewhere).
try:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
except Exception:
    _REPO_ROOT = Path.cwd()
CACHE_DIR = _REPO_ROOT / ".cache"
CACHE_FILE = CACHE_DIR / "magazine_issns.csv"
CACHE_HEADERS = [
    "publisher",
    "print_issn",
    "electronic_issn",
    "issn",
    "issn_l",
    "country",
    "source",
]


def _norm_publisher(publisher: str) -> str:
    return (publisher or "").strip().casefold()


# Backward compat alias
_norm_title = _norm_publisher


def _ensure_cache_dir() -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def load_magazine_issn_cache() -> dict[str, dict]:
    """Load the CSV cache. Returns {norm_publisher: {print_issn, electronic_issn, ...}}.

    Missing file returns {}. Bad rows are skipped.
    Publisher is the stable key (ISSN/country belong to publisher, not issue title).
    Supports old CSVs with `title` column as fallback for `publisher`.
    """
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            out: dict[str, dict] = {}
            for row in reader:
                # New column is `publisher`; fallback to `title` for old caches
                publisher = (row.get("publisher") or row.get("title") or "").strip()
                if not publisher:
                    continue
                key = _norm_publisher(publisher)
                # Prefer explicit print/electronic, fallback to generic issn/issn_l
                p = clean_issn(row.get("print_issn") or row.get("issn"))
                e = clean_issn(row.get("electronic_issn") or row.get("issn_l"))
                # If issn == issn_l and only one distinct value, keep both as same
                # (example: 1019-5009 / 1019-5009 for some magazines)
                if not p and row.get("issn"):
                    p = clean_issn(row.get("issn"))
                if not e and row.get("issn_l"):
                    e = clean_issn(row.get("issn_l"))
                # country is optional for backward compat (old CSVs have no column)
                country = (row.get("country") or "").strip() or None
                if not p and not e and not country:
                    continue
                out[key] = {
                    "publisher": publisher,
                    "title": publisher,  # compat alias
                    "print_issn": p,
                    "electronic_issn": e,
                    "issn": p,
                    "issn_l": e or p,
                    "country": country,
                    "source": row.get("source") or "",
                    "raw_row": row,
                }
            return out
    except Exception:
        return {}


def lookup_magazine_issn_cache(publisher: str) -> dict | None:
    """Exact-publisher (case-insensitive) lookup in the cache. Returns None if miss."""
    publisher = (publisher or "").strip()
    if not publisher:
        return None
    cache = load_magazine_issn_cache()
    return cache.get(_norm_publisher(publisher))


def save_magazine_issn_cache(
    publisher: str,
    print_issn: str | None,
    electronic_issn: str | None,
    source: str = "",
    country: str | None = None,
) -> bool:
    """Append or update the cache with a new ISSN mapping.

    Normalizes ISSNs via ``clean_issn``. If an entry for this exact publisher
    already exists, it is updated in place (preserving other rows). Returns
    True if a write happened. Never raises.

    ``publisher`` is the stable key (e.g. "Penthouse") — title varies per issue
    but publisher/ISSN/country is stable. ``country`` is optional and persisted
    in the ``country`` column for exact-publisher reuse.
    """
    publisher = (publisher or "").strip()
    if not publisher:
        return False
    p = clean_issn(print_issn) if print_issn else None
    e = clean_issn(electronic_issn) if electronic_issn else None
    c = (country or "").strip() if country else None
    # Allow country-only saves: if ISSN missing but country present, persist it
    if not p and not e and not c:
        return False
    # legacy columns: issn == print, issn_l == electronic (or print if electronic missing)
    issn = p or e or ""
    issn_l = e or p or ""
    _ensure_cache_dir()
    try:
        # Read existing rows
        rows: list[dict] = []
        existing_keys: dict[str, int] = {}
        if CACHE_FILE.exists():
            with CACHE_FILE.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for idx, row in enumerate(reader):
                    # Support old `title` column as fallback
                    pub = (row.get("publisher") or row.get("title") or "").strip()
                    if not pub:
                        continue
                    rows.append(row)
                    existing_keys[_norm_publisher(pub)] = idx
        key = _norm_publisher(publisher)
        new_row = {
            "publisher": publisher,
            "print_issn": p or "",
            "electronic_issn": e or "",
            "issn": issn,
            "issn_l": issn_l,
            "country": c or "",
            "source": source or "",
        }
        if key in existing_keys:
            # Merge: keep existing country if new one empty
            existing = rows[existing_keys[key]]
            merged = {**existing, **{k: v for k, v in new_row.items() if v != ""}}
            # Explicitly allow overwriting ISSNs even with ""? keep old if new empty
            for k in ("print_issn", "electronic_issn", "issn", "issn_l", "country", "publisher"):
                if not new_row.get(k) and existing.get(k):
                    merged[k] = existing[k]
            # Ensure old `title` key is migrated to `publisher`
            if "title" in merged and "publisher" not in merged:
                merged["publisher"] = merged.pop("title")
            elif "title" in merged:
                # keep publisher, drop stale title
                merged.pop("title", None)
            rows[existing_keys[key]] = merged
        else:
            rows.append(new_row)
        # Ensure dir and write atomically - normalize rows to new headers
        # Migrate any old `title` rows to `publisher`
        for r in rows:
            if "publisher" not in r and "title" in r:
                r["publisher"] = r.pop("title")
            r.pop("title", None)
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


def _extract_publisher_country(obj: dict) -> tuple[str | None, str | None]:
    """Extract publisher and country from a Simurg browse/torrentgroup object.

    Probes Gazelle-legacy keys plus magazine-specific ``magazine_publisher`` /
    ``magazine_country``. Checks both top-level and nested ``group``.
    Returns (publisher, country) stripped or None.
    """
    if not isinstance(obj, dict):
        return None, None
    grp = obj.get("group") if isinstance(obj.get("group"), dict) else {}
    # Publisher candidates in priority order
    pub = None
    for src in (obj, grp):
        for key in ("magazine_publisher", "publisher", "record_label", "label"):
            val = src.get(key)
            if val and str(val).strip():
                pub = str(val).strip()
                break
        if pub:
            break
    # Country candidates
    country = None
    for src in (obj, grp):
        for key in ("magazine_country", "country"):
            val = src.get(key)
            if val and str(val).strip():
                country = str(val).strip()
                break
        if country:
            break
    return pub or None, country or None


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
        publisher, country = _extract_publisher_country(obj)
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
                    "country": str(country).strip() if country else "",
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
            # Prefer detail's publisher/country/year if present
            pub_cand, country_cand = _extract_publisher_country(detail)
            # Fallback to browse_obj if detail lacks them
            if not pub_cand or not country_cand:
                pub_b, country_b = _extract_publisher_country(browse_obj)
                if not pub_cand:
                    pub_cand = pub_b
                if not country_cand:
                    country_cand = country_b
            publisher = pub_cand or ""
            country = country_cand or ""
            grp = detail.get("group") if isinstance(detail, dict) else None
            year = ""
            tags = []
            if isinstance(grp, dict):
                year = grp.get("year") or grp.get("original_year") or ""
                tags = grp.get("tags") or []
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
                    "country": str(country).strip() if country else "",
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
    """Ask the user whether to copy ISSNs (+ publisher/country) from a chosen Simurg entry.

    Shows ISSN values plus publisher/country and asks for each missing field
    separately. ISSN handling keeps the spec: when only one distinct ISSN exists
    but both fields are missing, offers a 'use for both' question. Publisher and
    country are only offered when the draft metadata is missing them and Simurg
    has a value — always with explicit approval (Y/n). Returns a dict of
    overrides ``{field: value}`` that the caller should apply (never mutates
    ``metadata`` directly). Empty dict means keep without reuse.
    """
    print_issn = (chosen.get("print_issn") or "").strip() or None
    electronic_issn = (chosen.get("electronic_issn") or "").strip() or None
    # Normalize via clean_issn (also handles None)
    print_issn = clean_issn(print_issn) if print_issn else None
    electronic_issn = clean_issn(electronic_issn) if electronic_issn else None
    # Publisher / country from Simurg candidate (may be None)
    sim_pub = (chosen.get("publisher") or "").strip() or None
    sim_country = (chosen.get("country") or "").strip() or None

    has_issn = bool(print_issn or electronic_issn)
    has_pub_country = bool(sim_pub or sim_country)

    if not has_issn and not has_pub_country:
        return {}

    missing_print = not (metadata.get("print_issn") or "").strip()
    missing_electronic = not (metadata.get("electronic_issn") or "").strip()
    # Publisher/country are considered missing when empty/whitespace
    missing_publisher = not (metadata.get("publisher") or "").strip()
    missing_country = not (metadata.get("country") or "").strip()

    # Only skip if nothing the candidate could fill is missing
    # (ISSN fields considered only if candidate actually has an ISSN)
    need_issn = (missing_print and print_issn) or (missing_electronic and electronic_issn)
    # Edge: single ISSN could fill both, so treat that as need too
    if (
        has_issn
        and missing_print
        and missing_electronic
        and len({x for x in (print_issn, electronic_issn) if x}) == 1
    ):
        need_issn = True
    need_pub = missing_publisher and sim_pub
    need_country = missing_country and sim_country
    if not need_issn and not need_pub and not need_country:
        return {}

    click.secho(
        f"\nSimurg entry '{chosen.get('title')}' (group {chosen.get('groupId')}) — reuse?",
        fg="cyan",
        bold=True,
    )
    click.echo(f"  print_issn:      {print_issn or '(none)'}")
    click.echo(f"  electronic_issn: {electronic_issn or '(none)'}")
    if sim_pub is not None or sim_country is not None:
        click.echo(f"  publisher:       {sim_pub or '(none)'}")
        click.echo(f"  country:         {sim_country or '(none)'}")
    if chosen.get("url"):
        click.echo(f"  source: {fmt_url(chosen['url'])}")
    # Show current draft values for comparison
    cur_parts = [
        f"print_issn={metadata.get('print_issn') or '(none)'}",
        f"electronic_issn={metadata.get('electronic_issn') or '(none)'}",
    ]
    if missing_publisher or sim_pub:
        cur_parts.append(f"publisher={metadata.get('publisher') or '(none)'}")
    if missing_country or sim_country:
        cur_parts.append(f"country={metadata.get('country') or '(none)'}")
    click.echo(f"  Current draft — {'  '.join(cur_parts)}")

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
        # Publisher / country — only fill missing, with explicit dry-run log
        if missing_publisher and sim_pub:
            overrides["publisher"] = sim_pub
        if missing_country and sim_country:
            overrides["country"] = sim_country
        if overrides:
            click.secho(
                f"Dry-run: would apply Simurg overrides {overrides} (no prompt)", fg="yellow"
            )
        return overrides

    overrides: dict[str, str] = {}

    # Case: both ISSNs present and both fields missing -> ask each separately,
    # but also offer a 'both' shortcut when user says yes to both it just fills both.
    # Case: only one ISSN but both missing -> explicitly ask 'use for both?'
    distinct = {x for x in (print_issn, electronic_issn) if x}

    # ISSN prompts — single distinct special case vs per-field
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
    else:
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

    # Publisher / country — always with explicit approval, only when draft is missing
    if missing_publisher and sim_pub:
        ans = (
            click.prompt(
                f"Use publisher '{sim_pub}' from Simurg? [Y/n]",
                type=str,
                default="y",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if ans in ("y", "yes", ""):
            overrides["publisher"] = sim_pub
    if missing_country and sim_country:
        ans = (
            click.prompt(
                f"Use country '{sim_country}' from Simurg? [Y/n]",
                type=str,
                default="y",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if ans in ("y", "yes", ""):
            overrides["country"] = sim_country

    return overrides
