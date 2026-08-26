# smoked-simurg

Local-only CLI to batch-upload E-Books to [Simurg](https://simurg.world/) (a Gazelle-based
e-book tracker). No global installs — everything lives in this repo and runs via a local
venv.

**MVP command:** `python -m simurg up <directory>` (or `make run DIR=<directory>`).

## Quick start

```bash
# 1. venv + deps
make venv                      # or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. configure (gitignored — never commit)
cp config.example.toml config.toml
#   fill in [tracker.simurg] session (browser cookie) + optional announce_url/image keys

# 3. health & auth checks
make checkconf                 # tracker/auth + image hosts + scrapers
make health                    # local dependency check

# 4. upload a batch of unrelated e-books
make run DIR=./my-batch                       # real uploads (interactive)
make run DIR=./my-batch ARGS="--dry-run"      # full dry-run: stages files, rehosts covers,
                                              # generates real .torrents, but skips the upload POST
```

## Configuration

Everything is configured through a single TOML file, `config.toml`, **at the repo root**. Run
from the repo root so `python -m simurg` finds it (it also looks in the package parent dir).

### Setup

```bash
cp config.example.toml config.toml
```

`config.example.toml` is a committed template with every key pre-filled as a blank/sensible
default. The copy is yours to edit — the repo will always look for `config.toml`, never the
example directly.

### Why `config.toml` is gitignored

`config.toml` is listed in `.gitignore` because it holds **personal secrets**:

- your Simurg **`session`** browser cookie
- your personal **`announce_url`** (`https://tracker.simurg.world/<passkey>/announce`)

Committing it would leak your account cookie and torrent passkey. The template
(`config.example.toml`) has all values blank, so it is safe to commit.

### Sections

| Section | Key | Meaning |
|---|---|---|
| `[directory]` | `download_directory` | where downloaded/staged books live (`.books`) |
| | `dottorrents_dir` | where generated `.torrent` files are written (`.torrents`) |
| | `staging_dir` | where renamed files are moved before torrenting (`.staging`; falls back to `download_directory`, then `.staging`) |
| `[image]` | `cover_uploader` | image host to rehost covers with: `ptscreens` / `imgbb` / `catbox` |
| | `ptscreens_key` / `imgbb_key` / `catbox_userhash` | API key for the chosen host |
| `[metadata]` | `googlebooks_key` | optional Google Books API key (quota) |
| | `cover_fallback_duckduckgo` | allow DuckDuckGo image search as cover fallback |
| `[tracker.simurg]` | `session` | **your browser session cookie** (required for auth) |
| | `api_key` | optional API key (used instead of the cookie if present) |
| | `announce_url` | your personal announce URL; if empty it is derived from the passkey |
| `[upload]` | `user_agent` | HTTP User-Agent sent to the tracker |
| | `log_dupe_tolerance` | fuzzy-match threshold (0–1) for log-based dupe detection |
| | `check_recent_uploads` | also scan the log for similar recent uploads during dupe check |
| | `check_requests` | check for open requests to fill |
| | `copy_uploaded_url_to_clipboard` | copy the uploaded torrent URL after success |
| | `debug_tracker_connection` | verbose tracker request logging |

**Secrets rules:** never commit `config.toml`, `*.torrent`, or a personal `announce_url`.
Keep `config.example.toml` blank. `git status` should never show `config.toml` as modified/added.

## What it does

`up <directory>` loops over unrelated `.pdf/.epub/.mobi/.azw3/.djvu` files (top-level only,
no nesting) and for each one:

1. **Decodes inbuilt metadata** from the file (EPUB OPF / PDF info / MOBI EXTH) — including
   description, edition, and illustrators/editors/translators when the file carries them.
2. **Queries every scraper** (OpenLibrary, Google Books, BookBrainz, AbeBooks) and
   collects all hits. When there is more than one result it prompts you to pick which one
   fills the missing fields (`[i]` inbuilt-only, `[s]` skip file, `[a]` abort all).
3. **Field review** — for fields that differ between the *file's own metadata* and the chosen
   scraper result (title, authors, year, publisher, ISBN, description, edition, illustrators),
   you can choose per field: `[i]` file value, `[s]` scraper value, `[b]` append both, or
   keep the merged default.
4. **Searches Simurg for duplicates** and prompts you to upload to an existing Publication or
   create a new one.
5. **Rehosts the cover** (downscaled to ~500×500, PNG→JPG) via `ptscreens`/`imgbb`/`catbox`;
   DuckDuckGo images as fallback.
6. **Stages/renames** the file to `{Title} - {Author} (year) [ISBN].ext`.
7. **Generates a private torrent** (`source=SIM`, `private=1`, piece length 32768) and
   **uploads** it to Simurg.

`--dry-run` does everything except the final upload POST: full interactive prompts, file
staging, cover rehosting, and a real `.torrent` written to `.torrents/`.

## Options

| Flag | Meaning |
|---|---|
| `--category {ebooks,magazines}` | Upload category (default `ebooks`). Selects the scraper + payload path; magazines use ISSN Portal / Internet Archive / Library of Congress / Crossref / Open Library only |
| `--dry-run` | Full flow (staging, cover rehost, torrent generation) but **no upload POST** |
| `--group-id ID` | Force upload to an existing Publication group id |
| `--cover URL` | Override cover URL |
| `--no-rename` | Skip filename sanitize/staging |

### Magazines

`python -m simurg up <directory> --category magazines` uploads PDF/CBR/CBZ/DJVU
magazine files. The canonical title + issue identity (date / volume / number) are
parsed from the filename (e.g. `National Geographic - June 2020.pdf`), then
filled via magazine-only scrapers. The upload payload uses the Simurg magazine
fields (`magazine_*`); see `docs/PLAN.md §11`.

## Rules it enforces

- Single-file torrents only (no folder nesting), `source:SIM`, `private:1`, piece 32768.
- `.txt` rejected (no transcode). Encrypted PDFs abort.
- Tags: lower-case, dots for spaces, forbidden `epub/pdf/mobi/scan/retail`.
- Source labels: `Retail/Scan/OCR/Convert/Other`.
- `config.toml`, `.torrents/`, `.books/`, `*.torrent`, `.staging/` are gitignored.

## Verified Simurg upload payload

Field names were **verified live** against `https://simurg.world/upload.php`
(`<form name="torrent">`, 2026-08-25). Simurg is a Gazelle (music-tracker) fork, so the POST
field names are Gazelle-legacy names, **not** the on-page labels:

| On-page label | POST `name` | Notes |
|---|---|---|
| Torrent file | `file_input` | single-file torrent |
| Type | `type` | `2` = E-Books |
| Find existing Publication | `publicationid` | not `groupid` |
| Original author / Translator / Editor / Illustrator | `artists[]` + `importance[]` | `importance` = 1 / 3 / 4 / 6 |
| Canonical Publication title | `book_title` | stable identity |
| First published | `original_year` | canonical year |
| Release title | `title` | includes edition wording |
| Release publication year | `year` | edition year |
| Tags | `tags` | comma string, lower, dots for spaces |
| Image | `image` | rehosted cover URL |
| Language | `language` | `English` for MVP |
| Publisher | `record_label` | Gazelle legacy field — label reads "Publisher:" |
| ISBN | `catalogue_number` | Gazelle legacy field — label reads "ISBN:" |
| Page count | `page_count` | from scraper only |
| Canonical Publication synopsis | `book_desc` | BBCode |
| Release notes | `album_desc` | BBCode |
| Format | `format` | `EPUB/PDF/MOBI/AZW3/DJVU` |
| Source | `bitrate` | `Retail/Scan/OCR/Convert/Other` |
| Release description | `release_desc` | file-specific BBCode |

Hidden/auxiliary fields: `auth` (session auth key), `torrent-new`, `workaround_broken_html_entities`.

The authoritative mapping lives in `simurg/uploader/payload.py` — the rest of the pipeline
uses semantic keys (`publisher`, `isbn`, `title`, `year`, ...) and only the payload module
renames them to the tracker's wire format.

## Layout

```
simurg/
  cli.py                # `up` + checkconf + health
  metadata/             # file metadata (epub/pdf/mobi) + combine/enricher
  metadata/scrapers/    # openlibrary, googlebooks, bookbrainz, abebooks, duckduckgo
  trackers/             # BaseGazelleApi + SimurgApi
  uploader/             # payload, dupe, torrent, upload, requests
  images/               # ptscreens, imgbb, catbox
docs/
  PLAN.md               # full spec
  rules.txt             # tracker rules
```

Full spec, execution model, and references: `docs/PLAN.md`.