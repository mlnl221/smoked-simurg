"""Stripped BaseGazelleApi for Simurg - minimal copy of salmon/trackers/base.py.

Keeps: authenticate, request with rate limit, browse/torrentgroup/torrent,
get_redirect_torrentgroupid, upload/api_key_upload/site_page_upload + parse helpers.
Drops: artist_rls, label_rls, log crawl heavy, report_lossy_master, etc.
"""

from __future__ import annotations

import asyncio
import re
from collections import namedtuple
from json.decoder import JSONDecodeError
from urllib.parse import parse_qs, urlparse

import click
import requests
from bs4 import BeautifulSoup
from ratelimit import RateLimitException, limits, sleep_and_retry
from requests.exceptions import ConnectTimeout, ReadTimeout

from simurg.errors import LoginError, RequestError, RequestFailedError

loop = asyncio.get_event_loop()

SearchReleaseData = namedtuple(
    "SearchReleaseData",
    ["lossless", "lossless_web", "year", "artist", "album", "release_type", "url"],
)


class BaseGazelleApi:
    site_code: str = "SIM"
    base_url: str = ""
    tracker_url: str = ""
    site_string: str = "SIM"

    def __init__(self):
        self.headers = {
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
            "User-Agent": "simurg/0.1.0",
        }
        # lazy import to avoid circular
        try:
            from simurg.config import get_config

            cfg = get_config()
            try:
                ua = cfg.upload.get("user_agent", None)
                if ua:
                    self.headers["User-Agent"] = str(ua)
            except Exception:
                pass
            try:
                dd = cfg.directory.get("dottorrents_dir", ".torrents")
                self.dot_torrents_dir = str(dd)
            except Exception:
                self.dot_torrents_dir = ".torrents"
        except Exception:
            self.dot_torrents_dir = ".torrents"

        if not hasattr(self, "cookie"):
            self.cookie = ""
        if not hasattr(self, "api_key"):
            self.api_key = None

        self.session = requests.Session()
        self.session.headers.update(self.headers)

        self.authkey = None
        self.passkey = None
        self.authenticate()

    @property
    def announce(self):
        # Simurg announce may be overridden via config announce_url
        try:
            from simurg.config import get_config

            cfg = get_config()
            tcfg = cfg.get_tracker_cfg("simurg")
            url = tcfg.get("announce_url", "")
            if url:
                return str(url).strip()
        except Exception:
            pass
        return f"{self.tracker_url}/{self.passkey}/announce"

    def request_url(self, id):
        return f"{self.base_url}/requests.php?action=view&id={id}"

    def authenticate(self):
        """Authenticate via session cookie -> authkey/passkey."""
        self.session.cookies.clear()
        if not getattr(self, "cookie", None):
            # Allow anonymous / dry-run without cookie - set dummy keys
            # But real upload will need it; raise only when actually using network?
            # For now set dummy and try; if empty skip request.
            # To keep behavior similar to salmon, require cookie unless testing.
            # We'll allow empty and not request index if so (for dry-run)
            self.authkey = "dummy"
            self.passkey = "dummy"
            return
        self.session.cookies["session"] = self.cookie
        try:
            acctinfo = loop.run_until_complete(self.request("index"))
        except RequestError as err:
            raise LoginError(str(err)) from err
        self.authkey = acctinfo["authkey"]
        self.passkey = acctinfo["passkey"]

    @sleep_and_retry
    @limits(10, 10)
    async def request(self, action, **kwargs):
        url = self.base_url + "/ajax.php"
        params = {"action": action, **kwargs}
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(url, params=params, timeout=10, allow_redirects=False),
            )
            # debug
            try:
                from simurg.config import get_config

                cfg = get_config()
                if cfg.upload.get("debug_tracker_connection", False):
                    click.secho("URL: ", fg="cyan", nl=False)
                    click.secho(url, fg="yellow")
                    click.secho("Params: ", fg="cyan", nl=False)
                    click.secho(str(params), fg="yellow")
                    click.secho("Response: ", fg="cyan", nl=False)
                    click.secho(str(resp), fg="yellow")
                    click.secho("Response Text: ", fg="cyan", nl=False)
                    click.secho(resp.text[:2000], fg="green")
            except Exception:
                pass

            resp_json = resp.json()
        except JSONDecodeError as err:
            raise LoginError(f"Failed to decode JSON for {action}: {err}") from err
        except (ConnectTimeout, ReadTimeout):
            click.secho(
                "Connection to API timed out, try script again later.",
                fg="red",
            )
            raise click.Abort() from None

        if resp_json.get("status") != "success":
            err_msg = resp_json.get("error", "unknown error")
            if "rate limit" in str(err_msg).lower():
                retry_after = float(resp.headers.get("Retry-After", "20"))
                click.secho(f"Rate limit exceeded, waiting {retry_after} seconds...", fg="yellow")
                raise RateLimitException("Rate limit exceeded", period_remaining=retry_after)
            else:
                raise RequestFailedError(err_msg)
        return resp_json["response"]

    async def torrentgroup(self, group_id):
        return await self.request("torrentgroup", id=group_id)

    @staticmethod
    def _parse_publication_prefill(html_text: str, publication_id: str):
        """Return (ok, canonical_title) for a /upload.php?publicationid=<id> page.

        Simurg pre-fills the hidden `publicationid` field (id="book_work_id") with
        the id when it resolves to a real Publication, and leaves it empty for an
        unknown id. That is the reliable signal that <id> is valid.
        """
        field = re.search(r'id="book_work_id" name="publicationid" value="([^"]*)"', html_text)
        value = field.group(1) if field else ""
        if value == str(publication_id):
            bt = re.search(r'id="book_title"[^>]*value="([^"]*)"', html_text)
            return True, (bt.group(1) if bt else "")
        return False, ""

    def validate_publication_id(self, publication_id):
        """Best-effort check that <publication_id> is a real Simurg Publication.

        Loading /upload.php?publicationid=<id> pre-fills the upload form when the
        id is valid. Returns (ok: bool, canonical_title: str). On network timeout
        returns (False, "") so the caller can decide whether to block.
        """
        url = self.base_url + "/upload.php"
        try:
            resp = self.session.get(url, params={"publicationid": publication_id}, timeout=10)
        except (ConnectTimeout, ReadTimeout):
            return False, ""
        return self._parse_publication_prefill(resp.text, str(publication_id))

    async def get_redirect_torrentgroupid(self, torrentid):
        url = self.base_url + "/torrents.php"
        params = {"torrentid": torrentid}
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(url, params=params, timeout=10, allow_redirects=False),
            )
            location = resp.headers.get("Location")
            if location:
                parsed = urlparse(location)
                query = parse_qs(parsed.query)
                torrent_group_id = query.get("id", [None])[0]
                return torrent_group_id
            else:
                click.secho(
                    "Couldn't retrieve torrent_group_id from torrent_id, no Redirect found!",
                    fg="red",
                )
                raise click.Abort()
        except (ConnectTimeout, ReadTimeout):
            click.secho("Connection to API timed out.", fg="red")
            raise click.Abort() from None

    async def get_request(self, id):
        data = {"id": id}
        return await self.request("request", **data)

    async def fetch_log(self, page):
        url = f"{self.base_url}/log.php"
        resp = await loop.run_in_executor(
            None,
            lambda: self.session.get(url, params={"page": page}, headers=self.headers),
        )
        return resp

    def get_uploads_from_log(self, max_pages=3):
        recent_uploads = []
        tasks = [self.fetch_log(i) for i in range(1, max_pages + 1)]
        for page in loop.run_until_complete(asyncio.gather(*tasks)):
            recent_uploads += self.parse_uploads_from_log_html(page.text)
        return recent_uploads

    async def api_key_upload(self, data, files):
        url = self.base_url + "/ajax.php?action=upload"
        data["auth"] = self.authkey
        api_key_headers = {**self.headers, "Authorization": self.api_key}
        resp = await loop.run_in_executor(
            None,
            lambda: self.session.post(url, data=data, files=files, headers=api_key_headers),
        )
        try:
            resp_json = resp.json()
        except Exception as e:
            click.secho(
                f"Failed to decode JSON response status {resp.status_code}", fg="red", err=True
            )
            click.secho(f"Response text: {resp.text[:2000]!r}", fg="red", err=True)
            raise click.Abort from e

        try:
            if resp_json["status"] != "success":
                raise RequestError(f"API upload failed: {resp_json.get('error')}")
            elif resp_json["status"] == "success":
                if (
                    "requestid" in resp_json["response"] and resp_json["response"]["requestid"]
                ) or (
                    "fillRequest" in resp_json["response"]
                    and resp_json["response"]["fillRequest"]
                    and resp_json["response"]["fillRequest"]["requestId"]
                ):
                    requestId = (
                        resp_json["response"]["requestid"]
                        if "requestid" in resp_json["response"]
                        else resp_json["response"]["fillRequest"]["requestId"]
                    )
                    if requestId == -1:
                        click.secho("Request fill failed!", fg="red")
                    else:
                        click.secho("Filled request: " + self.request_url(requestId), fg="green")

                torrent_id = 0
                group_id = 0
                if "torrentid" in resp_json["response"]:
                    torrent_id = resp_json["response"]["torrentid"]
                    group_id = resp_json["response"]["groupid"]
                elif "torrentId" in resp_json["response"]:
                    torrent_id = resp_json["response"]["torrentId"]
                    group_id = resp_json["response"]["groupId"]
                elif "torrent_id" in resp_json["response"]:
                    torrent_id = resp_json["response"]["torrent_id"]
                    group_id = resp_json["response"]["group_id"]
                return torrent_id, group_id
        except TypeError as err:
            raise RequestError(f"API upload failed, response text: {resp.text}") from err

    async def site_page_upload(self, data, files):
        # Simurg's existing-publication path uses `publicationid` only (NOT the
        # legacy `groupid` field — that resolves as a torrent group id and fails).
        if "publicationid" in data:
            url = self.base_url + "/upload.php"
        elif "groupid" in data:
            url = self.base_url + f"/upload.php?groupid={data['groupid']}"
        else:
            url = self.base_url + "/upload.php"
        data["auth"] = self.authkey
        resp = await loop.run_in_executor(
            None,
            lambda: self.session.post(url, data=data, files=files, headers=self.headers),
        )
        if self.announce in resp.text:
            match = re.search(r'<p style="color: red; text-align: center;">(.+)</p>', resp.text)
            if match:
                raise RequestError(f"Site upload failed: {match[1]} ({resp.status_code})")
        if "requests.php" in resp.url:
            try:
                torrent_id = self.parse_torrent_id_from_filled_request_page(resp.text)
                group_id = await self.get_redirect_torrentgroupid(torrent_id)
                click.secho(f"Filled request: {resp.url}", fg="green")
                return torrent_id, group_id
            except (TypeError, ValueError) as err:
                soup = BeautifulSoup(resp.text, "html.parser")
                error = soup.find("h2", string="Error")
                p_tag = error.parent.parent.find("p") if error else None
                error_message = p_tag.text if p_tag else resp.text[:500]
                raise RequestError(f"Request fill failed: {error_message}") from err
        try:
            return self.parse_most_recent_torrent_and_group_id_from_group_page(resp.text)
        except TypeError as err:
            # Save full HTML for debugging
            try:
                import pathlib

                pathlib.Path(".failed").mkdir(exist_ok=True)
                pathlib.Path(".failed/upload.html").write_text(resp.text, encoding="utf-8")
                pathlib.Path(".failed/upload_url.txt").write_text(resp.url, encoding="utf-8")
            except Exception:
                pass
            raise RequestError(
                f"Site upload failed, response text: {resp.text[:2000]} url: {resp.url}"
            ) from err

    async def upload(self, data, files):
        if hasattr(self, "api_key") and self.api_key:
            return await self.api_key_upload(data, files)
        else:
            return await self.site_page_upload(data, files)

    def parse_most_recent_torrent_and_group_id_from_group_page(self, text):
        # Try JSON first (Simurg duplicate/error returns JSON)
        try:
            import json

            j = json.loads(text)
            if isinstance(j, dict) and "error" in j and "torrentid" in j["error"]:
                m = re.search(r"torrentid=(\d+)", j["error"])
                if m:
                    tid = int(m.group(1))
                    # try to find group/release id in same string
                    m2 = re.search(r"releaseid=(\d+)", j["error"])
                    gid = int(m2.group(1)) if m2 else tid
                    return tid, gid
        except Exception:
            pass

        torrent_ids = []
        group_ids = []
        soup = BeautifulSoup(text, "html.parser")
        for pl in soup.find_all("a", class_="tooltip"):
            href = pl.get("href", "")
            torrent_url = re.search(r"torrents\.php\?torrentid=(\d+)", href)
            if torrent_url:
                torrent_ids.append(int(torrent_url[1]))
        for pl in soup.find_all("a", class_="brackets"):
            href = pl.get("href", "")
            group_url = re.search(r"upload\.php\?groupid=(\d+)", href)
            if group_url:
                group_ids.append(int(group_url[1]))
        if not torrent_ids or not group_ids:
            # Fallback: look for any torrents.php ids - Simurg uses action=publication
            for a in soup.find_all("a", href=True):
                href = a["href"]
                m = re.search(r"torrents\.php\?id=(\d+)", href)
                if m:
                    group_ids.append(int(m[1]))
                m2 = re.search(r"torrentid=(\d+)", href)
                if m2:
                    torrent_ids.append(int(m2[1]))
                # Simurg publication/release ids
                m3 = re.search(r"releaseid=(\d+)", href)
                if m3:
                    group_ids.append(int(m3.group(1)))
                m4 = re.search(r"action=publication[^\"]*id=(\d+)", href)
                if m4:
                    group_ids.append(int(m4.group(1)))
        # Also regex directly on text for any torrentid
        if not torrent_ids:
            for m in re.finditer(r"torrentid=(\d+)", text):
                torrent_ids.append(int(m.group(1)))
        if not group_ids:
            for m in re.finditer(r"releaseid=(\d+)", text):
                group_ids.append(int(m.group(1)))
            for m in re.finditer(r"groupId[\"']?\s*[:=]\s*\"?(\d+)", text):
                group_ids.append(int(m.group(1)))
        if not torrent_ids or not group_ids:
            raise TypeError("Could not parse group/torrent ids from response")
        return max(torrent_ids), max(group_ids)

    def parse_torrent_id_from_filled_request_page(self, text):
        torrent_ids = []
        soup = BeautifulSoup(text, "html.parser")
        for pl in soup.find_all("a", string="Yes"):
            href = pl.get("href", "")
            torrent_url = re.search(r"torrents\.php\?torrentid=(\d+)", href)
            if torrent_url:
                torrent_ids.append(int(torrent_url[1]))
        if not torrent_ids:
            raise TypeError("No torrent id found")
        return max(torrent_ids)

    def parse_uploads_from_log_html(self, text):
        log_uploads = []
        soup = BeautifulSoup(text, "html.parser")
        for entry in soup.find_all("span", class_="log_upload"):
            a = entry.find("a")
            if not a or not a.get("href"):
                continue
            torrent_id = a["href"][23:] if len(a["href"]) > 23 else a["href"].split("=")[-1]
            try:
                torrent_string = re.findall(r"\((.*?)\) \(", a.next_sibling)[0].split(" - ")
            except BaseException:
                continue
            artist = torrent_string[0]
            if len(torrent_string) > 1:
                title = torrent_string[1]
            else:
                artist = ""
                title = torrent_string[0]
            log_uploads.append((torrent_id, artist, title))
        return log_uploads
