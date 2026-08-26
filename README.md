# smoked-simurg

> **Warning: Work in progress — expect bugs.** This project is under active development. APIs, CLI flags, and the upload flow may change without notice. Test with `--dry-run` and verify payloads before real uploads.

Local-only CLI to batch-upload E-Books to [Simurg](https://simurg.world/) (a Gazelle-based e-book tracker). No global installs — everything lives in this repo and runs via a local `.venv`.

**MVP:** `python -m simurg up <directory>` (or `make run DIR=<directory>`) → N unrelated files (`.pdf/.epub/.mobi/.azw3/.djvu`) → N single-file private torrents (`source:SIM`, `private:1`, piece 32768).

## Quick start

```bash
make venv                      # or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml   # fill [tracker.simurg] session + optional announce_url / image keys
make checkconf                 # tracker/auth + image hosts + scrapers
make health                    # local deps
make run DIR=./my-batch ARGS="--dry-run"   # full dry-run: prompts, cover rehost, .torrent gen — no upload POST
make run DIR=./my-batch                    # real uploads (interactive)
```

> Run from the repo root so `python -m simurg` finds `config.toml`. `config.toml` is gitignored — never commit `session`, `announce_url`, or `*.torrent`.

## Docs

Human-readable wiki lives in `docs/`:

| Doc | What it covers |
|---|---|
| [`docs/setup.md`](docs/setup.md) | Venv, deps, `config.toml` placement, `make health`/`checkconf`, pre-commit hook, lint/format |
| [`docs/usage.md`](docs/usage.md) | `up` flow per file, all flags (`--dry-run`, `--category`, `--source`, `--group-id`, `--cover`, `--url`, `--no-rename`, `--no-review`), magazines, batch tips |
| [`docs/config.md`](docs/config.md) | Every `config.toml` section/key (`[directory]`, `[image]`, `[metadata]`, `[tracker.simurg]`, `[upload]`), secrets, image handling |
| [`docs/architecture.md`](docs/architecture.md) | Layout, data flow, modules (`metadata`, `trackers`, `uploader`, `images`), verified Gazelle-legacy payload mapping, invariants |
| [`docs/faq.md`](docs/faq.md) | Auth, encrypted PDFs, `.txt` rejection, dupes, `publicationid` vs `groupid`, covers, rate-limit, troubleshooting |
| [`docs/rules.txt`](docs/rules.txt) | Tracker rules (source-of-truth copy, takes precedence) |

## What it does (summary)

`up <directory>` loops top-level files and per file: decodes inbuilt metadata, queries scrapers fresh (ISBN → title/author), lets you pick hits and review file-vs-scraper fields, checks Simurg for dupes, rehosts the cover (downscaled ~500×500, PNG→JPG), stages/renames to `{Title} - {Author} (year) [ISBN].ext`, builds a private torrent and uploads it. Single-file torrents only; `.txt` rejected; encrypted PDFs abort.

Full steps, options, and payload details are in `docs/usage.md` and `docs/architecture.md`.
