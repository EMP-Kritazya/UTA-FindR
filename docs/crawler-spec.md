# UTAFindR — Crawler Build Spec

_Created 2026-10-14. Updated 2026-10-15 after discovery (see `docs/discovery-notes.md`). Related design doc: `docs/HYBRID-RETRIEVAL-RESEARCH.md`._

## 0. Goal

Build the **ingestion layer** for UTAFindR. It has two jobs:

1. **Syllabus crawler**: download every publicly available UTA **CSE** course syllabus from **Spring 2023 onward** (including Summer terms), store the raw files, and record each one in Postgres.
2. **Faculty crawler**: collect CSE faculty profiles (and later, their lab/personal sites), extract structured fields and clean main-content text, and record everything in Postgres.

**Out of scope for this task:** full PDF parsing (Docling), chunking, embeddings, and retrieval. Those are the next stages, owned by a separate program that reads from the same Postgres + `data/` directory (see §9). The only content inspection done here is a lightweight text check (§4, `page_count` / `text_chars` / `has_text`).

---

## 1. Hard Rules (do not break)

- **Public content only.** Never log in, never touch Canvas or anything behind authentication, never bypass access controls.
- **robots.txt is not enforced.** Check it and **log a warning** when a URL is disallowed, then continue. (Decision 2026-10-15: syllabi are legally required to be public and are linked from UTA's public pages; the bucket robots.txt blocks crawling, not access.)
- **Stop when asked.** If a host returns **403**, or otherwise asks us to stop, **halt all requests to that host** for the rest of the run and report it.
- **Be polite:** at most 1 request/second per host; identifying User-Agent `UTAFindR/0.1 (+<repo url>; contact: <email from env>)`; exponential backoff on 429/5xx; honor `Retry-After`.
- **Never scrape:** RateMyProfessors, Google Scholar, Reddit, UTA Libraries' licensed databases.
- **Never mirror UTA pages publicly.** Raw files are for our processing and citation evidence only.
- Secrets and contact email come from `.env`, never hard-coded.
- **Accuracy over guessing.** Never infer facts we didn't observe (e.g., never store "retired"). Every status records its source and when it was checked (§4.2).

---

## 2. Sources (decided in discovery)

| Purpose | Source |
|---|---|
| Current CSE faculty | CSE faculty directory HTML: `https://www.uta.edu/academics/schools-colleges/engineering/academics/departments/cse/faculty-directory` |
| Username / identity | `GET https://api.web.uta.edu/DM/courses/instructor?email=<prefix>@` (or `?username=`) |
| Profile content + Teaching history | `GET https://profiles.digitalmeasures.com/clients/1d35c932-8c2a-5c36-87f1-18cc1edaec2c?reportId=1cabc9a4-2d70-11ec-9e3f-1531315b955f&identifierKey=username&identifierValue=<username>` |
| Course sections (class schedule) | `GET https://api.web.uta.edu/DM/courses?year=&subject=CSE&semester=&coursenumber=` (**not** PeopleSoft) |
| CSE course numbers | Union of archived CSE catalogs (2022–23 onward) + course numbers seen on faculty profiles |
| Syllabus files | S3 `oit-ead-canvas-syllabus.s3.amazonaws.com/...` and DM signed links `www.digitalmeasures.com/login/uta/faculty/files?...&public=true` |

- Syllabus links are discovered from **both** `DM/courses` and profile Teaching groups and deduped on `(term_key, course_code, section)` + canonical URL.
- **S3 files:** conditional GET (`If-None-Match` / `If-Modified-Since`).
- **DM signed files:** treated as public. Signatures expire (~7 days) and change on every profile fetch, so **refetch the profile JSON right before downloading**. Identify these files by **decoded file path + sha256**, never by the signed URL. The canonical URL stored for them has `expire` and `sgntr` removed.

### 2.1 Course-number list + completeness check

- Query `DM/courses` once per `(year, course number)`. The API returns **at most 100 rows and has no paging**. If a response has exactly 100 rows, treat it as truncated and split by `semester`, then by `section` if needed.
- For every term, compare the sum of per-course-number rows against the broad query's `totalResult` (`year + semester + subject=CSE`). Report every mismatch. Try to recover missing course numbers from the broad query's returned rows and from instructor queries (`&instructor=<last name>`).

---

## 3. Repository Layout

(Leave `Trials/`, `engine/` and `requirements.txt` as they are.)

```
ingest/
  __init__.py
  config.py              # pydantic-settings: DB URL, data dir, UA contact, rate limit, term range
  http_client.py         # polite client: per-host rate limit, robots warning, 403 halt, retries, conditional GET
  raw_store.py           # content-addressed storage: data/raw/{sha[:2]}/{sha}.{ext} + magic-byte detection
  content_check.py       # page_count / text_chars / has_text (PyMuPDF, python-docx) — not full parsing
  db.py                  # SQLAlchemy engine + session
  models.py              # ORM tables (section 4)
  terms.py               # "Spring 2023" <-> term_key "2023-1" <-> strm "2232"; range iteration
  names.py               # professor name normalization + alias matching
  crawlers/
    __init__.py
    roster.py            # directory + DM/courses → professors, course_sections
    faculty_profile.py   # DM profile JSON → raw, extracted fields, syllabus links
    syllabus.py          # merge links, download files, record rows
    lab_sites.py         # LATER (milestone M6): shallow crawl of lab/personal sites
  extract/
    __init__.py
    profile.py           # DM profile JSON → structured fields + main text (Markdown)
    generic_html.py      # uneven pages → main content via trafilatura (M6)
  reports/
    coverage.py          # per-term coverage + status breakdowns
  cli.py                 # Typer CLI entrypoints
migrations/              # Alembic
tests/
docs/
  crawler-spec.md        # this file
  discovery-notes.md
data/                    # git-ignored
  raw/
  reports/
docker-compose.yml       # local Postgres 16
.env.example
pyproject.toml
```

Library choices: `httpx`, `selectolax`, `markdownify`, `trafilatura` (M6), `playwright` (discovery only), `sqlalchemy` 2.x, `alembic`, `psycopg` 3, `pydantic-settings`, `typer`, `tenacity`, `rapidfuzz`, `pymupdf`, `python-docx`, and `pytest` + `respx` for tests.

---

## 4. Data Model (Postgres)

Enumerations are stored as `text` + `CHECK` constraints rather than native Postgres enums, so adding a value later is a one-line migration.

```text
crawl_runs
  id, crawler (text), started_at, finished_at, status, stats (jsonb)

documents                      -- one row per unique file content
  id, url (first URL seen), final_url, sha256 (unique), mime_type, ext, size_bytes,
  source_type ('syllabus' | 'faculty_profile' | 'lab_page'),
  source_key (text, nullable)   -- e.g. decoded DM file path, or profile username
  etag, last_modified, http_status, fetched_at, crawl_run_id,
  superseded_by (fk documents.id, nullable),
  parse_status ('pending' | 'done' | 'failed') default 'pending',
  page_count (int, nullable), text_chars (int, nullable), has_text (bool, nullable)

document_urls                  -- one file can be reached from many URLs
  id, document_id, url (canonical), first_seen_at, last_seen_at,
  etag, last_modified, last_http_status, last_checked_at
  unique(document_id, url)

professors
  id, canonical_name, aliases (text[]), uta_username (unique, nullable), dm_user_id,
  email (unique, nullable), title, department, profile_url, lab_urls (text[]),
  research_interests (text[]), first_seen_term, last_seen_term, updated_at,
  employment_status, status_source (text), status_checked_at (timestamptz)

professor_profiles             -- extracted snapshot per profile document
  id, professor_id, document_id, fields (jsonb), main_text_md (text), extracted_at

course_sections                -- from DM/courses + profiles (coverage denominator)
  id, term_key, term_label, strm, course_code ('CSE 3318'), section, title, session,
  section_kind ('lecture_lab' | 'thesis' | 'dissertation' | 'independent_study' | 'research'),
  professor_id (nullable), instructor_raw, instructor_email, sources (text[]),
  syllabus_status, syllabus_status_detail (text), syllabus_checked_at (timestamptz)
  unique(term_key, course_code, section)

syllabus_links                 -- discovery queue: every link seen, downloaded or not
  id, term_key, course_code, section, url (canonical), link_type ('s3' | 'dm'),
  sources (text[]), professor_id, discovered_at, last_seen_at,
  download_status ('pending' | 'ok' | 'not_modified' | 'failed' | 'skipped'), http_status,
  document_id (nullable)
  unique(term_key, course_code, section, url)

syllabi                        -- a section/term and the file it links to
  id, term_key, term_label, course_code, section (nullable),
  professor_id (nullable), instructor_raw, document_id,
  listing_url, discovered_at
  unique(term_key, course_code, section, document_id)
```

### 4.1 Notes

- `term_key` is sortable, e.g. `2023-1` for Spring 2023 (1 = Spring, 2 = Summer, 3 = Fall). `strm` = `2` + `YY` + {2 Spring, 5 Summer, 8 Fall}. Mapping lives in `terms.py`. Range: `term_key >= 2023-1` (`strm >= 2232`).
- Raw files live on disk; the DB stores the hash, never the bytes. Path = `DATA_DIR/raw/{sha[:2]}/{sha}.{ext}`.
- **Versioning:** if the same canonical URL (or DM `source_key`) returns a new hash, insert a new `documents` row and set `superseded_by` on the old one.
- **Content check:** right after download, fill `page_count`, `text_chars`, `has_text` (PyMuPDF for PDF, python-docx for DOCX). This is NOT full parsing; it only tells us whether the file has extractable text or is empty/scanned.
- Use Alembic migrations from the start. `init-db` runs `alembic upgrade head`, **never** `Base.metadata.create_all()`.

### 4.2 Status tracking (observed facts only)

`professors.employment_status`:

| Value | Rule |
|---|---|
| `current` | present in the CSE faculty directory crawl |
| `emeritus` | ONLY if `workingTitle` or directory text contains "Emeritus" |
| `not_in_directory` | taught a CSE section Spring 2023+ but not in the current directory |
| `no_public_profile` | `DM/courses/instructor` lookup returns `[]` |

Plus `last_seen_term`, `status_source`, `status_checked_at`.

`course_sections.syllabus_status`:

| Value | Rule |
|---|---|
| `available` | downloaded, `has_text = true` |
| `available_no_text` | downloaded, `has_text = false` |
| `link_broken` | a link exists but the download failed (HTTP status in `syllabus_status_detail`) |
| `not_posted` | instructor's DM profile is reachable and lists this section, but no syllabus link from any source |
| `instructor_unreachable` | no reachable profile and no link from DM/courses |
| `not_applicable` | thesis / dissertation / independent-study / research sections |

Statuses are **recomputed on every crawl run** (a syllabus may be uploaded late).

---

## 5. Module Specs

### 5.1 `http_client.py`

- One `httpx.Client` per process with a per-host rate limit (1 req/s default, configurable).
- robots.txt: cached `urllib.robotparser` per host; a disallowed URL logs a **warning** and proceeds.
- A 403 halts that host for the rest of the run (`HostHalted`).
- `get(url, prev=None)` sends `If-None-Match` / `If-Modified-Since` when `prev` has them; returns status, content, headers, final_url.
- Retries via tenacity: 429/500/502/503/504 and timeouts, up to 4 attempts, exponential backoff, `Retry-After` honored.
- Logs every request (method, url, status, bytes, ms).

### 5.2 `raw_store.py`

- `save(content, ext) -> sha256`: write to `data/raw/{sha[:2]}/{sha}.{ext}` atomically; skip if it already exists.
- `path_for(sha, ext)` and `exists(sha, ext)`.
- Detect the real type from **magic bytes**, not the URL extension (PDF, DOCX, DOC, HTML, JSON).

### 5.3 `crawlers/roster.py`

- Directory → `professors` (`employment_status = current`).
- `DM/courses` per (year, course number), Spring 2023 → current, with truncation split + completeness check (§2.1) → `course_sections` and `syllabus_links` (source `dm_courses`).
- Resolve each instructor email → `DM/courses/instructor` → username / `no_public_profile`.
- Match on username/email first, then fuzzy names (rapidfuzz). Uncertain matches go to a review CSV and are never auto-merged.

### 5.4 `crawlers/faculty_profile.py`

- For each professor with a username, fetch the DM profile JSON → raw_store (`.json`) + `documents(source_type='faculty_profile', source_key=username)`.
- `extract/profile.py`: structured fields from item ids / headings (name, title, email, department, research interests, education, links, …) and `main_text_md` (record HTML → Markdown under headings).
- Teaching groups → `course_sections` (source `profile`) and `syllabus_links` (source `profile`).

### 5.5 `crawlers/syllabus.py`

- Inputs: `syllabus_links` rows with CSE subject and `term_key >= 2023-1`.
- S3: conditional GET → magic-byte check → `raw_store.save` → `documents` + `document_urls` → content check → `syllabi`.
- DM: refetch the owner's profile JSON for fresh signed URLs first; identity = decoded path + sha256.
- Versioning per §4.1. Non-syllabus files (images, etc.) are skipped and logged.
- Recompute `course_sections.syllabus_status` at the end of the run.

### 5.6 `crawlers/lab_sites.py` (milestone M6, later)

- For each `professors.lab_urls` entry: same host only, depth ≤ 2, ≤ 30 pages per site, HTML only.
- Main content via `extract/generic_html.py` (trafilatura) → `source_type='lab_page'`.

### 5.7 `reports/coverage.py`

Per term:

- Denominator = **lecture/lab sections only**. Thesis, dissertation, independent-study and research sections go in a separate column, excluded from coverage %.
- Columns: sections, sections with a syllabus link, unique files, files downloaded, files with `has_text = true`, `totalResult` mismatch (§2.1), and the `syllabus_status` breakdown.
- Also: professors with the most missing syllabi, and the `employment_status` breakdown.
- Note in the output: the true denominator can't be verified against PeopleSoft before Spring 2025.
- Print to the console and write `data/reports/coverage_<date>.csv`.

### 5.8 `cli.py` (Typer)

```
python -m ingest.cli init-db                      # alembic upgrade head
python -m ingest.cli roster [--limit N]
python -m ingest.cli profiles [--limit N]
python -m ingest.cli syllabi --since "Spring 2023" [--limit N]
python -m ingest.cli lab-sites [--limit N]          # M6
python -m ingest.cli coverage
python -m ingest.cli all --since "Spring 2023"
```

Every command creates a `crawl_runs` row and prints a summary (fetched, new, unchanged, failed).

---

## 6. Milestones (build in order, commit after each)

| #   | Milestone  | Done when |
| --- | ---------- | --------- |
| M0  | Discovery  | ✅ `docs/discovery-notes.md` written; strategy approved 2026-10-15 |
| M1  | Foundation | docker-compose Postgres, config, http_client, raw_store, content_check, models + Alembic, `init-db` works, unit tests pass |
| M2  | Roster     | `roster` fills `professors` + `course_sections` Spring 2023 → now; completeness check reported; name-review CSV produced |
| M3  | Profiles   | `profiles` stores raw + extracted fields for all reachable CSE professors |
| M4  | Syllabi    | `syllabi --since "Spring 2023"` downloads files; rows in `documents`, `document_urls`, `syllabi`; content check filled |
| M5  | Coverage   | `coverage` report runs; gaps written down in discovery notes |
| M6  | Lab sites  | shallow lab/personal site crawl (only after M1–M5 are solid) |

Run each stage with `--limit 5` first, inspect the output by hand, then run it in full.

---

## 7. Acceptance Criteria

- **Idempotent:** running `syllabi` twice in a row → the second run reports 0 new downloads (304s or identical hashes).
- Every stored file has exactly one `documents` row with sha256, mime_type and fetched_at, plus ≥1 `document_urls` row. Every syllabus row links to a professor or keeps `instructor_raw`.
- No request ever goes to an authenticated page, and no host is contacted after it returned 403 (verified by the request log).
- Unit tests cover: `terms.py` parsing and ordering, `raw_store` dedup, conditional-GET logic and retry behavior (respx-mocked), 403 host halt, name normalization, magic-byte type detection, and content check.
- A fresh clone can run: `docker compose up -d && python -m ingest.cli init-db && python -m ingest.cli all --since "Spring 2023"`.
- A README section explains each command and the data layout.

## 8. Final Report (after the full run)

- total distinct instructors found; how many are in the current CSE directory; how many have a reachable DM profile
- total sections (Spring 2023 →) and how many have syllabus links
- unique syllabus files after dedup; files successfully downloaded
- files with extractable text vs empty/scanned
- `totalResult` mismatches per term
- `employment_status` and `syllabus_status` breakdowns per term

## 9. Hand-off to the embedding program

The embedding/extraction program (separate, `engine/`) reads the same Postgres and `DATA_DIR`:

```sql
SELECT d.id, d.sha256, d.ext, s.course_code, s.term_label, s.section
FROM documents d JOIN syllabi s ON s.document_id = d.id
WHERE d.source_type = 'syllabus' AND d.parse_status = 'pending' AND d.superseded_by IS NULL;
```

File path = `DATA_DIR/raw/{sha[:2]}/{sha}.{ext}`. The embedding program owns `parse_status` (sets `done` / `failed`); the crawler only inserts new rows and sets `superseded_by`.

## 10. Working Style

- The repo owner is learning. In commit messages and short code comments, explain **why** (e.g., why conditional GET, why content addressing), not just what.
- Prefer small, readable modules over clever abstractions.
- When something about UTA's site is uncertain, write it in `docs/discovery-notes.md` and ask rather than guess.
