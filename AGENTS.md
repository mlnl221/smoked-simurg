# AGENTS.md

> **Before starting ANY task, read `mistakes.md` in the repo root. After any mistake or user correction, append an entry to `mistakes.md` immediately using the format inside it. Never repeat a recorded mistake.**

## Project Overview
`smoked-simurg` — local-only CLI to batch-upload E-Books to `https://simurg.world` (Gazelle). MVP: `python -m simurg up <directory>` loops N unrelated files (`.pdf/.epub/.mobi/.azw3/.djvu`) → N single-file private torrents (`source:SIM`, `private:1`, `piece 32768`). No global `pip install`; everything stays in repo. See `PLAN.md` for full spec, `rules.txt` for tracker rules, `example downlaoded torrent.txt` for torrent template.

## Setup Commands
- Create venv: `make venv` or `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Requirements are minimal: `click`, `requests`, `ratelimit`, `beautifulsoup4`, `torf`, `pypdf`, `ebooklib`, `pillow` — see `PLAN.md §6`
- Config: copy `config.example.toml` → `config.toml` (gitignored, holds `session`/`announce_url` — never commit personal `https://tracker.simurg.world/.../announce`). Run from repo root.
- Run upload: `make run DIR=/path/to/batch` or `python -m simurg up /path/to/batch` — add `ARGS="--dry-run"` to forward flags
- Check tracker/auth + image hosts + scrapers: `make checkconf` / `python -m simurg checkconf`
- Health (deps): `make health` / `python -m simurg health`
- Clean caches: `make clean`

## Code Style (Python)
- Python 3.11+, strict typing where practical (`mypy` optional, no required strict mode for MVP)
- Formatter: `ruff` / `black` if added — otherwise PEP 8, 4-space indent, double quotes, no semicolons
- Use `pathlib` and `click` for CLI; `requests` + `ratelimit` for Gazelle API; `beautifulsoup4` only for HTML fallback
- Minimal vendoring: only stripped copies of `salmon/trackers/base.py` (auth/rate/upload), `dupe_checker.py` (browse/print/prompt), `upload.py` (payload/torrent) — see `PLAN.md §9`. Do not add `salmon/web`, `converter`, `tagger`, extra CLI commands
- File layout (MVP only): `simurg/__main__.py`, `cli.py` (only `up` + `checkconf`), `constants.py`, `config.py`, `metadata/{epub,pdf,mobi,combine,enricher}`, `metadata/scrapers/{openlibrary,googlebooks,bookbrainz,abebooks,duckduckgo}`, `trackers/{base,simurg}`, `uploader/{payload,dupe,torrent,upload,requests}`, `images/{base,ptscreens,imgbb,catbox}` — do not create extra paths
- Keep `source:SIM` literal (from `example downlaoded torrent.txt:10`) for all torrents; `private=True`, `piece_length=32768`
- Handle sensitive `announce_url` via local `config.toml` or env; never hardcode or log it

## Testing Instructions
- Tests: `pytest` from repo root, `python -m pytest -q` or `make test`
- Manual smoke test: `make run DIR=./test-batch ARGS="--dry-run"` — verifies per-file metadata decode, fresh scraper lookups (no cache), title→ISBN browse fallback, 10-result limit, cover downscale/jpg + rehost
- Verify auth: `make checkconf` must succeed before any upload test
- After moving files or changing imports: `python -m py_compile simurg/**/*.py` and `make clean` to clear caches

## Build & Run Conventions
- Always run from repo root; `python -m simurg` is the entrypoint (`simurg/__main__.py`) — do not add global scripts
- `Makefile` is the only runner: `DIR=` is required for `run`; `ARGS` forwards flags (`--dry-run`, `--no-rename`, `--group-id`, `--cover`)
- Do not introduce `pyproject.toml`/`pip install -e .`/`uv tool` for MVP
- Keep `config.toml` gitignored; commit only `config.example.toml`. `.torrents/`, `.books/`, `*.torrent`, `__pycache__/` are ignored

## Security & Tracker Rules
- Never commit `config.toml`, `*.torrent`, or personal announce URL
- Respect `rules.txt`: single-file torrents (no nesting), reject `.txt` (no transcode), abort on `isEncrypted` PDF, `Retail/Scan/OCR/Convert/Other` source labels, tags without `epub/pdf/retail`, DHT/PEX/LPD disabled (`private:1`)
- Cover handling: downscale to ~500×500, convert PNG→JPG, upload via `ptscreens`/`imgbb`/`catbox`; fallback to DuckDuckGo images only if scraper/file cover missing

## Mistakes Log
- Every agent must read `mistakes.md` before starting work (see header). It separates historical failures from base rules in this file.
- After any error, failed tool call, or user correction, append to `mistakes.md` within 30 seconds using its `What happened / Why / How to avoid` format. Keep entries messy and chronological — do not try to be perfect.
- Monthly: review `mistakes.md` and prune fixed items.

## PR Instructions
- Title format: `[simurg] <Title>`
- Keep changes minimal and MVP-scoped; update `PLAN.md` if flow changes
- Run `make health` and `make checkconf` (with valid session) before committing
