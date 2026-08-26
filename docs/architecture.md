# Architecture

MVP layout per `AGENTS.md` (no `salmon/web`, `converter`, `tagger`, extra commands). Entry point `python -m simurg` → `simurg/__main__.py:3` → `simurg/cli.py:cli`.

## Layout

```
simurg/
  __main__.py
  cli.py               # up + checkconf + health
  constants.py
  config.py
  errors.py
  __init__.py          # __version__
  metadata/
    epub.py            # OPF decode
    pdf.py             # pypdf info / is_encrypted
    mobi.py            # MOBI/AZW3 EXTH + djvu stub
    pagecount.py
    magazine.py        # decode_magazine_filename, build_magazine_metadata
    combine.py         # build_metadata, validate_metadata, edition helpers
    enricher.py        # search_all_scrapers, search_by_url, search_custom, merge_fill_gaps
    review.py          # review_metadata via click.edit
    scrapers/
      base.py
      openlibrary.py
      googlebooks.py
      bookbrainz.py
      abebooks.py
      penguinrandomhouse.py
      librarything.py
      wonderclub.py
      duckduckgo.py        # image fallback
      openalex.py          # magazine-only (ISSN via /sources)
      internetarchive.py   # magazine-only
      libraryofcongress.py # magazine-only
      crossref.py          # magazine-only
      util.py              # year_from, etc.
  trackers/
    base.py            # BaseGazelleApi: auth, rate, browse, upload
    simurg.py          # SimurgApi
  uploader/
    payload.py         # compile_data_* — verified Gazelle-legacy mapping
    dupe.py            # generate_dupe_search_strs, check_existing_group
    torrent.py         # torf single-file private torrent
    upload.py
    requests.py        # check_requests
  images/
    base.py
    ptscreens.py
    imgbb.py
    catbox.py
```

## Data flow

```
files in <directory>
  → _group_detection (cli.py:184)
  → _decode_inbuilt_quick (epub/pdf/mobi) → inbuilt dict
  → search_all_scrapers / search_magazine_scrapers (enricher.py)
       ├─ search_isbn → search_title_author fallback (fresh, no cache)
       └─ search_by_url / search_custom on demand
  → scraper picker (_prompt_scraper_selection / _prompt_no_results_fallback)
  → search_all_by_isbn + merge_fill_gaps (cross-source ISBN enrichment)
  → build_metadata / build_magazine_metadata (combine.py / magazine.py)
  → _prompt_field_merge + _apply_field_overrides (file vs scraper review, ebooks only)
  → review_metadata (editor, skippable)
  → generate_dupe_search_strs → check_existing_group (Simurg browse/publication) [+ check_requests]
  → validate_metadata / validate_magazine_metadata
  → cover download → downscale ~500×500, PNG→JPG (Pillow) → rehost (ptscreens/imgbb/catbox)
  → stage/rename to staging_dir ({Title} - {Author} (year) [ISBN].ext, BLACKLISTED_CHARS sanitized)
  → torrent (torf, private=1, piece 32768, source:SIM) → payload compile_data_* → upload POST
  → rate-limit 15 s between files
```

`--dry-run` follows the same path including prompts, staging, cover rehost, and real `.torrent` write to `dottorrents_dir`; only the final POST is skipped (`cli.py:794-820`, `README.md:100-101`).

## Key modules

### `constants.py`

`ALLOWED_EXTENSIONS`, `MAGAZINE_EXTENSIONS`, `FORMAT_MAP`/`MAGAZINE_FORMAT_MAP`, `SOURCE_LABELS`, `BLACKLISTED_CHARS`, `FORBIDDEN_TAGS`, `UPLOAD_TYPE`, symbols `OK_SYMBOL`/`FAIL_SYMBOL`/`SKIP_SYMBOL`/`INFO_SYMBOL`/`WARN_SYMBOL`, helpers `fmt_url`/`fmt_urls` (blue underlined clickable links, `constants.py:64-73`).

Torrent invariants: `private=True`, `piece_length=32768`, `source:SIM` literal (`AGENTS.md`, `uploader/torrent.py`).

### `config.py`

`_find_config`, `_load_toml` (tomllib/tomli), `_CfgSection` wrapper, `Config` with `directory`/`image`/`metadata`/`tracker`/`upload`, `get_tracker_cfg("simurg")`, global `load_config`/`get_config`/`get_config_path` (see `docs/config.md`).

### `metadata/`

- `epub.py`/`pdf.py`/`mobi.py`: file-local decode (title, authors, publisher, year, ISBN, description, edition, language, page_count, cover_path, is_encrypted). Author normalization `_normalize_authors` flips `Last, First` (`cli.py:143-181`).
- `combine.py`: `build_metadata` merges inbuilt + scraper choice, handles `title`/`remaster_title`, `year`/`remaster_year`, `edition` detection (`detect_edition`, `strip_edition_from_canonical`), tags cleanup, defaults `source→Other`, `language→English`. `validate_metadata` returns missing required fields (`cli.py:1254`).
- `magazine.py`: filename parsing (`decode_magazine_filename`), magazine scraper mapping, `build_magazine_metadata`, `validate_magazine_metadata` (needs `issue_date` or `issue_number`).
- `enricher.py:111-344`: `search_all_scrapers` (ISBN→title/author with fuzzy scores `_fuzzy_title`/`_fuzzy_author` via `SequenceMatcher`), `search_magazine_scrapers`, `search_custom`, `search_by_url` (domain dispatch via `url_domains` + `match_url`), `search_all_by_isbn` + `merge_fill_gaps` (primary-locked `title/authors/year`, scalar fill and list-merge for publisher/page_count/description/cover_url/tags, `enricher.py:228-253`). `rank_results` picks best hit. Categories filter via `categories & {"ebook","magazine"}` (`enricher.py:72-74`).
- `scrapers/*.py`: each `Scraper(session)` implements `search_isbn`, `search_title_author`, `search_magazine` (magazine-only), `search_url` + `url_domains`/`match_url` for pasted URLs, and `categories`. No persistent cache — always freshly queried (per manual smoke test requirement).

### `trackers/`

`trackers/base.py`: shared Gazelle auth, `requests.Session`, `ratelimit`, browse/publication/search endpoints. `trackers/simurg.py`: `SimurgApi` with Simurg `base_url`, session cookie, `ajax.php?action=index` for passkey/announce derivation, tracker-specific `browse` parsing.

### `uploader/`

- `payload.py:4-309`: verified `upload.php <form name="torrent">` mapping (checked 2026-08-25). Simurg is a Gazelle fork — POST names are Gazelle-legacy, not on-page labels:

  | On-page label | POST `name` | Notes |
  |---|---|---|
  | Torrent file | `file_input` | single-file torrent |
  | Type | `type` | `2` = E-Books, `7` = Magazines |
  | Find existing Publication | `publicationid` | not `groupid` (see `payload.py:159-163`, `mistakes.md` 2026-08-25) |
  | Original author / Translator / Editor / Illustrator | `artists[]` + `importance[]` | `importance` = 1 / 3 / 4 / 6 |
  | Canonical Publication title | `book_title` | stable identity |
  | First published | `original_year` | canonical year |
  | Release title | `title` | includes edition wording |
  | Release publication year | `year` | edition year |
  | Tags | `tags` | comma string, lower, dots for spaces |
  | Image | `image` | rehosted cover URL |
  | Language | `language` | `English` for MVP |
  | Publisher | `record_label` | Gazelle legacy — label reads "Publisher:" |
  | ISBN | `catalogue_number` | Gazelle legacy — label reads "ISBN:" |
  | Page count | `page_count` | from scraper only |
  | Canonical Publication synopsis | `book_desc` | BBCode |
  | Release notes | `album_desc` | BBCode |
  | Format | `format` | `EPUB/PDF/MOBI/AZW3/DJVU` (books) / `PDF/CBR/CBZ/DJVU` (mags) |
  | Source | `bitrate` | `Retail/Scan/OCR/Convert/Other` |
  | Release description | `release_desc` | file-specific BBCode |

  Hidden/auxiliary: `auth` (session auth key), `torrent-new`, `workaround_broken_html_entities`. Authoritative mapping lives in `simurg/uploader/payload.py`; rest of pipeline uses semantic keys (`publisher`, `isbn`, etc.).
- `dupe.py`: `generate_dupe_search_strs` (ISBN, title, authors variants, `EDITION_RE` stripping), `check_existing_group` interactive browse/print/prompt.
- `torrent.py`: `torf.Torrent` single-file, `private`, `piece_length=32768`, `created_by`, `source:SIM`.
- `upload.py`/`requests.py`: POST upload, request-fill check with `requestid`.

### `images/`

`base.py` downscales to ~500×500 with `Pillow`, converts PNG→JPG; `ptscreens.py`/`imgbb.py`/`catbox.py` POST the file and return the hosted URL. Failures fall back per priority in `cli.py:1287-1293`.

### `cli.py`

~1400-line orchestrator. Helpers: `_sanitize_filename`, `_rate_limit_wait` (visible countdown, `Ctrl+C` to skip, `cli.py:39-62`), `_print_inbuilt_metadata` (aligned panel, `cli.py:65-100`), `_download_url_to_temp`, `_flip_last_first`/`_normalize_authors`, `_group_detection`, `_format_scraper_result`, `_scrape_from_url`, `_prompt_no_results_fallback`, `_prompt_scraper_selection`, `_prompt_field_merge`/`_apply_field_overrides`. Main `up` handles arg validation, `ALLOWED_EXTENSIONS`/`MAGAZINE_EXTENSIONS` filtering, `.txt` rejection, `staging_dir`/`dottorrents_dir` resolution, per-file loop with encryption check, staged-filename parsing fallback, enrichment, prompts, editor, dupe, cover, rename, torrent, upload.

## Invariants

- Single-file torrents only, no nesting, `private:1` (DHT/PEX/LPD disabled), `source:SIM`, `piece 32768`.
- `.txt` never uploaded (`rules.txt:36`, I31). Encrypted PDFs abort.
- `Retail` never guessed — unset `source` → `Other` (`rules.txt:61`).
- Tags: lower-case, dots for spaces, `FORBIDDEN_TAGS` excluded (`constants.py:37`).
- Staging renames sanitize `BLACKLISTED_CHARS` (`constants.py:8`).
- `config.toml`, `.torrents/`, `.books/`, `*.torrent`, `.staging/` are gitignored; never commit personal `announce_url`.
- `publicationid` is the wire field for existing Publications; never POST `groupid` (`payload.py:148-165`, `239-254`).

## External references

- Tracker `upload.php` field mapping verified live 2026-08-25 (`payload.py:1-6`, `README.md:163-192`).
- `docs/rules.txt` is the source-of-truth copy of Simurg rules (precedence note `rules.txt:16`).
- Execution spec lives in `README.md` (canonical user-facing docs) — `PLAN.md` is not in this branch.
