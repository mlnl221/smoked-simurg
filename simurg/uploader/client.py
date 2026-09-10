"""Push uploaded torrents to qBittorrent (typically on a NAS) after tracker upload.

Stripped qBittorrent-only port of smoked-salmon's
``uploader/torrent_client.py`` + the seed half of ``uploader/seedbox.py``.

Two views of one directory (qbit runs on the NAS, simurg in WSL):
- ``local_path``: WSL view of the seed dir (mounted NAS share).
  Content is copied here first so it exists for seeding.
- ``save_path``: qbit/NAS-local view of the SAME dir, passed as
  ``save_path`` to ``torrents_add``.

All failures are warnings, never fatal — the tracker upload already
succeeded by the time this runs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

import click

_WIN_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

# Timeouts: every external call in the push path must be bounded so a dead
# NAS fails fast instead of stalling the per-file upload loop forever.
_WSLPATH_TIMEOUT = 15
_COPY_TIMEOUT_FILE = 120
_COPY_TIMEOUT_DIR = 600
_QBIT_TIMEOUT = (5, 30)  # (connect, read) passed as REQUESTS_ARGS


def masked_url(url: str) -> str:
    """Hide credentials for logging. Never returns auth material."""
    try:
        scheme, sep, rest = url.partition("://")
        if not sep:
            return "****"
        authority, slash, path = rest.partition("/")
        if "@" in authority:
            host = authority.rpartition("@")[2]
            return f"{scheme}://****:****@{host}{slash}{path}"
        return url
    except Exception:
        return "****"


def parse_qbit_url(url: str) -> tuple[str, str | None, str | None]:
    """Parse libtc URL ``qbittorrent+http://user:pass@host:port``.

    Returns (base_url, username, password). Raises ValueError on bad scheme.
    """
    parsed = urlparse(url)
    parts = parsed.scheme.split("+")
    if parts[0] != "qbittorrent" or len(parts) < 2:
        raise ValueError(
            f"Unsupported torrent_client URL (want qbittorrent+http://...): {masked_url(url)}"
        )
    netloc = parsed.netloc
    username: str | None = None
    password: str | None = None
    if "@" in netloc:
        auth, netloc = netloc.rsplit("@", 1)
        if ":" in auth:
            username, password = auth.split(":", 1)
        else:
            username = auth or None
        if username:
            username = unquote(username)
        if password:
            password = unquote(password)
    return f"{parts[1]}://{netloc}{parsed.path}", username, password


class QBittorrentClient:
    """Thin wrapper around qbittorrent-api. Login failure -> client None."""

    def __init__(self, url: str):
        self.base_url, self.username, self.password = parse_qbit_url(url)
        click.secho(f"Connecting to qBittorrent at {masked_url(url)}...", fg="cyan")
        self.client = self.login()

    def login(self):
        try:
            import qbittorrentapi

            qbt = qbittorrentapi.Client(
                host=self.base_url,
                username=self.username,
                password=self.password,
                REQUESTS_ARGS={"timeout": _QBIT_TIMEOUT},
            )
            qbt.auth_log_in()
            click.secho("Connected to qBittorrent", fg="green")
            return qbt
        except ImportError:
            click.secho(
                "qbittorrent-api not installed (pip install -r requirements.txt)",
                fg="red",
                bold=True,
            )
            return None
        except Exception as e:
            click.secho(f"qBittorrent connect failed: {e}", fg="red", bold=True)
            return None

    def add_to_downloader(
        self,
        save_path: str,
        torrent: bytes,
        is_paused: bool = False,
        category: str = "",
    ) -> bool:
        if not self.client:
            return False
        try:
            click.secho("Adding torrent to qBittorrent...", fg="yellow")
            self.client.torrents_add(
                torrent_files=torrent,
                save_path=save_path,
                is_paused=is_paused,
                category=category or None,
            )
            click.secho("Torrent added to qBittorrent", fg="green")
            return True
        except Exception as e:
            click.secho(f"Failed to add torrent to qBittorrent: {e}", fg="red", bold=True)
            return False


def _copy_via_windows_interop(content: Path, local_dir: str) -> Path:
    """Copy via cmd.exe for Windows drive paths (e.g. W:\\dir) unreachable in WSL.

    Translates the WSL source with ``wslpath -w`` (yields a UNC Windows can
    read, incl. /mnt/c and WSL-native paths). Raises RuntimeError on failure.
    """
    win_src = subprocess.run(
        ["wslpath", "-w", str(content)],
        capture_output=True,
        text=True,
        check=True,
        timeout=_WSLPATH_TIMEOUT,
    ).stdout.strip()
    dest_win = local_dir.replace("/", "\\")
    if not dest_win.endswith("\\"):
        dest_win += "\\"
    if content.is_dir():
        cmd = ["cmd.exe", "/c", "xcopy", win_src, f"{dest_win}{content.name}\\", "/E", "/I", "/Y"]
        timeout = _COPY_TIMEOUT_DIR
    else:
        cmd = ["cmd.exe", "/c", "copy", "/Y", win_src, dest_win]
        timeout = _COPY_TIMEOUT_FILE
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"windows copy failed: {(proc.stderr or proc.stdout).strip()[:200]}")
    return Path(local_dir) / content.name


def copy_to_seed_dir(content: Path, local_dir: str) -> Path:
    """Copy staged file (or pack dir) into seed dir. Returns dest."""
    content = Path(content)
    if _WIN_PATH.match(local_dir):
        return _copy_via_windows_interop(content, local_dir)
    dest_dir = Path(local_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / content.name
    if content.is_dir():
        shutil.copytree(content, dest, dirs_exist_ok=True)
    else:
        if dest.exists() and dest.stat().st_size == content.stat().st_size:
            return dest  # ponytail: skip identical re-copy
        shutil.copy2(content, dest)
    return dest


def maybe_push_to_client(torrent_path, content_path, dry_run: bool = False) -> bool:
    """Copy content to NAS mount + add .torrent to qbit. Never raises.

    Returns True only if torrent was accepted by qbit.
    """
    if dry_run:
        return False
    try:
        from simurg.config import get_config

        cfg = get_config()
        ccfg = cfg.client
        enabled = bool(ccfg.get("enabled", False))
        url = str(ccfg.get("torrent_client", "") or "").strip()
        if not enabled or not url:
            return False
        save_path = str(ccfg.get("save_path", "") or "").strip()
        local_path = str(ccfg.get("local_path", "") or "").strip()
        category = str(ccfg.get("category", "") or "").strip()
        add_paused = bool(ccfg.get("add_paused", False))
    except Exception:
        return False

    if not save_path or not local_path:
        click.secho("Torrent client: skipped (set [client] save_path + local_path)", fg="yellow")
        return False

    try:
        dest = copy_to_seed_dir(Path(content_path), local_path)
        click.secho(f"Seed copy: {dest}", fg="cyan")
    except Exception as e:
        click.secho(f"Torrent client: seed copy failed: {e}", fg="red", bold=True)
        return False

    try:
        with open(torrent_path, "rb") as f:
            blob = f.read()
    except Exception as e:
        click.secho(f"Torrent client: cannot read {torrent_path}: {e}", fg="red", bold=True)
        return False

    try:
        client = QBittorrentClient(url)
        return client.add_to_downloader(save_path, blob, is_paused=add_paused, category=category)
    except Exception as e:
        click.secho(f"Torrent client: push failed: {e}", fg="red", bold=True)
        return False
