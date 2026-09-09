# smoked-simurg

> **Warning: Work in progress — expect bugs.** This project is under active development. APIs, CLI flags, and the upload flow may change without notice. Test with `--dry-run` and verify payloads before real uploads.

Local-only CLI to batch-upload E-Books to [Simurg](https://simurg.world/) (a Gazelle-based e-book tracker). No global installs — everything lives in this repo and runs via a local `.venv`.

**MVP:** `python -m simurg up <directory>` (or `make run DIR=<directory>`) → N unrelated files (`.pdf/.epub/.mobi/.azw3/.djvu`) → N single-file private torrents (`source:SIM`, `private:1`, piece 32768). Each run processes the first `--limit` sorted files (default 50, `0` = unlimited) — rerun the same command for the next batch.

## Quick start

```bash
make venv                      # or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml   # fill [tracker.simurg] session + optional announce_url / image keys
make checkconf                 # tracker/auth + image hosts + scrapers
make health                    # local deps
make run DIR=./my-batch ARGS="--dry-run"   # full dry-run: prompts, cover rehost, .torrent gen — no upload POST
make run DIR=./my-batch                    # real uploads (interactive)
```

> Run from the repo root so `python -m simurg` finds `config.toml`. `config.toml` and `.cache/magazine_issns.csv` are gitignored — never commit `session`, `announce_url`, `*.torrent`, or cached ISSNs.

## Docs

Human-readable wiki lives in `docs/`:

| Doc | What it covers |
|---|---|
| [`docs/setup.md`](docs/setup.md) | Venv, deps, `config.toml` placement, `make health`/`checkconf`, pre-commit hook, lint/format |
| [`docs/usage.md`](docs/usage.md) | `up` flow per file, all flags (`--dry-run`, `--category`, `--source`, `--format`, `--language`, `--group-id`, `--cover`, `--url`, `--no-rename`, `--no-review`, `--limit`), magazines, batch tips |
| [`docs/config.md`](docs/config.md) | Every `config.toml` section/key (`[directory]`, `[image]`, `[metadata]`, `[tracker.simurg]`, `[upload]`), secrets, image handling |
| [`docs/architecture.md`](docs/architecture.md) | Layout, data flow, modules (`metadata`, `trackers`, `uploader`, `images`), verified Gazelle-legacy payload mapping, invariants |
| [`docs/faq.md`](docs/faq.md) | Auth, encrypted PDFs, `.txt` rejection, dupes, `publicationid` vs `groupid`, covers, rate-limit, troubleshooting |
| [`docs/rules.txt`](docs/rules.txt) | Tracker rules (source-of-truth copy, takes precedence) |

## What it does (summary)

`up <directory>` loops top-level files and per file: decodes inbuilt metadata, queries scrapers fresh (ISBN → title/author; magazines reuse `.cache/magazine_issns.csv` publisher-keyed ISSN when seen before), lets you pick hits and review file-vs-scraper fields (magazines: `OpenAlex` forced gap-fill → manual `S...` paste → Simurg browse fallback for Playboy/Penthouse), checks Simurg for dupes, validates + rehosts the cover (downscaled ~500×500, PNG→JPG), stages/renames to `{Title} - {Author} (year) [ISBN].ext` (magazines: `{Title} - {Issue label} (year).ext`, padded `YYYY-MM-DD`), builds a private torrent (`source:SIM`, `private:1`, `32768`) and uploads it. Single-file torrents only; `.txt` rejected; encrypted PDFs abort.

Full steps, options, and payload details are in `docs/usage.md` and `docs/architecture.md`.

## Scrapers

Ebooks query `OpenLibrary`, `GoogleBooks`, `BookBrainz`, `AbeBooks`, `PenguinRandomHouse`, `LibraryThing`, and `WonderClub` — always fresh, no persistent cache. Magazines query `OpenAlex` (ISSN/ISSN-L, needs `metadata.openalex_api_key`), `InternetArchive`, `LibraryOfCongress`, `Crossref`, plus `LibraryThing`/`WonderClub` (dual-category). `OpenLibrary` is the only source for work-level `first_publish_year` (First Published date); most other scrapers only know the edition year.

Paste any result link back with `[u]` or `--url` — trailing title slugs are accepted (`.../books/OL4437227M/Catch_me_if_you_can` works). Paste domains: `openlibrary.org`, `books.google.com` / `googleapis.com` (`?id=` or `/volumes/<id>`), `bookbrainz.org` (`/edition|/work|/book/<bbid>`), `abebooks.com` (product, `/bd`, or search pages), `penguinrandomhouse.com`, `librarything.com` (`/work/<id>`), `wonderclub.com` (`/books|/magazines/<slug>`, `/<slug>-<isbn>`), `archive.org` (`/details/<id>`, deep links OK), `loc.gov`, `openalex.org` / `api.openalex.org` (bare `S...` IDs OK). `Crossref` has no paste support.

Caveats: `LibraryThing` returns no cover/publisher/description and needs `metadata.librarything_token` (without it, silent no-results); `LibraryOfCongress` returns no cover; `Crossref` returns ISSNs only.

`make checkconf` probes `openlibrary`/`googlebooks`/`bookbrainz`/`abebooks` (live ISBN lookup) and `openalex` when a key is set; the rest report `SKIPPED`.

## Covers

Priority per file: `--cover` override → edited `[img]` image URL → scraper cover → DuckDuckGo fallback (search via `"<author> <title> book cover"`, disable with `cover_fallback_duckduckgo = false`) → embedded file cover (ebooks only). Source URLs are never hotlinked — every cover is downloaded and rehosted via `cover_uploader` (`ptscreens`/`imgbb`/`catbox`), downscaled to ~500×500 and converted PNG→JPG.

Downloads are validated, not just HTTP-200: `Content-Type: image/*`, ≥5 KB body (rejects Google/OpenLibrary placeholder images), `Pillow` parse check, and ≥100×100 px dimensions. Anything failing falls through to the next source with a `(missing/invalid)` note.

Two interactive guards (skipped in `--dry-run`):
- If all automatic sources fail, you are prompted to paste a cover image URL (validated the same way, retry until valid, `[s]` skips without cover, `[a]` aborts).
- After rehosting, the hosted link is printed for you to open and check: `[Enter]` keeps it, `[u]` pastes a replacement URL (re-downloaded, re-validated, re-hosted), `[s]` drops the cover, `[a]` aborts.

## Review & payload notes

- `year` is the work's First Published date, `remaster_year` the edition's Release year. They are never auto-copied: when `year` is empty the editor buffer pre-fills it from `remaster_year`, but saving the buffer is your explicit accept. The tracker POST already sends `year = remaster_year or year`, so uploads never go out year-less.
- Tags are posted as one comma-separated `tags` field. Review edits may turn them into a list; the payload layer joins lists back (`['fiction','american','west']` → `"fiction, american, west"`) — posting a raw list would make the tracker keep only the last tag.
- `--group-id` takes the **Publication** id (`torrents.php?action=publication&id=PID`), never the torrent group id. It is validated via `GET /upload.php?publicationid=<id>`.
