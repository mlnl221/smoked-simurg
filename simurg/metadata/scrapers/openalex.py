"""OpenAlex scraper — magazine ISSN lookup via /sources.

Uses https://api.openalex.org/sources?search=<title>&api_key=<key>&per_page=10
to resolve ISSN / ISSN-L for periodicals. Magazine-only (categories={"magazine"}).

Docs: https://developers.openalex.org/api-reference/sources/list-sources
Auth: https://developers.openalex.org/api-reference/authentication
Free tier $1/day ~1k search calls; key from config `metadata.openalex_api_key`.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from simurg.metadata.scrapers.base import BaseScraper
from simurg.metadata.scrapers.util import clean_issn


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _pick_issns(issn_list: list | None, issn_l: str | None) -> tuple[str | None, str | None]:
    """Pick print/electronic ISSNs from OpenAlex payload.

    ``issn`` is a list of all ISSNs for the source, ``issn_l`` is the linking ISSN.
    Policy: print_issn = issn_l if present else first issn; electronic_issn = first
    remaining issn distinct from print_issn.
    """
    cleaned: list[str] = []
    for raw in issn_list or []:
        c = clean_issn(raw)
        if c and c not in cleaned:
            cleaned.append(c)
    l_clean = clean_issn(issn_l)
    if l_clean and l_clean not in cleaned:
        # Prefer l_clean as canonical; keep it first for deterministic pick
        cleaned = [l_clean] + [c for c in cleaned if c != l_clean]
    elif l_clean and cleaned and cleaned[0] != l_clean:
        # Ensure l_clean is considered for print_issn even if not first
        pass

    print_issn = l_clean or (cleaned[0] if cleaned else None)
    electronic_issn = None
    for c in cleaned:
        if c != print_issn:
            electronic_issn = c
            break
    # If issn_l was distinct and not already used, electronic may be cleaned[0] when print was l_clean
    if not electronic_issn and l_clean and cleaned:
        for c in cleaned:
            if c != l_clean:
                electronic_issn = c
                break
    # Fallback: if only issn_l exists and no list, print_issn already set
    return print_issn, electronic_issn


class OpenAlexScraper(BaseScraper):
    name = "openalex"
    categories = {"magazine"}
    url_domains: set[str] = {"openalex.org", "api.openalex.org"}

    def _api_key(self) -> str:
        try:
            from simurg.config import get_config

            cfg = get_config()
            return (
                cfg.metadata.get("openalex_api_key", "")
                or cfg.metadata.get("openalex_key", "")
                or cfg.metadata.get("openalex_apikey", "")
                or ""
            ).strip()
        except Exception:
            return ""

    def search_isbn(self, isbn: str) -> dict | None:
        return None

    def search_title_author(self, title: str, authors: list[str]) -> dict | None:
        return None

    def search_magazine(self, title: str, issue: dict | None = None) -> dict | None:
        title = (title or "").strip()
        if not title:
            return None
        api_key = self._api_key()
        # Require key per current OpenAlex policy; silently skip if missing to avoid 429 noise
        if not api_key:
            return None
        try:
            url = "https://api.openalex.org/sources"
            params = {"search": title, "api_key": api_key, "per_page": 10}
            r = self.session.get(url, params=params, timeout=10)
            if r.status_code != 200:
                return None
            data = r.json()
            results = data.get("results") or []
            if not results:
                return None
            # Pick best fuzzy match on display_name; keep threshold low so we always have a candidate
            best = None
            best_score = -1.0
            for src in results:
                name = src.get("display_name") or ""
                score = _fuzzy(title, name)
                # Boost exact ISSN type matches slightly
                if score > best_score:
                    best_score = score
                    best = src
            if best is None:
                best = results[0]
                best_score = _fuzzy(title, best.get("display_name") or "")
            # Reject very poor matches (likely unrelated source)
            if best_score < 0.5:
                # Still return if only one result and type looks magazine-ish? Keep threshold.
                return None
            issn_list = best.get("issn") or []
            issn_l = best.get("issn_l")
            print_issn, electronic_issn = _pick_issns(issn_list, issn_l)
            if not print_issn and not electronic_issn:
                return None
            # Optional publisher extraction: OpenAlex hosts have host_organization_name
            publisher = best.get("host_organization_name") or best.get("host_organization") or None
            if isinstance(publisher, dict):
                publisher = publisher.get("display_name")
            # Country: not reliably in /sources; leave None
            return {
                "title": best.get("display_name") or title,
                "first_published": None,
                "print_issn": print_issn,
                "electronic_issn": electronic_issn,
                "publisher": publisher,
                "country": None,
                "frequency": None,
                "issue_date": (issue or {}).get("issue_date"),
                "issue_date_precision": (issue or {}).get("issue_date_precision"),
                "volume": (issue or {}).get("volume"),
                "issue_number": (issue or {}).get("issue_number"),
                "page_count": None,
                "language": None,
                "cover_url": None,
                "description": None,
                "source_urls": [f"https://openalex.org/{best.get('id', '').split('/')[-1]}"]
                if best.get("id")
                else [],
                "_openalex_raw": best,
            }
        except Exception:
            return None

    def search_url(self, url: str) -> dict | None:
        """Resolve a pasted OpenAlex source URL to ISSN metadata.

        Accepts:
        - https://openalex.org/S137355760
        - https://openalex.org/sources/S137355760
        - https://api.openalex.org/sources/S137355760
        - https://api.openalex.org/sources?filter=display_name.search:National%20Geographic (not supported - use ID URLs)
        """
        import re
        from urllib.parse import urlparse

        # Allow bare ID like "S137355760" (CLI normalizes, but also handle here)
        if re.match(r"^\s*S\d+\s*$", url.strip()):
            sid_match = re.search(r"S\d+", url)
            if sid_match:
                url = f"https://openalex.org/{sid_match.group(0)}"
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        netloc = (parsed.netloc or "").lower()
        if "openalex.org" not in netloc:
            return None
        # Extract S\d+ ID
        m = re.search(r"S\d+", url)
        if not m:
            return None
        sid = m.group(0)
        api_key = self._api_key()
        if not api_key:
            return None
        try:
            api_url = f"https://api.openalex.org/sources/{sid}"
            r = self.session.get(api_url, params={"api_key": api_key}, timeout=10)
            if r.status_code != 200:
                return None
            data = r.json()
            src = data
            if not src.get("display_name"):
                return None
            issn_list = src.get("issn") or []
            issn_l = src.get("issn_l")
            print_issn, electronic_issn = _pick_issns(issn_list, issn_l)
            if not print_issn and not electronic_issn:
                return None
            publisher = src.get("host_organization_name") or src.get("host_organization") or None
            if isinstance(publisher, dict):
                publisher = publisher.get("display_name")
            return {
                "title": src.get("display_name") or sid,
                "first_published": None,
                "print_issn": print_issn,
                "electronic_issn": electronic_issn,
                "publisher": publisher,
                "country": None,
                "frequency": None,
                "page_count": None,
                "language": None,
                "cover_url": None,
                "description": None,
                "source_urls": [f"https://openalex.org/{sid}"],
                "_openalex_raw": src,
            }
        except Exception:
            return None


def fetch_openalex_issn(title: str, session=None) -> dict | None:
    """Convenience helper for the forced post-scrape ISSN gap-fill in cli.py.

    Creates a one-off scraper and returns its magazine result (or None). Keeps the
    session handling outside the CLI so tests can monkeypatch easily.
    """
    title = (title or "").strip()
    if not title:
        return None
    import requests

    sess = session or requests.Session()
    scraper = OpenAlexScraper(sess)
    # Pass empty issue so scraper still returns ISSNs without needing issue dict
    res = scraper.search_magazine(title, None)
    if not res or (not res.get("print_issn") and not res.get("electronic_issn")):
        return None
    return res
