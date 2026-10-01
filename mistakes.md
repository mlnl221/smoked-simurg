# mistakes.md

This file records past failures for this project. Agents must read it before starting work and avoid repeating the same mistakes.

**Rule:** Before any task, read this file. After any mistake or user correction, append a new entry below immediately (30 seconds after fixing).

---

## Entry Format

### YYYY-MM-DD
- **What happened**:
- **Why it happened**:
- **How to avoid next time**:

---

## Past Mistakes

### 2026-08-25 (sample)
- **What happened**: Left debug `print()` statements in submission
- **Why it happened**: Forgot to remove debugging prints before commit
- **How to avoid next time**: Search for `print(` before submitting; only `logger` allowed

### 2026-08-25
- **What happened**: `--group-id=12862` uploads failed with "The selected torrent group does not exist."
- **Why it happened**: (1) `compile_data_existing_publication` sent a legacy `groupid` field alongside `publicationid`; Simurg resolves `groupid` as a *torrent group* id (a different id space from Publications), so 12862 (a Publication id) was rejected. (2) Confused Simurg's two id spaces: `torrents.php?action=publication&id=<PID>` is the Publication id; the plain `torrents.php?id=<N>` is a torrent *group* id (different number — for The Woods: PID 12862 vs group 9175). The upload form's `publicationid` field wants the **Publication** id, which the autocomplete `data` also returns.
- **How to avoid next time**: Never POST the `groupid` alias for Simurg; only `publicationid`. Validate `--group-id` by GETting `/upload.php?publicationid=<id>` and checking the hidden `book_work_id` field becomes `value="<id>"`. A valid `--group-id` is the Publication id (from `action=publication&id=`), NOT the group id on `torrents.php?id=`.

### 2026-08-25
- **What happened**: When asked to implement inside a git worktree, I edited files in the MAIN repo checkout (`/mnt/.../simurg/...`) instead of the worktree (`/mnt/.../simurg-cross-isbn/...`), then had to copy them over and `git checkout` the main repo to undo.
- **Why it happened**: I kept using the original working-directory path from the system prompt while the actual task target was the separate worktree checkout.
- **How to avoid next time**: When a worktree is created, immediately note its absolute path and use `workdir=<worktree path>` for ALL subsequent tool calls. Verify with `git -C <worktree> status` before editing, and never assume the cwd is the target branch.

### 2026-08-25
- **What happened**: Added `AbeBooksScraper` (simurg/metadata/scrapers/abebooks.py) and at first (a) pasted the rate-limited `_get` helper + a duplicate `class AbeBooksScraper` declaration outside the real class, so the method landed at module level and every call blew up with `AttributeError`; (b) naively scraped `h1`/`a[href*='/author/']` and got a title with a " - Hardcover" suffix, author noise ("Explore Michael Connelly", the book title itself), a publisher string polluted with the whole listings block, and the AbeBooks logo as "cover".
- **Why it happened**: AbeBooks has two different page shapes — `/products/isbn/<isbn>` (clean `itemprop="name"`/`itemprop="author"` meta + a `<dl class="listing-metadata">` with Publisher/Publication date/Language/Number of pages) and `/<slug>/<id>/bd` single-listing pages where `itemprop="name"` is literally "AbeBooks" and the `h1` title is truncated. Also `og:image` resolves to the site logo, and `/book-search/title/.../author/` links match a loose `/author/` selector.
- **How to avoid next time**: When adding an HTML scraper, (1) never re-declare the class — edit inside the single existing `class` block; (2) verify selectors against BOTH the ISBN page and a `/bd` listing page via a live fetch before trusting them; (3) prefer structured `itemprop`/`meta` data, strip a " - <Binding>" title suffix, reject `itemprop="name"=="AbeBooks"`, anchor authors only via `href.startswith("/author/")` while dropping "Explore …" links, take the cover from `<img src="https://pictures.abebooks.com/...">` (not `og:image`), and for `search_title_author` pull the full title from `data-test-id="listing-title"` (longest of the results) since followed listing pages truncate it.

### 2026-08-25
- **What happened**: Cover rehost URL leaked into next file's scrape URL. `url, _ = Uploader().upload_file(temp_cover)` reused the same `url` variable checked at line 858 for user-pasted scrape URLs. Never reset between loop iterations, so file N's rehosted cover URL was fed to `_scrape_from_url()` for file N+1.
- **Why it happened**: Variable name collision — `url` used for two unrelated purposes in the same loop body without resetting.
- **How to avoid next time**: Never reuse a loop-scoped variable name for an unrelated purpose without resetting it at loop top. Prefer distinct names (e.g. `rehost_url`).

### 2026-08-25
- **What happened**: While fixing ruff B007 (unused loop variable), renamed a tuple-destructure element to `_key` but the loop body still referenced `key` — would have been a runtime NameError; caught only because `ruff check` still flagged and the test suite would fail.
- **Why it happened**: Different loops over the same list use different elements; blanket-renaming destructure vars to `_x` ignored which names the body actually uses.
- **How to avoid next time**: When renaming loop-destructure variables to satisfy ruff, re-check the body for each loop separately (only the unused one gets `_`), then re-run `ruff check` before committing.

### 2026-08-26
- **What happened**: Registered `PenguinRandomHouseScraper` in `_all_scrapers()` and 2 enricher tests failed (`test_search_all_by_isbn_aggregates_hits` got 3 hits incl. a live PRH Dune response; suite also ran live network calls in other patched-scrapers tests).
- **Why it happened**: Tests in `tests/test_enricher.py` mock each registered ebook scraper by name; adding a new scraper without mocking it made tests hit the live site.
- **How to avoid next time**: When adding any scraper to `_all_scrapers()`, grep `tests/` for the existing scrapers' names and stub the new one everywhere it's enumerated (or via an autouse fixture).

### 2026-08-26
- **What happened**: Ran `python -m py_compile ...` and got `command not found: python`.
- **Why it happened**: Repo uses a local venv; no global python on PATH.
- **How to avoid next time**: Always use `.venv/bin/python` / `.venv/bin/ruff` / `.venv/bin/pytest` from repo root.

### 2026-08-26
- **What happened**: Magazine upload `Penthouse - February 2002 Vol 33 Issue 6 (2002)` failed in `.failed/` with `{"status":"failure","error":"Enter a valid issue date in YYYY-MM-DD format."}`. Payload had `magazine_issue_date: "2002-02"` and `magazine_issue_date_precision: "month"`.
- **Why it happened**: `simurg/uploader/payload.py:217` / `:258` passed `metadata["issue_date"]` straight through. Internally magazines store month-precision as `YYYY-MM` and year-precision as `YYYY` (`metadata/magazine.py`, `scrapers/util.py:normalize_issue_date`), but Simurg's `upload.php` validates strictly `YYYY-MM-DD` regardless of `magazine_issue_date_precision`. Tests in `tests/test_magazine.py:263,322` also asserted the unpadded `2020-06`.
- **How to avoid next time**: Always pad magazine `issue_date` in the payload layer to `YYYY-MM-DD` (`YYYY-MM` → `YYYY-MM-01`, `YYYY` → `YYYY-01-01`) while keeping `magazine_issue_date_precision` as the true precision. Add helper `_magazine_issue_date_for_payload()` in `payload.py` and use it in both `compile_data_new_magazine` and `compile_data_existing_magazine`.

### 2026-09-07
- **What happened**: Ran unscoped `rg` for 32-hex secrets and printed live `config.toml` values (passkey announce URL, image/scraper keys) into tool output during a secrets audit.
- **Why it happened**: Pattern matched the local gitignored `config.toml`; output not masked.
- **How to avoid next time**: Never `rg`/`cat` `config.toml` unmasked. Scope secret scans to tracked files (`git grep`) and verify `config.toml` presence only via masked key-length checks.

### 2026-09-30
- **What happened**: Scraper-cover candidate list referenced `scraper_results` at cover stage, but it is only assigned inside `if not url_used` — `--url` runs crashed with UnboundLocalError (caught by test_cli_up_url_flag_uses_pasted_url).
- **Why it happened**: Trusted agent claim "init None each iteration" without reading the init site (line 2055, inside the branch).
- **How to avoid next time**: Init loop vars next to siblings (`scraper_data = {}`); verify agent claims at the exact lines before editing.

### 2026-09-30
- **What happened**: Uploaded 5 books with blank/placeholder covers (Magnolia Summer + 4 more, e.g. Keepers/Finders/Mountain Moonlight white blank, Rules of Contact gray placeholder). Spot-checked only books 1,2,3,5,10 — all real — and declared covers verified while blanks sat at book14/18/19/22/30.
- **Why it happened**: (1) `is_valid_cover` checked only bytes (≥5KB) + dims (≥100px); the white blank is 14KB 383×500 so it passed. Google Books frontcover returns blank for unscanned books. (2) Verified rehosted URLs for reachability, never looked at image CONTENT. (3) Spot-check sample missed the bad ones.
- **How to avoid next time**: Content-check every cover: sha256 blocklist (`BLANK_COVER_SHA256`) + pixel-variance blank test (`stdev < 20`; real covers scored ≥27, placeholders 9–16). `_download_url_to_temp` rejects blanks so fallback chain runs; rehost step downloads the rehosted URL back and drops to imageless on fail. Audit 100% of saved covers with the detector, not a visual sample. Added `tests/test_validate.py`.

### 2026-09-30
- **What happened**: Auto-upload driver stalled on early-dupe prompt; pexpect TIMEOUT killed run after 3/10 uploads. Separately, uploads carried lame tags (`non.fiction`, bare `fiction`) despite rich descriptions.
- **Why it happened**: (1) Driver matched `continue / \[s\]kip file` but prompt renders `[c]ontinue / [s]kip` (`]` breaks substring). (2) `GENRE_LEXICON` lacked variants (`suspenseful`, `mysterious`, `romance`/`romantic`) and matcher used strict `\bphrase\b`, so `suspenseful`/`mysterious`/`romance with` never hit; fallback `non.fiction` stuck and merged alongside real suggestions.
- **How to avoid next time**: Match prompts on stable substrings (`skip this file now`); add morphological variants to lexicon + stem-tolerant `\w*` matching; drop `non.fiction` fallback when suggestions exist (combine.py + review.py); answer cross-enrich `y` (primary locked, fills tags).

### 2026-09-09
- **What happened**: Ebook upload `The Djinn in the Nightingale's Eye` failed with `{"status":"failure","error":"You must enter at least one tag. Maximum length is 200 characters."}` despite showing 8 tags. Payload tags string was 203 chars.
- **Why it happened**: `clean_tags` capped tag COUNT at 8 but nothing enforced the tracker 200-char TOTAL on the joined string; `_tags_for_payload` passed it through untouched. Same gap let `Collections / Anthologies` become `collections..anthologies` (slash stripped, dots never collapsed).
- **How to avoid next time**: When tracker validates a joined string field, cap the joined length at build time AND guard at the wire boundary (review edits bypass cleaning). Added `TAGS_MAX_LENGTH=200` + `fit_tags_to_limit()` (drop trailing tags, keep >=1) used by both `clean_tags` and `_tags_for_payload`; `clean_tags` also collapses `\.{2,}` and strips edge `.-`.

