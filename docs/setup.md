# Setup

Local-only setup. No global `pip install` — everything runs from a repo-local `.venv`. Always run from the repo root.

## Requirements

- Python 3.11+ (tested on 3.13.2). `tomllib` is stdlib from 3.11; Python <3.11 needs `tomli`.
- `ffmpeg` and `git` optional but reported by `make health`.
- Dependencies (see `requirements.txt:3-12`): `click`, `requests`, `ratelimit`, `beautifulsoup4`, `torf`, `pypdf`, `ebooklib`, `pillow`.

## Create venv and install deps

```bash
make venv
# fallback manual:
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # Linux/macOS
.venv\Scripts\pip install -r requirements.txt      # Windows
```

`make venv` tries `python3 -m venv` first, then `uv venv --seed` if `venv` is unavailable (`Makefile:40-52`). On success it upgrades `pip` and installs `requirements.txt` (prefers `uv pip` when `uv` exists, `Makefile:57-61`).

## Configure

```bash
cp config.example.toml config.toml
# edit config.toml — fill tracker session, announce_url, image keys
```

`config.toml` is gitignored (`AGENTS.md`, `.gitignore`). Never commit it, `*.torrent`, or a personal `https://tracker.simurg.world/.../announce`. `config.example.toml` stays blank and is safe to commit. The loader looks for `./config.toml`, then `simurg/../config.toml` (`simurg/config.py:9-18`).

Minimal working config for a dry run:

```toml
[tracker.simurg]
session = ""        # leave empty only for --dry-run
announce_url = ""
```

For real uploads you need at least `session` (browser cookie). See `docs/config.md` for all keys.

## Verify

```bash
make health        # local deps, python version, dottorrents dir
make checkconf     # tracker/auth + image hosts + scrapers
```

- `make health` / `python -m simurg health` checks imports and external binaries (`simurg/cli.py` health command).
- `make checkconf` / `python -m simurg checkconf` validates `session`/`announce_url`, image-host keys, and scraper reachability. Run this before any upload test (`AGENTS.md`).

## Pre-commit hook (version-controlled)

The repo stores the hook at `.githooks/pre-commit`, not `.git/hooks` (which is not versioned):

```bash
make hooks
# equivalent: git config core.hooksPath .githooks
git config --get core.hooksPath   # should print .githooks
```

Behavior (`README.md:151-153`, `.githooks/pre-commit`):
- Resolves `ruff` as `.venv/bin/ruff` → `$PATH` → `.venv/bin/python -m ruff`.
- Formats only staged `*.py` files (`git diff --cached`) and re-stages them.
- Skips gracefully if `ruff` is missing.

Alternative via `pre-commit` framework (`.pre-commit-config.yaml`):

```bash
.venv/bin/python -m pre_commit run --all-files
```

Uses `astral-sh/ruff-pre-commit v0.16.4` hooks `ruff --fix` + `ruff-format`.

## Lint and format

```bash
make lint      # ruff check + ruff format --check
make format    # ruff format + ruff check --fix
```

Style: Python 3.11+, 4-space indent, double quotes, no semicolons, PEP 8. Config in `ruff.toml` (line-length 100, target py311, double quotes, space indent).

## Clean

```bash
make clean   # removes __pycache__, .torrents, .failed, .pytest_cache; keeps .venv
```

## Tests

```bash
make test              # .venv/bin/python -m pytest -q
.venv/bin/pytest -q
```

After moving files or changing imports, run `python -m py_compile simurg/**/*.py` and `make clean`.
