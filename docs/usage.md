# Usage

Entry point is `python -m simurg` (`simurg/__main__.py:3`). The Makefile is the recommended runner. Always run from the repo root.

## Quick start

```bash
make run DIR=./my-batch                       # real uploads (interactive)
make run DIR=./my-batch ARGS="--dry-run"     # dry run — full flow, no upload POST
# direct python:
.venv/bin/python -m simurg up ./my-batch --dry-run
.venv/bin/python -m simurg up ./mags --category magazines --dry-run
```

`make run` requires `DIR=` (`Makefile:30`). Extra flags pass through `ARGS`.

## Commands

| Command | Purpose |
|---|---|
| `python -m simurg up <dir> [options]` | Batch upload — main command |
| `python -m simurg checkconf` | Validate tracker/auth, image hosts, scrapers |
| `python -m simurg health` | Check local deps (click, requests, torf, etc.) |
| `python -m simurg --help` / `up --help` | CLI help |

## `up` options

From `up --help` (`simurg/cli.py:925-965`):

| Flag | Meaning |
|---|---|
| `--dry-run` | Full flow (staging, cover rehost, `.torrent` generation, prompts, `.cache` reuse) but skips the final upload POST. Cover prompts (manual URL, rehost preview) are skipped — dry-run never blocks on cover input |
| `--category {ebooks,magazines}` | Category (default `ebooks`). Selects scraper + payload path; magazines use `.cache` publisher-keyed reuse → `OpenAlex` (ISSN/ISSN-L) → `InternetArchive` / `LibraryOfCongress` / `Crossref` / `LibraryThing` / `WonderClub` (and `MAGAZINE_EXTENSIONS` `PDF/CBR/CBZ/DJVU`). Note: `OpenLibrary` is ebook-only |
| `--source {Retail,Scan,OCR,Convert,Other}` | Source label. Never guessed — defaults to `Other` when unset |
| `--format {...}` | Override format (ebooks: `PDF/EPUB/MOBI/AZW3/DJVU`; magazines: `PDF/CBR/CBZ/DJVU`). Overwrites scraped/file value |
| `--language {English,Japanese,Turkish}` | Override language. Overwrites scraped/file value |
| `--group-id ID` | Force upload to existing Publication `publicationid` (Publication id from `torrents.php?action=publication&id=PID`, not the torrent group id) |
| `--cover URL` | Override cover URL, top cover priority (downloaded, validated, rehosted like any other source) |
| `--url URL` | Paste a book-page URL (`openlibrary.org`, `books.google.com`/`googleapis.com`, `bookbrainz.org`, `abebooks.com`, `penguinrandomhouse.com`, `librarything.com`, `wonderclub.com`, `archive.org`, `loc.gov`, `openalex.org`/`api.openalex.org`) — routed to matching scraper, skipping auto search. Trailing title slugs accepted |
| `--no-rename` | Skip filename sanitize/staging |
| `--no-review` | Skip interactive editor metadata review |
| `--limit N` | Max files processed per run (default `50`, `0` = unlimited, constant `BATCH_LIMIT_DEFAULT` in `simurg/constants.py`). Ebooks stop the directory walk at N (walk order in, sorted within the batch) so huge dirs (4000+ files) start fast — rerun the same command for the next batch. Magazines always scan the full directory so Year/Decade packs stay complete (pack-aware cut). Note: `--no-rename` leaves files in place, so rerunning repeats the same batch |

## What `up` does per file

Each run processes up to `--limit` (default 50) files; rerun the same command for the next batch (processed files move to staging, so the next run picks up where it left off). Ebooks stop the directory walk at the cap; magazines scan everything so packs stay complete.

For each top-level file in `<directory>` (subdirectories are warned and ignored, `cli.py:687-691`):

1. **Filter and detect groups.** Allowed ebook extensions are `.pdf/.epub/.mobi/.azw3/.djvu` (`constants.py:11`). Magazines: `.pdf/.cbr/.cbz/.djvu` (`constants.py:23`). `.txt` is rejected per `rules.txt:36` (`cli.py:705-710`). Before processing, ebook files are grouped by normalized title+authors to warn about publisher packs that should be in a single torrent (`cli.py:728-774`).

2. **Decode inbuilt metadata** (`cli.py:850-859`, `_decode_inbuilt_quick`).
   - EPUB via OPF (`metadata/epub.py`), PDF via `pypdf` info, MOBI/AZW3/DJVU via `metadata/mobi.py`. Includes description, edition, illustrators/editors/translators when present. Encrypted PDFs abort (`cli.py:860-868`, `rules.txt:36`). ISBN dashes stripped and validated to 10/13 chars (`cli.py:876-879`).

3. **Query scrapers — fresh, no generic cache** (`enricher.py:11-161`, `uploader/magazine_issn.py`).
   - Ebooks: `OpenLibrary`, `GoogleBooks`, `BookBrainz`, `AbeBooks`, `PenguinRandomHouse`, `LibraryThing`, `WonderClub` (`enricher.py:56-67`).
    - Magazines: `.cache/magazine_issns.csv` publisher-keyed reuse (if seen before) → `OpenAlex` (ISSN/ISSN-L via `/sources`, needs `openalex_api_key`) → `InternetArchive` / `LibraryOfCongress` / `Crossref` / `LibraryThing` / `WonderClub` (WonderClub also handles `/magazines/*` URLs). All except `.cache` are always fresh.
   - Strategy: ISBN search first when ISBN present; if no confident hit (`_fuzzy_title >=0.8` or `_fuzzy_author >=0.8`) also search `title+author` and show all hits. 10-result-style prompt when multiple hits. For magazines `WonderClub`/`LibraryThing` correctly parse volume/issue fallback.

4. **Interactive picker** (`cli.py:408-460`, `1002-1048`).
   - Multiple hits → `[n]` pick, `[i]` inbuilt-only, `[s]` skip file, `[a]` abort all, `[u]` paste URL. Single hit auto-used. No results → retry up to 3 times: `[t]` new title+author, `[c]` freeform query (`search_custom`), `[u]` URL, `[i]`/`[s]`/`[a]` (`cli.py:298-405`).

5. **Cross-source ISBN enrichment** (`cli.py:1060-1126`, `enricher.py:255-321`).
   - Re-queries every scraper by ISBN and offers to fill gaps (publisher, year, page_count, description, cover_url, tags). Primary title/authors/year stay locked. Only prompts when gaps exist.

6. **Combine and cached + forced ISSN fill + Simurg fallback + field review** (`cli.py:1130-1500`, `metadata/combine.py`, `uploader/magazine_issn.py`, `scrapers/openalex.py`).
   - Magazines: after `build_magazine_metadata`, warm-save any existing ISSN to `.cache/magazine_issns.csv` so next same-title issue can reuse. Then exact-title cache lookup (casefold) fills missing ISSNs without network. If still missing, forced `OpenAlex /sources` (`search_magazine`, shows `GET ... search='Title' per_page=10 api_key=***`) fills the gap (never overwrites); on `no ISSN match` offers manual `https://openalex.org/S...` paste via `search_url`. If still missing after OpenAlex, searches Simurg itself (`browse` + `torrentgroup` via `magazine_issn.py:search_simurg_magazine_issns`) for existing same-title magazines, shows `print`/`electronic` ISSNs with URLs, and prompts `Use print_issn X for print? [Y/n]` / `Use electronic_issn Y for electronic? [Y/n]` or `Use it for BOTH? [y/N]` when one distinct ISSN (e.g. `1019-5009` for both). Dry-run auto-uses first Simurg candidate. ISSNs from any successful source are persisted to `.cache` for next issue.
   - Ebooks: `build_metadata` merges inbuilt + scraper choice. Source defaults to `--source` or `Other` (never claims `Retail`). Magazines: `build_magazine_metadata` parses canonical title + issue identity from filename (e.g. `National Geographic - June 2020.pdf` or `Penthouse 2002-02` without dash via `metadata/magazine.py:decode_magazine_filename`). When required fields are missing and a scraper was used, offers per-field review for fields that differ between file and scraper — title, authors, year, publisher, ISBN, description, edition, illustrators (and editors/translators when present): `[i]` file value, `[s]` scraper value, `[b]` append both (for list/text fields), or `[k]` keep merged default (`cli.py:494-611`). Dry-run keeps defaults without prompting.

7. **Editor review** (`cli.py:1550+`, `metadata/review.py`).
   - Opens `$EDITOR`/`default_editor`/`nano` for freeform revision. Shows every editable field (including `publisher`, `country`, `frequency`, `page_count` for magazines now; `page_count` editable via `pg`). Skip with `--no-review`. Salmon-style `review_metadata` flow but `album_desc` displayed as `description`.

8. **Dupe search** (`cli.py:1600+`, `uploader/dupe.py`).
   - Builds search strings from title/authors/ISBN (`generate_dupe_search_strs`), calls `check_existing_group` (Simurg `browse`/`publication` API, fuzzy 0.85 auto-select). Prompt: upload to existing Publication or create new. `--group-id` bypasses search (validated via `GET /upload.php?publicationid=<id>`). Optionally checks open requests (`uploader/requests.py`). Dry-run still prompts; tracker-less dry-run skips it.

9. **Cover handling** (`images/`, `images/validate.py`).
    - Priority: `--cover` → edited `[img]` image URL (review menu) → `cover_url_scraper` → DuckDuckGo fallback (`cover_fallback_duckduckgo`, default true) → embedded `cover_path` (ebooks only, validated). Downloads, validates (`Content-Type: image/*`, ≥5 KB, `Pillow` parse, ≥100×100 px — placeholders fail through to the next source), downscales to ~500×500, converts PNG→JPG via `Pillow`, rehosts via `ptscreens`/`imgbb`/`catbox` (`images/ptscreens.py`, `imgbb.py`, `catbox.py`). Never hotlinks source URLs.
    - Interactive guards (skipped in `--dry-run`): all sources failed → paste-a-cover-URL prompt (re-validated, `[s]` skip / `[a]` abort); after rehost → preview-confirm loop on the printed link (`[Enter]` keep, `[u]` replacement URL re-downloaded + re-hosted, `[s]` drop cover, `[a]` abort).

10. **Stage/rename** (`cli.py:1750+`, `images/base.py`).
    - Unless `--no-rename`, sanitizes blacklisted chars (`constants.py:8`, `BLACKLISTED_CHARS`) and moves to `staging_dir` (falls back `upload_directory` → `.staging`) as `{Title} - {Author} (year) [ISBN].ext` (magazines: `{Title} - {Issue label} (year).ext` via `magazine_issue_label`).

11. **Torrent + upload** (`uploader/torrent.py`, `uploader/payload.py`, `uploader/upload.py`).
    - Single-file private torrent: `source:SIM`, `private:1`, piece length 32768 (`constants.py` / `payload.py` docstring, `torrent.py`). Payload uses verified Gazelle-legacy names (see `payload.py:4-24`): `book_title`, `original_year`, `title`, `year`, `record_label`/`magazine_publisher`, `catalogue_number`/`magazine_print_issn`+`magazine_electronic_issn`, `bitrate` (Source), `book_desc`/`album_desc`/`release_desc`, `artists[]`+`importance[]`, `publicationid` (not `groupid`), `type=2` (E-Books) or `7` (Magazines). Magazines pad `magazine_issue_date` to `YYYY-MM-DD` (`_magazine_issue_date_for_payload`). Hidden `auth` + `torrent-new` posted. Dry-run writes real `.torrent` to `dottorrents_dir` (default `.torrents`, plus `tracker.simurg.dottorrents_dir` override) but skips POST. After each successful upload (never on skip/delete/fail, never after the last file, never on dry-run), rate-limits 7 s with countdown and `Ctrl+C` to skip wait (`cli.py:39-62`).

## Batch and categories

- Each directory should contain **N unrelated** files → N single-file torrents. Publisher-issued packs belong in one directory named like `Dune - Frank Herbert (2020) [EPUB Retail]/` so the pack is uploaded from that subdir, not as separate files.
- Runs are capped at `--limit` files (default 50): ebooks stop the directory walk at N — rerun the same `make run DIR=...` command for the next batch.
- ` --category ebooks` (default): formats `EPUB/PDF/MOBI/AZW3/DJVU`, source labels `Retail/Scan/OCR/Convert/Other`, payload `type=2`.
- ` --category magazines`: formats `PDF/CBR/CBZ/DJVU`, sources `Retail/Scan/OCR/Convert/Other`, payload `type=7` (`compile_data_new_magazine`/`compile_data_existing_magazine`). Requires `issue_date` or `issue_number` and volume when present (`rules.txt:130`). See `metadata/magazine.py`.

## Tips

- Always `make checkconf` after editing `config.toml` (probes `openlibrary`/`googlebooks`/`bookbrainz`/`abebooks` plus `openalex` when keyed; other scrapers report `SKIPPED`).
- Use `ARGS="--dry-run"` to rehearse prompts, cover rehost, and inspect `config.directory.dottorrents_dir/.torrent` before real uploads (dry-run still uses `.cache` and auto-applies first Simurg candidate).
- Keep `config.toml`, `.torrents/`, `.books/`, `.staging/`, `.cache/`, `*.torrent` out of git (all gitignored). `.cache/magazine_issns.csv` is safe to delete — it will be rebuilt.
