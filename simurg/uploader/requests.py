"""Request checker minimal per F23."""

from __future__ import annotations

import asyncio
from urllib import parse

import click

from simurg.constants import fmt_url

loop = asyncio.get_event_loop()


def get_request_results(gazelle_site, searchstrs):
    results = []
    for s in searchstrs:
        try:
            resp = loop.run_until_complete(gazelle_site.request("requests", search=s))
            items = resp.get("results") if isinstance(resp, dict) else resp
            if not items:
                continue
            for req in items:
                if req not in results:
                    results.append(req)
        except Exception:
            continue
    return results


def print_request_results(gazelle_site, results, searchstr):
    if not results:
        click.secho(f"\nNo requests were found on {gazelle_site.site_string}", fg="green", nl=False)
        click.secho(f" (searchstrs: {searchstr})", bold=True)
    else:
        click.secho(f"\nRequests were found on {gazelle_site.site_string}: ", fg="green", nl=False)
        click.secho(f" (searchstrs: {searchstr})", bold=True)
        for idx, r in enumerate(results[:10]):
            try:
                rid = r.get("requestId") or r.get("id")
                url = gazelle_site.request_url(rid)
                title = r.get("title") or r.get("groupName") or ""
                artist = r.get("artist") or ""
                year = r.get("year") or r.get("groupYear") or ""
                click.echo(f" {idx + 1:02d} >> {fmt_url(url)} | ", nl=False)
                click.secho(f"{artist} - {title} ", fg="cyan", nl=False)
                click.secho(f"({year})", fg="yellow")
            except Exception:
                continue


def _prompt_for_request_id(gazelle_site, results):
    while True:
        rid = click.prompt(
            click.style(
                "\nFill a request? Choose from results, paste a URL, or [N]o.", fg="magenta"
            ),
            default="N",
        )
        s = rid.strip()
        if not s or s.lower().startswith("n"):
            return None
        if s.isdigit():
            idx = int(s) - 1
            if idx < 0:
                idx = 0
            if idx < len(results):
                return int(results[idx].get("requestId") or results[idx].get("id"))
            else:
                return int(s)
        elif s.startswith(gazelle_site.base_url + "/requests.php"):
            qs = parse.parse_qs(parse.urlparse(s).query)
            if "id" in qs:
                return int(qs["id"][0])
            else:
                click.echo("Could not find request ID in URL.")
                continue
        else:
            click.echo("Invalid input.")
            continue


def _confirm_request_id(gazelle_site, request_id):
    try:
        req = loop.run_until_complete(gazelle_site.request("request", id=request_id))
    except Exception as e:
        click.secho(f"{request_id} does not exist. ({e})", fg="red")
        raise click.Abort from e
    click.echo(f"\nSelected Request: {fmt_url(gazelle_site.request_url(request_id))}")
    # show details minimal
    title = req.get("title") or req.get("groupName") or ""
    click.secho(f" {title}", fg="cyan")
    while True:
        resp = click.prompt(
            click.style("\nAre you sure you want to fill this request? [Y]es, [n]o", fg="magenta"),
            default="Y",
        )
        c = resp.strip().lower()
        if c.startswith("y") or c == "":
            return True
        elif c.startswith("n"):
            return False


def check_requests(gazelle_site, searchstrs, dry_run=False):
    if dry_run:
        # Skip prompt in dry-run
        return None
    try:
        from simurg.config import get_config

        cfg = get_config()
        if not cfg.upload.get("check_requests", True):
            return None
    except Exception:
        pass
    results = get_request_results(gazelle_site, searchstrs)
    print_request_results(gazelle_site, results, " / ".join(searchstrs))
    # Always-upload mode: no interactive request prompt, skip request filling
    return None
