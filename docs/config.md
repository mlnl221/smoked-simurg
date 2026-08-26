# Configuration

Single file: `config.toml` at the repo root, copied from `config.example.toml` (`config.example.toml:1-32`). Loaded via `simurg/config.py` (`_find_config` tries `cwd/config.toml`, then package parent, `config.py:9-18`). `config.toml` is gitignored; `config.example.toml` is committed blank.

```bash
cp config.example.toml config.toml
# edit session / announce_url / image keys
```

Secrets rule: never commit `config.toml`, `*.torrent`, or a personal `https://tracker.simurg.world/<passkey>/announce` (`AGENTS.md`, `README.md:44-52`).

## Cache

`.cache/magazine_issns.csv` is a local exact-title ISSN cache (gitignored, `uploader/magazine_issn.py:20-140`). It is not configured in `config.toml` — it lives at repo root `.cache/` and is created on demand. Columns: `title,print_issn,electronic_issn,issn,issn_l,source`. A magazine title that was successfully scraped once (via scraper warm, `OpenAlex`, manual `S...` URL, or Simurg browse) is persisted; the next file with the same exact title (casefold, e.g. `Penthouse` == `penthouse` but not `Playboy USA`) reuses `1019-5009` / `1019-5009` without network. Safe to delete; it rebuilds. Never commit it.

## Sections

### `[directory]`

| Key | Default | Meaning |
|---|---|---|
| `upload_directory` | `.books` | Where uploaded/staged books live |
| `dottorrents_dir` | `.torrents` | Where generated `.torrent` files are written |
| `staging_dir` | `.staging` | Where renamed files are moved before torrenting. If empty, falls back to `upload_directory`, then `.staging` (`README.md:58-60`) |

`cli.py:827-839` resolves `dottorrents_dir` from `directory.dottorrents_dir`, overridden by `tracker.simurg.dottorrents_dir` when set.

```toml
[directory]
upload_directory = ".books"
dottorrents_dir = ".torrents"
staging_dir = ".staging"
```

### `[image]`

| Key | Meaning |
|---|---|
| `cover_uploader` | `ptscreens` \| `imgbb` \| `catbox` — host used to rehost covers |
| `ptscreens_key` | API key for `ptscreens` |
| `imgbb_key` | API key for `imgbb` |
| `catbox_userhash` | Optional `catbox` userhash |

The uploader always rehosts (`images/base.py`, `ptscreens.py`, `imgbb.py`, `catbox.py`): download → downscale ~500×500 → PNG→JPG → upload to the chosen host. Source URLs are never hotlinked (`rules.txt:52`, `cli.py:1287-1290`).

```toml
[image]
cover_uploader = "ptscreens"
ptscreens_key = ""
imgbb_key = ""
catbox_userhash = ""
```

### `[metadata]`

| Key | Default | Meaning |
|---|---|---|
| `googlebooks_key` | `""` | Optional Google Books API key (quota) for `GoogleBooksScraper` |
| `cover_fallback_duckduckgo` | `true` | Allow DuckDuckGo image search when scraper/file cover missing (`scrapers/duckduckgo.py`) |
| `librarything_token` | `""` | LibraryThing Talpa API token (also used as REST `apikey`) for `LibraryThingScraper` |
| `openalex_api_key` | `""` | OpenAlex `/sources` API key (free, https://openalex.org/settings/api) — required for ISSN lookup via `OpenAlexScraper` |

```toml
[metadata]
googlebooks_key = ""
cover_fallback_duckduckgo = true
librarything_token = ""
openalex_api_key = ""
```

### `[tracker.simurg]`

| Key | Meaning |
|---|---|
| `session` | **Required for real uploads.** Browser `session` cookie for Simurg (`trackers/simurg.py`, `trackers/base.py`). Empty is allowed only for `--dry-run` (`cli.py:776-820`) |
| `api_key` | Optional API key — used instead of cookie when present |
| `announce_url` | Personal announce URL. If empty, derived from passkey via `ajax.php?action=index` (`config.example.toml:21`); otherwise use `https://tracker.simurg.world/<passkey>/announce` verbatim |
| `dottorrents_dir` | Optional per-tracker override for `.torrents` location |

```toml
[tracker.simurg]
session = "your-browser-cookie"
# api_key = ""
announce_url = ""   # or https://tracker.simurg.world/<passkey>/announce
# dottorrents_dir = ".torrents/simurg"
```

Auth and announce URL are sensitive. Do not log or share them; `make checkconf` validates without printing secrets.

### `[upload]`

| Key | Default / Notes | Meaning |
|---|---|---|
| `user_agent` | `simurg/0.1.0` (`config.example.toml:25`) | HTTP User-Agent sent to tracker |
| `default_editor` | `""` → `$EDITOR` then `nano` | Editor for interactive metadata review (`click.edit` path via `metadata/review.py`) |
| `log_dupe_tolerance` | `0.5` | Fuzzy-match threshold 0–1 for log-based dupe detection (`uploader/dupe.py`) |
| `check_recent_uploads` | `true` | Scan the upload log for similar recent uploads during dupe check |
| `check_requests` | `true` | Check for open requests to fill (`uploader/requests.py`) |
| `copy_uploaded_url_to_clipboard` | `false` | Copy the uploaded torrent URL after success |
| `debug_tracker_connection` | `false` | Verbose tracker request logging (`trackers/base.py`) |

```toml
[upload]
user_agent = "simurg/0.1.0"
default_editor = ""
log_dupe_tolerance = 0.5
check_recent_uploads = true
check_requests = true
copy_uploaded_url_to_clipboard = false
debug_tracker_connection = false
```

## How config is used

- `load_config(path=None)` finds and parses `config.toml` (`config.py:53-72`). `get_config()` is lazy and cached (`config.py:75-81`). `get_config_path()` reports the resolved path (`config.py:84-90`).
- `Config` wraps the TOML dict; `directory`/`image`/`metadata`/`tracker`/`upload` are `_CfgSection` views (`config.py:29-52`). Per-tracker access via `get_tracker_cfg("simurg")` (`config.py:56-60`).
- `checkconf` and `health` read the same file, so `make checkconf` after edits catches misconfig quickly.

## Example

```toml
[directory]
upload_directory = ".books"
dottorrents_dir = ".torrents"
staging_dir = ".staging"

[image]
cover_uploader = "ptscreens"
ptscreens_key = "YOUR_PTSCREENS_KEY"

[metadata]
googlebooks_key = ""
cover_fallback_duckduckgo = true
openalex_api_key = "YOUR_OPENALEX_KEY"

[tracker.simurg]
session = "session_cookie_from_browser_devtools"
announce_url = ""

[upload]
user_agent = "simurg/0.1.0"
log_dupe_tolerance = 0.5
check_recent_uploads = true
check_requests = true
```
