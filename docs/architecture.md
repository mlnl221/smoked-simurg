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
      wonderclub.py        # ebook + magazine, also handles /magazines/* URLs
      duckduckgo.py        # image fallback
      openalex.py          # magazine-only (ISSN via /sources, ISSN-L)
      internetarchive.py   # magazine-only
      libraryofcongress.py # magazine-only
      crossref.py          # magazine-only
      util.py              # year_from, clean_issn, normalize_issue_date, issue_label
  trackers/
    base.py            # BaseGazelleApi: auth, rate, browse, upload, torrentgroup
    simurg.py          # SimurgApi
  uploader/
    payload.py         # compile_data_* — verified Gazelle-legacy mapping + _magazine_issue_date_for_payload
    dupe.py            # generate_dupe_search_strs, check_existing_group
    magazine_issn.py   # .cache/magazine_issns.csv exact-title reuse + Simurg browse fallback (Playboy/Penthouse)
    torrent.py         # torf single-file private torrent
    upload.py
    requests.py        # check_requests
  images/
    base.py
    ptscreens.py
    imgbb.py
    catbox.py
.cache/                # gitignored — magazine_issns.csv exact-title ISSN cache
```

## Data flow

```
files in <directory>
  → _group_detection (cli.py:184)
  → _decode_inbuilt_quick (epub/pdf/mobi) → inbuilt dict
    └─ magazines: decode_magazine_filename (title + issue_date/volume/issue_number from filename)
  → search_all_scrapers / search_magazine_scrapers (enricher.py)
       ├─ search_isbn → search_title_author fallback (fresh, no generic cache)
       └─ search_by_url / search_custom on demand (pasted URLs via url_domains)
  → scraper picker (_prompt_scraper_selection / _prompt_no_results_fallback, 3-retry)
  → search_all_by_isbn + merge_fill_gaps (cross-source ISBN enrichment, primary-locked title/authors/year)
  → build_metadata / build_magazine_metadata (combine.py / magazine.py)
     └─ magazines: .cache/magazine_issns.csv exact-title warm save (so next same-title issue can skip scraping)
     └─ magazines: .cache lookup → OpenAlex /sources forced gap-fill → Simurg browse fallback
  → _prompt_field_merge + _apply_field_overrides (file vs scraper review, ebooks only; dry-run keeps defaults)
  → review_metadata (editor, skippable; magazine-editable now includes page_count)
  → generate_dupe_search_strs → check_existing_group (Simurg browse/publication) [+ check_requests]
  → validate_metadata / validate_magazine_metadata
  → cover download → downscale ~500×500, PNG→JPG (Pillow) → rehost (ptscreens/imgbb/catbox, never hotlink)
  → stage/rename to staging_dir ({Title} - {Author} (year) [ISBN].ext or magazines: {Title} - {Issue label} (year).ext, BLACKLISTED_CHARS sanitized)
  → torrent (torf, private=1, piece 32768, source:SIM) → payload compile_data_* (magazines pad issue_date to YYYY-MM-DD) → upload POST
  → rate-limit 7 s after each successful upload only (countdown, Ctrl+C to skip)
```

`--dry-run` follows the same path including prompts, staging, cover rehost, and real `.torrent` write to `dottorrents_dir`; only the final POST is skipped (`cli.py:794-820`, `README.md:100-101`). `.cache/magazine_issns.csv` is the only persistent cache (exact-title ISSN reuse); all other scrapers are always freshly queried (`enricher.py:1`).

## Key modules

### `constants.py`

`ALLOWED_EXTENSIONS`, `MAGAZINE_EXTENSIONS`, `FORMAT_MAP`/`MAGAZINE_FORMAT_MAP`, `SOURCE_LABELS`, `BLACKLISTED_CHARS`, `FORBIDDEN_TAGS`, `UPLOAD_TYPE`, symbols `OK_SYMBOL`/`FAIL_SYMBOL`/`SKIP_SYMBOL`/`INFO_SYMBOL`/`WARN_SYMBOL`, helpers `fmt_url`/`fmt_urls` (blue underlined clickable links, `constants.py:64-73`).

Torrent invariants: `private=True`, `piece_length=32768`, `source:SIM` literal (`AGENTS.md`, `uploader/torrent.py`).

### `config.py`

`_find_config`, `_load_toml` (tomllib/tomli), `_CfgSection` wrapper, `Config` with `directory`/`image`/`metadata`/`tracker`/`upload`, `get_tracker_cfg("simurg")`, global `load_config`/`get_config`/`get_config_path` (see `docs/config.md`).

### `metadata/`

- `epub.py`/`pdf.py`/`mobi.py`: file-local decode (title, authors, publisher, year, ISBN, description, edition, language, page_count, cover_path, is_encrypted). Author normalization `_normalize_authors` flips `Last, First` (`cli.py:143-181`).
- `combine.py`: `build_metadata` merges inbuilt + scraper choice, handles `title`/`remaster_title`, `year`/`remaster_year`, `edition` detection (`detect_edition`, `strip_edition_from_canonical`), tags cleanup, defaults `source→Other`, `language→English`. `validate_metadata` returns missing required fields (`cli.py:1254`).
- `magazine.py`: filename parsing (`decode_magazine_filename` handles `Title - June 2020`, `Title - Vol 12 Issue 3`, and bare `Title 2020-02` without dash), magazine scraper mapping, `build_magazine_metadata`, `validate_magazine_metadata` (needs `issue_date` or `issue_number`).
- `enricher.py:111-344`: `search_all_scrapers` (ISBN→title/author with fuzzy scores `_fuzzy_title`/`_fuzzy_author` via `SequenceMatcher`), `search_magazine_scrapers`, `search_custom`, `search_by_url` (domain dispatch via `url_domains` + `match_url`), `search_all_by_isbn` + `merge_fill_gaps` (primary-locked `title/authors/year`, scalar fill and list-merge for publisher/page_count/description/cover_url/tags, `enricher.py:228-253`). `rank_results` picks best hit. Categories filter via `categories & {"ebook","magazine"}` (`enricher.py:72-74`).
- `scrapers/*.py`: each `Scraper(session)` implements `search_isbn`, `search_title_author`, `search_magazine` (magazine-only), `search_url` + `url_domains`/`match_url` for pasted URLs, and `categories`. No generic persistent cache — always freshly queried except the magazine `.cache/magazine_issns.csv` exact-title ISSN reuse (`enricher.py:1`, `uploader/magazine_issn.py:20-140`). Magazine scrapers: `openalex` (ISSN/ISSN-L via `/sources`, needs `metadata.openalex_api_key`), `internetarchive`/`libraryofcongress`/`crossref` (ISSN/title), plus `openlibrary`/`librarything`/`wonderclub` (also `{ebook,magazine}` and handle `/magazines/*` URLs for WonderClub).

### `trackers/`

`trackers/base.py`: shared Gazelle auth, `requests.Session`, `ratelimit` (10/10), `browse`/`torrentgroup`/`torrent`/`get_redirect_torrentgroupid`/`upload` (api_key + site_page). `trackers/simurg.py`: `SimurgApi` with Simurg `base_url`, session cookie, `ajax.php?action=index` for passkey/announce derivation, tracker-specific `browse` parsing and `publicationid` validation via `upload.php` prefill (`trackers/base.py:163-190`).

### `uploader/`

- `payload.py:4-309`: verified `upload.php <form name="torrent">` mapping (checked 2026-08-25). Simurg is a Gazelle fork — POST names are Gazelle-legacy, not on-page labels. Magazine issue dates are padded to `YYYY-MM-DD` via `_magazine_issue_date_for_payload()` (`payload.py:52-78`, fixes `.failed/Penthouse*` `2002-02` rejection):

  | On-page label | POST `name` | Notes |
  |---|---|---|
  | Torrent file | `file_input` | single-file torrent |
  | Type | `type` | `2` = E-Books, `7` = Magazines |
  | Find existing Publication | `publicationid` | not `groupid` (see `payload.py:159-163`, `mistakes.md` 2026-08-25) |
  | Original author / Translator / Editor / Illustrator | `artists[]` + `importance[]` | `importance` = 1 / 3 / 4 / 6 |
  | Canonical Publication title | `book_title` | stable identity |
  | First published | `original_year` | canonical year (magazines: periodical first-published, distinct from issue `year`) |
  | Release title | `title` | includes edition wording or magazine issue label |
  | Release publication year | `year` | edition year (magazines: issue year) |
  | Tags | `tags` | comma string, lower, dots for spaces |
  | Image | `image` | rehosted cover URL |
  | Language | `language` | `English` for MVP |
  | Publisher | `record_label` (ebooks) / `magazine_publisher` (mags) | Gazelle legacy — label reads "Publisher:" |
  | ISBN (ebooks) / ISSN (mags) | `catalogue_number` (ebooks) / `magazine_print_issn` + `magazine_electronic_issn` (mags) | Gazelle legacy — label reads "ISBN:" / "Print ISSN:" & "Electronic ISSN:" |
  | Page count | `page_count` | from scraper/file; magazines now shown in review (`review.py:42`) |
  | Canonical Publication synopsis | `book_desc` | BBCode |
  | Release notes | `album_desc` | BBCode |
  | Format | `format` | `EPUB/PDF/MOBI/AZW3/DJVU` (books) / `PDF/CBR/CBZ/DJVU` (mags) |
  | Source | `bitrate` | `Retail/Scan/OCR/Convert/Other` |
  | Magazine issue date | `magazine_issue_date` (+ `magazine_issue_date_precision`) | padded to `YYYY-MM-DD` even when precision is `month`/`year` |
  | Magazine volume/issue | `magazine_volume` / `magazine_issue_number` | from filename or scraper |
  | Release description | `release_desc` | file-specific BBCode |

  Hidden/auxiliary: `auth` (session auth key), `torrent-new`, `workaround_broken_html_entities`. Authoritative mapping lives in `simurg/uploader/payload.py`; rest of pipeline uses semantic keys (`publisher`, `isbn`, `print_issn`, etc.).
- `dupe.py`: `generate_dupe_search_strs` (ISBN, title, authors variants, `EDITION_RE` stripping), `check_existing_group` interactive browse/print/prompt (fuzzy 0.85 auto-select).
- `magazine_issn.py`: `save_magazine_issn_cache`/`lookup_magazine_issn_cache`/`load_magazine_issn_cache` (`.cache/magazine_issns.csv`, headers `title,print_issn,electronic_issn,issn,issn_l,source`, exact-title `casefold`), `search_simurg_magazine_issns` (browse + torrentgroup ISSN extraction via `_extract_issns`), `prompt_simurg_issn_reuse` (print/electronic separately, `use for both?` when one distinct ISSN).
- `torrent.py`: `torf.Torrent` single-file, `private`, `piece_length=32768`, `created_by`, `source:SIM`.
- `upload.py`/`requests.py`: POST upload, request-fill check with `requestid`.

### `images/`

`base.py` downscales to ~500×500 with `Pillow`, converts PNG→JPG; `ptscreens.py`/`imgbb.py`/`catbox.py` POST the file and return the hosted URL. Failures fall back per priority in `cli.py:1287-1293`.

### `cli.py`

~2100-line orchestrator. Helpers: `_sanitize_filename`, `_rate_limit_wait` (visible countdown, `Ctrl+C` to skip, `cli.py:39-62`), `_print_inbuilt_metadata` (aligned panel, `cli.py:65-100`), `_download_url_to_temp`, `_flip_last_first`/`_normalize_authors`, `_group_detection`, `_format_scraper_result`, `_scrape_from_url`, `_prompt_no_results_fallback`, `_prompt_scraper_selection`, `_prompt_field_merge`/`_apply_field_overrides`. Main `up` handles arg validation, `ALLOWED_EXTENSIONS`/`MAGAZINE_EXTENSIONS` filtering, `.txt` rejection, `staging_dir`/`dottorrents_dir` resolution, per-file loop with encryption check, staged-filename parsing fallback, enrichment, prompts, `.cache` warm save + exact-title lookup, OpenAlex forced gap-fill + manual URL + Simurg browse fallback, editor, dupe, cover, rename, torrent, upload.

## Invariants

- Single-file torrents only, no nesting, `private:1` (DHT/PEX/LPD disabled), `source:SIM`, `piece 32768`.
- `.txt` never uploaded (`rules.txt:36`, I31). Encrypted PDFs abort.
- `Retail` never guessed — unset `source` → `Other` (`rules.txt:61`).
- Tags: lower-case, dots for spaces, `FORBIDDEN_TAGS` excluded (`constants.py:37`).
- Staging renames sanitize `BLACKLISTED_CHARS` (`constants.py:8`).
- `config.toml`, `.torrents/`, `.books/`, `*.torrent`, `.staging/`, `.cache/` are gitignored; never commit personal `announce_url` or cached ISSNs that may leak titles.
- `publicationid` is the wire field for existing Publications; never POST `groupid` (`payload.py:148-165`, `239-254`).
- Magazine ISSNs: `.cache/magazine_issns.csv` is the only persistent cache (exact-title, casefold); all other scrapers are always fresh. ISSNs are persisted after every successful source (scraper warm, OpenAlex, OpenAlex URL, Simurg) for next same-title issue.

## External references

- Tracker `upload.php` field mapping verified live 2026-08-25 (`payload.py:1-6`, `README.md:163-192`).
- `docs/rules.txt` is the source-of-truth copy of Simurg rules (precedence note `rules.txt:16`).
- Execution spec lives in `README.md` (canonical user-facing docs) — `PLAN.md` is not in this branch.
