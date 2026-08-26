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

From `simurg/cli.py:648-672` (`up --help`):

| Flag | Meaning |
|---|---|
| `--dry-run` | Full flow (staging, cover rehost, `.torrent` generation, prompts) but skips the final upload POST |
| `--category {ebooks,magazines}` | Category (default `ebooks`). Selects scraper + payload path; magazines use `IssnPortal` / `InternetArchive` / `LibraryOfCongress` / `Crossref` (and `MAGAZINE_EXTENSIONS`) |
| `--source {Retail,Scan,OCR,Convert,Other}` | Source label. Never guessed — defaults to `Other` when unset (`cli.py:1138-1140`) |
| `--group-id ID` | Force upload to existing Publication `publicationid` |
| `--cover URL` | Override cover URL (skips scraper/file cover) |
| `--url URL` | Paste a book-page URL (openlibrary/googlebooks/bookbrainz/abebooks/archive.org/loc) — routed to matching scraper, skipping auto search (`enricher.py:85-108`) |
| `--no-rename` | Skip filename sanitize/staging |
| `--no-review` | Skip interactive editor metadata review (`cli.py:1206`) |

## What `up` does per file

For each top-level file in `<directory>` (subdirectories are warned and ignored, `cli.py:687-691`):

1. **Filter and detect groups.** Allowed ebook extensions are `.pdf/.epub/.mobi/.azw3/.djvu` (`constants.py:11`). Magazines: `.pdf/.cbr/.cbz/.djvu` (`constants.py:23`). `.txt` is rejected per `rules.txt:36` (`cli.py:705-710`). Before processing, ebook files are grouped by normalized title+authors to warn about publisher packs that should be in a single torrent (`cli.py:728-774`).

2. **Decode inbuilt metadata** (`cli.py:850-859`, `_decode_inbuilt_quick`).
   - EPUB via OPF (`metadata/epub.py`), PDF via `pypdf` info, MOBI/AZW3/DJVU via `metadata/mobi.py`. Includes description, edition, illustrators/editors/translators when present. Encrypted PDFs abort (`cli.py:860-868`, `rules.txt:36`). ISBN dashes stripped and validated to 10/13 chars (`cli.py:876-879`).

3. **Query scrapers — fresh, no cache** (`enricher.py:11-161`).
   - Ebooks: `OpenLibrary`, `GoogleBooks`, `BookBrainz`, `AbeBooks`, `PenguinRandomHouse`, `LibraryThing`, `WonderClub` (`enricher.py:56-67`).
   - Magazines: `IssnPortal`, `InternetArchive`, `LibraryOfCongress`, `Crossref`.
   - Strategy: ISBN search first when ISBN present; if no confident hit (`_fuzzy_title >=0.8` or `_fuzzy_author >=0.8`) also search `title+author` and show all hits. 10-result-style prompt when multiple hits.

4. **Interactive picker** (`cli.py:408-460`, `1002-1048`).
   - Multiple hits → `[n]` pick, `[i]` inbuilt-only, `[s]` skip file, `[a]` abort all, `[u]` paste URL. Single hit auto-used. No results → retry up to 3 times: `[t]` new title+author, `[c]` freeform query (`search_custom`), `[u]` URL, `[i]`/`[s]`/`[a]` (`cli.py:298-405`).

5. **Cross-source ISBN enrichment** (`cli.py:1060-1126`, `enricher.py:255-321`).
   - Re-queries every scraper by ISBN and offers to fill gaps (publisher, year, page_count, description, cover_url, tags). Primary title/authors/year stay locked. Only prompts when gaps exist.

6. **Combine and field review** (`cli.py:1128-1140`, `metadata/combine.py`).
   - Ebooks: `build_metadata` merges inbuilt + scraper choice. Source defaults to `--source` or `Other` (never claims `Retail`). Magazines: `build_magazine_metadata` parses canonical title + issue identity from filename (e.g. `National Geographic - June 2020.pdf` via `metadata/magazine.py:decode_magazine_filename`). When required fields are missing and a scraper was used, offers per-field review for fields that differ between file and scraper — title, authors, year, publisher, ISBN, description, edition, illustrators (and editors/translators when present): `[i]` file value, `[s]` scraper value, `[b]` append both (for list/text fields), or `[k]` keep merged default (`cli.py:494-611`). Dry-run keeps defaults without prompting (`cli.py:1153-1171`).

7. **Editor review** (`cli.py:1202-1214`, `metadata/review.py`).
   - Opens `$EDITOR`/`default_editor`/`nano` for freeform revision. Skip with `--no-review`. Salmon-style `review_metadata` flow.

8. **Dupe search** (`cli.py:1219-1252`, `uploader/dupe.py`).
   - Builds search strings from title/authors/ISBN (`generate_dupe_search_strs`), calls `check_existing_group` (Simurg `browse`/`publication` API). Prompt: upload to existing Publication or create new. `--group-id` bypasses search (validated via `GET /upload.php?publicationid=<id>`). Optionally checks open requests (`uploader/requests.py`). Dry-run still prompts; tracker-less dry-run skips it.

9. **Cover handling** (`cli.py:1287-1330`, `images/`).
   - Priority: `--cover` → `cover_url_scraper` → embedded `cover_path` → DuckDuckGo fallback (`cover_fallback_duckduckgo`). Downloads, downscales to ~500×500, converts PNG→JPG via `Pillow`, rehosts via `ptscreens`/`imgbb`/`catbox` (`images/ptscreens.py`, `imgbb.py`, `catbox.py`). Never hotlinks source URLs.

10. **Stage/rename** (`cli.py:1330+`, `images/base.py`).
    - Unless `--no-rename`, sanitizes blacklisted chars (`constants.py:8`, `BLACKLISTED_CHARS`) and moves to `staging_dir` (falls back `download_directory` → `.staging`) as `{Title} - {Author} (year) [ISBN].ext`.

11. **Torrent + upload** (`uploader/torrent.py`, `uploader/payload.py`, `uploader/upload.py`).
    - Single-file private torrent: `source:SIM`, `private:1`, piece length 32768 (`constants.py` / `payload.py` docstring, `torrent.py`). Payload uses verified Gazelle-legacy names (see `payload.py:4-24`): `book_title`, `original_year`, `title`, `year`, `record_label` (Publisher), `catalogue_number` (ISBN), `bitrate` (Source), `book_desc`/`album_desc`/`release_desc`, `artists[]`+`importance[]`, `publicationid` (not `groupid`), `type=2` (E-Books) or `7` (Magazines). Hidden `auth` + `torrent-new` posted. Dry-run writes real `.torrent` to `dottorrents_dir` (default `.torrents`, plus `tracker.simurg.dottorrents_dir` override) but skips POST. Between files, rate-limits 15 s with countdown and `Ctrl+C` to skip wait (`cli.py:39-62`, `842-844`).

## Batch and categories

- Each directory should contain **N unrelated** files → N single-file torrents. Publisher-issued packs belong in one directory named like `Dune - Frank Herbert (2020) [EPUB Retail]/` so the pack is uploaded from that subdir, not as separate files.
- ` --category ebooks` (default): formats `EPUB/PDF/MOBI/AZW3/DJVU`, source labels `Retail/Scan/OCR/Convert/Other`, payload `type=2`.
- ` --category magazines`: formats `PDF/CBR/CBZ/DJVU`, sources `Retail/Scan/OCR/Convert/Other`, payload `type=7` (`compile_data_new_magazine`/`compile_data_existing_magazine`). Requires `issue_date` or `issue_number` and volume when present (`rules.txt:130`). See `metadata/magazine.py`.

## Tips

- Always `make checkconf` after editing `config.toml`.
- Use `ARGS="--dry-run"` to rehearse prompts, cover rehost, and inspect `config.directory.dottorrents_dir/.torrent` before real uploads.
- Keep `config.toml`, `.torrents/`, `.books/`, `.staging/`, `*.torrent` out of git (gitignored).
