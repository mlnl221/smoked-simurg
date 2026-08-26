# FAQ

## General

**What is smoked-simurg?**
Local-only CLI to batch-upload unrelated e-books (and magazines) to Simurg (`https://simurg.world`, a Gazelle fork). `python -m simurg up <directory>` loops files → single-file private torrents. See `README.md` and `docs/usage.md`.

**Does it require a global install?**
No. Everything lives in the repo and runs via a local `.venv` (`AGENTS.md`). Use `make venv` or `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

**Which Python?**
Python 3.11+ required (`README.md:133`, `ruff.toml:4`). Tested on 3.13.2 (`health` output). Python <3.11 needs `tomli`.

## Configuration

**Where does `config.toml` live?**
Repo root (`./config.toml`). Copy `config.example.toml → config.toml` and edit it. The loader also checks the package parent dirs (`simurg/config.py:11-14`). Always run from repo root so `python -m simurg` finds it.

**Why is `config.toml` gitignored?**
It holds your `session` cookie and personal `announce_url` (`https://tracker.simurg.world/<passkey>/announce`). Committing it leaks account secrets (`README.md:44-52`). `config.example.toml` stays blank.

**How do I get the `session` value?**
Log in to `https://simurg.world` in your browser → DevTools → Application/Storage → Cookies → copy the `session` cookie into `[tracker.simurg].session`. Verify with `make checkconf` (`docs/config.md`).

**What if `announce_url` is empty?**
The tracker derives it from the passkey via `ajax.php?action=index` (`config.example.toml:21`). Set it explicitly only if your announce URL differs (e.g. custom passkey path). Never log or share it; `*.torrent` files are also gitignored.

**Which image host should I use?**
`cover_uploader = "ptscreens"` (default), `imgbb`, or `catbox` (`config.example.toml:8`). Put the key in the matching `*_key` / `catbox_userhash`. Covers are always downloaded, downscaled to ~500×500, PNG→JPG via `Pillow`, and rehosted — never hotlinked (`images/base.py`, `rules.txt:52`).

## Upload flow

**What exactly happens per file?**
Decode inbuilt metadata → query every scraper fresh (ISBN then title+author) → interactive picker (or auto when 0/1 hits) → cross-ISBN enrichment offer → combine + per-field file-vs-scraper review → editor review (skippable) → dupe search + request check → cover rehost → stage/rename → private torrent (`source:SIM`, `piece 32768`) → upload POST (`docs/usage.md` step list, `simurg/cli.py`).

**What does `--dry-run` do?**
Full interactive flow, staging, cover rehosting, and real `.torrent` written to `dottorrents_dir` — but no upload POST (`cli.py:794-820`, `README.md:100-101`). Use it to rehearse prompts and inspect outputs. Between-files tracker-less dry-run skips dupe search with a warning.

**Why do I see a 15 s wait between files?**
Rate-limit protection (`cli.py:842-844`, `_rate_limit_wait` at `cli.py:39-62`). Visible countdown; `Ctrl+C` offers to skip the remaining wait without aborting the batch.

**Can I force an upload into an existing Publication?**
`--group-id ID` forces `publicationid` (`cli.py:652`). It must be the **Publication** id (from `torrents.php?action=publication&id=PID`), not the torrent group id (`torrents.php?id=GID`) — they are different id spaces (`mistakes.md:2026-08-25 groupid`, `payload.py:159-163`). Validated by `GET /upload.php?publicationid=<id>` checking hidden `book_work_id`.

**How is `--source` handled?**
`--source Retail|Scan|OCR|Convert|Other` (`cli.py:662`). Never guessed; missing → `Other` (`cli.py:1138-1140`, `rules.txt:61`). `Retail` requires provenance proof — a clean look is not enough.

**Can I paste a retailer URL instead of auto-searching?**
`--url <URL>` or `[u]` at the prompt. Routed via `enricher.py:85-108` `search_by_url` to the matching scraper (`url_domains`). Supported domains include `openlibrary.org`, `books.google.*`, `bookbrainz.org`, `abebooks.*`, `archive.org`, `loc.gov` (see `enricher.py` scraper registrations).

## Files and formats

**Which file types are allowed?**
Ebooks: `.pdf/.epub/.mobi/.azw3/.djvu` (`constants.py:11`). Magazines: `.pdf/.cbr/.cbz/.djvu` (`constants.py:23`, `rules.txt:127`). `CBR/CBZ` belong in Comics & Manga or Magazines, not ebooks (`rules.txt:86,113`). `.txt` is rejected with a red message (`cli.py:705-710`, `rules.txt:36`).

**Why does it abort on some PDFs?**
Encrypted PDFs (`isEncrypted`) abort with a skip message (`cli.py:860-868`). Simurg forbids DRM-locked payloads (`rules.txt:36`).

**Why does it warn about subdirectories?**
MVP processes only the top level of `<directory>` (`cli.py:687-691`). Publisher packs of multiple volumes belong in one subdir and you should run `up` on that subdir (tip in `cli.py:737-739`). Otherwise each file becomes a separate torrent.

**How does magazine filename parsing work?**
`--category magazines` parses the canonical title + issue identity from the filename (e.g. `National Geographic - June 2020.pdf`) via `metadata/magazine.py:decode_magazine_filename`, then fills via magazine-only scrapers (`enricher.py:192-217`). Requires issue `year` plus `issue_date` or `issue_number` (volume when present, `rules.txt:130`). Upload payload uses `magazine_*` fields (`payload.py:195-281`).

**Where do torrents and staged files go?**
`dottorrents_dir` (default `.torrents`, per-tracker override `tracker.simurg.dottorrents_dir`) for `.torrent` files (`cli.py:827-839`). `staging_dir` (default `.staging`, fallback `download_directory` → `.staging`) for renamed files (`config.example.toml:5`). Staged name: `{Title} - {Author} (year) [ISBN].ext`, `BLACKLISTED_CHARS` `[:?<>\\*|"/]` replaced with `_` (`constants.py:8`, `cli.py:35`).

**What torrent settings are used?**
Single-file, `private=1` (DHT/PEX/LPD disabled), `piece_length=32768`, `source:SIM` literal (`AGENTS.md`, `uploader/torrent.py`, `payload.py` docstring).

## Metadata and scrapers

**Which scrapers are queried?**
Ebooks: `OpenLibrary`, `GoogleBooks`, `BookBrainz`, `AbeBooks`, `PenguinRandomHouse`, `LibraryThing`, `WonderClub`. Magazine-only: `IssnPortal`, `InternetArchive`, `LibraryOfCongress`, `Crossref` (`enricher.py:56-69`). `DuckDuckGo` is cover-image fallback only when `cover_fallback_duckduckgo=true`.

**Is scraper data cached?**
No. Always queried fresh; no persistent cache (`enricher.py:1`, manual smoke test requirement in `AGENTS.md`).

**I got multiple scraper hits — what should I pick?**
The picker shows one line per hit with `_scraper`, title, year, first two authors, publisher, pages, ISBN, fuzzy scores `t=`/`a=`, and first `source_url` as a clickable link (`cli.py:207-239`). Pick the one matching the file's edition. `[i]` keeps inbuilt-only, `[s]` skips file.

**What is cross-source ISBN enrichment?**
After you pick a hit, if an ISBN exists the tool re-queries every scraper by that ISBN and offers to fill empty fields (publisher, year, page_count, description, cover_url, tags) from other sources. Title/authors/year stay locked to the primary pick (`enricher.py:228-252`). Only prompts when gaps exist.

**Why does the editor open after scraping?**
`metadata/review.py:review_metadata` opens `$EDITOR`/`upload.default_editor`/`nano` for freeform fixes (salmon-style). Skip with `--no-review` for non-interactive batches (`cli.py:1206`).

## Tracker rules and dupes

**How are duplicates detected?**
`uploader/dupe.py:generate_dupe_search_strs` builds ISBN + title/author variants (edition words stripped via `EDITION_RE`/`CANONICAL_STRIP_RE` in `constants.py:40-49`) and calls Simurg `browse`/`publication` search via `trackers/base.py` + `simurg.py`. You choose to upload to an existing Publication or create a new one. `check_recent_uploads` and `log_dupe_tolerance` (`config.example.toml:28-29`) tune recent-log fuzzy matching. See `rules.txt:70-77`.

**What are allowed source labels?**
`Retail` (born-digital from authorized channel), `Scan` (page images), `OCR` (scan + OCR), `Convert` (derived from another format), `Other` (explain in description) (`rules.txt:59-66`, `constants.py:34`). Each maps to the Gazelle-legacy `bitrate` POST field (`payload.py:18,126`).

**What tags are forbidden?**
Lower-case, dots for spaces, and `epub`, `pdf`, `mobi`, `azw3`, `djvu`, `scan`, `retail` are forbidden in tags (`constants.py:37`, `rules.txt:53`).

## Common errors

**`config.toml not found`**
Copy `config.example.toml → config.toml` in the repo root (`config.py:13-17`). Check `make health` reports the resolved path.

**`Failed to authenticate` / `make checkconf` fails**
Session cookie expired or `announce_url` wrong. Refresh the `session` cookie from the browser and retry `make checkconf` (`cli.py:788-793`). Use `--dry-run` to test without tracker.

**`Unable to resolve action astral-sh/ruff-action@v4` (CI)**
The floating tag `v4` did not exist — fix is `astral-sh/ruff-action@v3` in `.github/workflows/lint.yml:16,21`.

**`No ebooks files found`**
Directory has no top-level files with allowed extensions, or only subdirectories/`.txt`. Check `--category` and move files to top level.

**`The selected torrent group does not exist.`**
`--group-id` was a torrent group id, not a Publication id, or `groupid` was POSTed alongside `publicationid`. The form field is `publicationid` only (`payload.py:159-163`). Pass the Publication id from `action=publication`.

**Covers not rehosting**
Check `[image].cover_uploader` and its `*_key` / `catbox_userhash` in `config.toml`, then `make checkconf` to test that host. Verify `[metadata].cover_fallback_duckduckgo` if you expect DuckDuckGo fallback.

**`python: command not found` during lint/compile**
Use the venv binaries: `.venv/bin/python`, `.venv/bin/ruff`, `.venv/bin/pytest` (`mistakes.md:2026-08-26`).
