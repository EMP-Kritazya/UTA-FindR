# UTAFindR — Discovery Notes (Step 1 / M0)

_Investigated 2026-10-15. All requests were unauthenticated, ≤1 req/s, UA `UTAFindR/0.1 (discovery)`.
Spec: `docs/crawler-spec.md`. **Decisions in §8 were resolved on 2026-10-15 — see the spec.**_

## TL;DR

- **Faculty profiles are JSON-backed.** Two public JSON APIs exist; no HTML parsing is needed for profiles.
- **There is a public course search API** (`api.web.uta.edu/DM/courses`) that covers Fall 2022 → now, lists the instructor for each CSE section, and includes a syllabus link where one exists.
- **BLOCKER:** almost every syllabus file lives in the S3 bucket `oit-ead-canvas-syllabus.s3.amazonaws.com`, and **that bucket's `robots.txt` is `Disallow: /`**. Under the hard rules we must not download from it without a decision from you (see §8).
- A smaller set of older, faculty-uploaded syllabi is served from `www.digitalmeasures.com/login/uta/faculty/files?...&public=true` (signed, expiring links). robots.txt allows that path, but the path contains `/login/`, so this needs your OK too.

---

## 1. robots.txt findings

| Host                                         | robots.txt      | Relevant result                                                                                                                                                           |
| -------------------------------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `www.uta.edu`                                | 200             | Disallows `/faculty/`, `/catalog/`, `/cgi-bin/`, … **`/academics/faculty/profile` is allowed** (prefix rule; verified with `urllib.robotparser`). CSE dept pages allowed. |
| `cse.uta.edu`                                | 200             | Only `Disallow: /NEUC-UTA-program.php`. Root 301-redirects to `www.uta.edu/academics/schools-colleges/engineering/academics/departments/cse`.                             |
| `api.web.uta.edu`                            | 404             | No robots → allowed.                                                                                                                                                      |
| `profiles.digitalmeasures.com`               | 404             | No robots → allowed.                                                                                                                                                      |
| `www.digitalmeasures.com`                    | 200             | Disallows `/login/dashboard/`, some demo/test paths. **`/login/uta/faculty/files` is not disallowed.**                                                                    |
| `dmfiles.uta.edu`                            | 200             | **`Disallow: /`** — only hosts profile photos as far as seen; we don't need it.                                                                                           |
| `oit-ead-canvas-syllabus.s3.amazonaws.com`   | 200             | **`Disallow: /`** — this is where the syllabi are. ⚠️                                                                                                                     |
| `catalog.uta.edu`                            | 404             | Allowed.                                                                                                                                                                  |
| `arcs-prd.utshare.utsystem.edu` (PeopleSoft) | 404             | Allowed.                                                                                                                                                                  |
| `mentis.uta.edu`                             | 400 (app error) | Treated as no robots; legacy, not needed.                                                                                                                                 |
| `ranger.uta.edu`, `crystal.uta.edu`          | 404             | Allowed (relevant only for M6 lab sites).                                                                                                                                 |

> Disclosure: before I had read the S3 robots.txt result I sent **one `HEAD`** request to one S3 syllabus URL (same command). No body was downloaded; no further requests went to that host.

---

## 2. Q1 — Faculty profile page

`https://www.uta.edu/academics/faculty/profile?username=<u>` → 301 → `?user=<email-prefix>`. The HTML is an empty shell; the page's inline JS does two fetches:

### 2a. UTA instructor lookup (identity)

```
GET https://api.web.uta.edu/DM/courses/instructor?username=<username>
GET https://api.web.uta.edu/DM/courses/instructor?email=<email-prefix>@      # what the page actually uses
```

```json
[
  {
    "userName": "athitsos",
    "instructorName": "Vassilis Athitsos",
    "email": "athitsos@uta.edu",
    "workingTitle": "Professor",
    "department": ["Computer Science and Engineering"],
    "photo": "https://dmfiles.uta.edu/athitsos/pci/vassilis-1.jpg",
    "nameProfileLink": "https://www.uta.edu/academics/faculty/profile?user=athitsos"
  }
]
```

Empty list `[]` = no profile. **Username ≠ email prefix in general** (e.g. `mcmurrough@uta.edu` → username `murrough`; `william.beksi@` → `beksiw`), so always resolve via this endpoint.

### 2b. Digital Measures (Watermark) profile content — the real data

```
GET https://profiles.digitalmeasures.com/clients/1d35c932-8c2a-5c36-87f1-18cc1edaec2c
      ?reportId=1cabc9a4-2d70-11ec-9e3f-1531315b955f
      &identifierKey=username&identifierValue=<username>
```

- Plain `curl` works (no browser, cookies or tokens needed). ~115 KB JSON for a senior professor.
- `clientId` is UTA's, `reportId` is the "normal profile" report, both hard-coded in the page JS. (A commented-out "slim" report `663776c1-7f99-11f0-8e80-15e8927a8670` also exists.)
- Shape: `{"items": [ ... ]}`, a flat ordered list. Heading items (`id` like `heading8`, `heading.value` = "Teaching") are followed by data items whose content is in `data.records[]` or `data.groups[].records[]`. Each record's `value` is an **HTML fragment**.

Trimmed:

```json
{"items":[
 {"id":"personalDetails1","data":{"records":[{"value":"<h1> Vassilis Athitsos</h1>The University of Texas at Arlington<br />Professor<br />Computer Science and Engineering<br /><a href=\"mailto:athitsos@uta.edu\">athitsos@uta.edu</a>","contentType":"text/html"}]}},
 {"id":"heading-17832","heading":{"value":"About Me","level":2}},
 {"id":"17916","data":{"records":[ ... bio ... ]}},
 {"id":"heading-118061","heading":{"value":"Education","level":2}},
 {"id":"118111","data":{"records":[ ...3 ... ]}},
 {"id":"heading8","heading":{"value":"Teaching","level":2}},
 {"id":"37285","data":{"groups":[
    {"heading":{"value":"Fall 2026","level":3},
     "records":[{"value":"CSE 6324, section 981, ADV TOPS SOFTWARE ENGINEERING.<br /><a href=\"https://oit-ead-canvas-syllabus.s3.amazonaws.com/uta.instructure.com/2026-FALL/279665-2268-CSE-6324-981/2026-FALL_2268-CSE-6324-981.docx\">Download Syllabus for CSE 6324-981</a>"}, ...]},
    ...
 ]}}
]}
```

Sections seen: personal details, About Me, Social Media, Work History, Awards, Education, Research, Publications, Connections, Teaching, Service, Presentations, Creative Activities.
Item ids are stable report-field ids (e.g. Teaching = `37285`), so the extractor can key on ids _and_ fall back to heading text.

**Recommendation:** use 2a + 2b JSON. Store the raw DM JSON as the `faculty_profile` document. `main_text_md` = record HTML fragments → Markdown, concatenated under their headings (trafilatura isn't needed for this source).

---

## 3. Q2 — Syllabus links on profiles

- Under **Teaching** (`id 37285`), grouped by **term heading** ("Fall 2026", "Summer 2026", …). Each record is one **section**: `"<SUBJ> <NUM>, section <SEC>, <TITLE>."` with an optional `<a>` "Download Syllabus for …".
- History goes back to **Fall 2020** for the sampled professor (so a `term_key >= 2022-3` filter is needed).
- Cross-listed sections can point to the same file (HONR-EN 4303-001 → the CSE 4309-001 file).
- Independent study/thesis/dissertation sections (`6399`, `6699`, `6397`, `7399`, …) never have syllabi.

Two link types:

| Type                             | Pattern                                                                                                                                                                           | Seen for                                                   | Format                                                                       |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Canvas "Upload Syllabus" (S3)    | `https://oit-ead-canvas-syllabus.s3.amazonaws.com/uta.instructure.com/<YYYY>-<SEASON>/<canvasCourseId>-<strm>-<SUBJ>-<NUM>-<SEC>/<YYYY>-<SEASON>_<strm>-<SUBJ>-<NUM>-<SEC>.<ext>` | Most sections, all terms                                   | `.pdf` and `.docx` both common                                               |
| Faculty upload in Watermark (DM) | `https://www.digitalmeasures.com/login/uta/faculty/files?file=<base64 path>&surId=<n>&public=true&ver=2&expire=<unix>&sgntr=<sig>`                                                | A few sections (sample: Fall 2022, Spring 2023, Fall 2023) | `file` decodes to e.g. `athitsos/schteach/2022_fall_cse4309_syllabus-1.docx` |

- `strm` (PeopleSoft term code) = `2` + `YY` + `{2 Spring, 5 Summer, 8 Fall}` → `2268` = Fall 2026, `2242` = Spring 2024. Useful for `terms.py`.
- S3 objects return `ETag` + `Last-Modified` (good for conditional GET), `Content-Type: application/pdf`.
- DM links **expire ~7 days** after the profile JSON is generated and the signature changes on every fetch. So: (1) always re-read the profile right before downloading, and (2) **the URL is not a stable identity**. Key DM files by decoded `file` path + content sha256, not by URL. They return `application/octet-stream` with `Content-Disposition: attachment; filename="…"`, so magic-byte detection is required.

---

## 4. Q3 — Public course & syllabus information page

**Page:** `https://www.uta.edu/academics/courses-and-schedules/courses` ("Find Your Syllabus"). It's a JS front end over a JSON API:

```
GET https://api.web.uta.edu/DM/courses?year=<YYYY>[&subject=CSE][&semester=Fall|Spring|Summer]
        [&coursenumber=<NNNN>][&section=<SSS>][&session=…][&instructor=…][&keyword=…]
```

```json
[{
  "totalResult": 273,
  "userId": "2030206",
  "courseId": "…",
  "title": "FUNDAMENT OF MACHINE LEARNING",
  "subject": "CSE", "courseNumber": "4309", "section": "001",
  "year": "2022", "semester": "Fall", "session": "Regular Academic Session",
  "instructorFirstName": "Vassilis", "instructorLastName": "Athitsos",
  "instructorName": "Vassilis Athitsos", "instructorEmail": "athitsos@uta.edu",
  "facultyProfileLink": "https://www.uta.edu/academics/faculty/profile?user=athitsos#Teaching",
  "syllabusLink": "https://oit-ead-canvas-syllabus.s3.amazonaws.com/…pdf" | "",
  "syllabusLinkTitle": "Download Syllabus" | ""
}]
```

Behaviour verified:

- **Covers Fall 2022 → Fall 2026** (and earlier).
- **Hard cap of 100 rows per response, no paging** (`page`, `pageNumber`, `pagesize` are ignored; the site UI also only shows the first 100). `totalResult` is the true count.
- `coursenumber=` **exact** match is reliable (e.g. CSE 4316 Fall 2022 → 12 sections). Partial values (`3`, `33`) return odd, incomplete subsets — don't use them.
- So the complete listing needs one query per course number, e.g. `year + subject=CSE + coursenumber` (split by `semester` if a result ever hits 100).
- **It only exposes S3 (Canvas) syllabus links.** The DM faculty-uploaded syllabi are missing: Athitsos CSE 4309-001 Fall 2022 has `syllabusLink: ""` here but a DM link on the profile. → Profiles are a superset for link discovery.

---

## 5. Q4 — CSE faculty directory

`https://www.uta.edu/academics/schools-colleges/engineering/academics/departments/cse/faculty-directory` — server-rendered HTML, **70 faculty**, one `div.cse-faculty-member` each:

```html
<div class="col-12 mb-3 cse-faculty-member">
  …
  <h2 class="h5">
    <a href="https://www.uta.edu/academics/faculty/profile?username=athitsos"
      >Vassilis Athitsos</a
    >, Professor and Director of CSE Academic Affairs
  </h2>
  <p>
    athit<span class="notthis">[NOT-THIS-PART]</span>sos@uta.edu | ERB 623 |
    Office Hrs: …
  </p>
  <h3 class="h6">Research Areas</h3>
  <ul>
    <li>Artificial Intelligence &gt; Computer Vision, …</li>
  </ul>
  <h3 class="h6">Research Interests</h3>
  <ul>
    <li>Gesture Recognition, …</li>
  </ul>
</div>
```

- Username: `profile?username=<u>` in the `<a href>`. Title: text after the `</a>,`.
- Email is **obfuscated** with `<span class="notthis">` — drop that span before reading the text. (We get clean emails from the instructor API anyway.)
- Directory names can differ from API names ("Jeff (Yu) Lei" vs "Yu Lei", "Christopher Conly" vs "Chris Conly"). → Match on **email/username first**, names only as a fallback, as the spec says.

---

## 6. Q5 — Class schedule (past terms, instructors)

- Official schedule: PeopleSoft guest search at `https://arcs-prd.utshare.utsystem.edu/psc/ARCSPRD/EMPLOYEE/PSFT_ACS/c/COMMUNITY_ACCESS.CLASS_SEARCH.GBL` (public, no login). **Its term dropdown only offers 2025 Spring → 2027 Spring**, so it can't cover Fall 2022 – Summer 2024. It's also a stateful PeopleSoft form (painful to crawl).
- **Recommendation: use the `DM/courses` API as the class schedule.** It goes back to 2022, includes instructor name + email for every section, and includes departed instructors.
- Caveat: it's built from Watermark data, so it may leave out sections Watermark doesn't know (e.g. unassigned or "Staff" sections). We could spot-check one term (Spring 2025) against PeopleSoft later if exact coverage numbers matter.
- Former instructors: their emails resolve through `DM/courses/instructor?email=` (e.g. Beksi, McMurrough), and their DM profiles still render, so profile-based syllabus discovery works for them too.
- Course numbers to iterate: the CSE catalog (`https://catalog.uta.edu/coursedescriptions/cse/`) lists **184** CSE course numbers. Retired numbers wouldn't be in it, so also add any course numbers seen on profiles.

---

## 6b. Coverage check — official schedule vs DM sources (Spring 2025)

Ran 2026-10-15. The official PeopleSoft guest search for CSE Spring 2025 returned 605 class rows (186 UG + 419 GRAD). The full search takes several minutes per career and needs Playwright. I compared it with `DM/courses` queried per catalog course number (352 rows; the API reports `totalResult` 363).

| Component                        | Official | In DM API | API row has syllabus link |
| -------------------------------- | -------- | --------- | ------------------------- |
| LEC                              | 238      | 185       | 152                       |
| LAB                              | 56       | 45        | 39                        |
| IND (directed/independent study) | 160      | 72        | 0                         |
| DTN (dissertation)               | 88       | 30        | 0                         |
| THE (thesis)                     | 63       | 10        | 0                         |

Only **LEC + LAB (294 sections)** are real syllabus-bearing sections, so they're the coverage denominator.

| LEC/LAB sections                                       | In DM API | Missing from API |
| ------------------------------------------------------ | --------- | ---------------- |
| Instructor in current CSE directory (219)              | 207       | 12               |
| Instructor not in directory, i.e. left or adjunct (75) | 23        | **52**           |

- The 12 "current faculty" gaps **do appear on their DM profiles** with syllabus links (checked Losh, Huber, Nilizadeh). → Profiles ⊇ API for current faculty.
- The 52 belong to instructors (e.g. Bakker, Carroll, Dillhoff, Weems, Rice, Meszko, Fegaras) who are absent from the DM API entirely, even when queried by name. **No public DM route reaches their sections or syllabi.**
- Where both sources list a section, the instructor matches in 229 of 230 cases.
- Estimated reachable share of teaching sections for Spring 2025: **profiles only ≈ 219 / 294 (74 %)**; **profiles + API ≈ 242 / 294 (82 %)**. Older terms are probably lower, because more of their instructors have since left.
- PeopleSoft only goes back to Spring 2025, so for older terms the true denominator can't be measured. The coverage report should say so.

---

## 7. Q7 — Rough counts (CSE, from `DM/courses`)

`total` = true section count. `syll/sample` = sections with an S3 syllabus link among the first ≤100 returned rows. That's a sample, not the full term.

| Term        | Sections  | Syllabus link / sample   |
| ----------- | --------- | ------------------------ |
| Fall 2022   | 273       | 60 / 100                 |
| Spring 2023 | 276       | 60 / 100                 |
| Summer 2023 | 81        | 55 / 81                  |
| Fall 2023   | 308       | 57 / 100                 |
| Spring 2024 | 305       | 66 / 100                 |
| Summer 2024 | 97        | 52 / 97                  |
| Fall 2024   | 346       | 66 / 100                 |
| Spring 2025 | 363       | 53 / 100                 |
| Summer 2025 | 99        | 49 / 99                  |
| Fall 2025   | 386       | 60 / 100                 |
| Spring 2026 | 371       | 61 / 100                 |
| Summer 2026 | 78        | 28 / 78                  |
| Fall 2026   | 374       | 59 / 100                 |
| **Total**   | **3,357** | **≈55–65 % of sections** |

- Many sections without a link are thesis/dissertation/research/directed-study sections that never have a syllabus. The coverage report should show these separately (or exclude them from the denominator).
- Many sections share one file (e.g. 11 sections of CSE 1106 Fall 2022). Expect roughly **1,500–2,000 syllabus links** and fewer unique files after sha256 dedup.
- All 726 syllabus links in the samples were S3 links.

Crawl size at 1 req/s: profiles ≈ 2 × ~100 people (~4 min). Course API ≈ 184 course numbers × 5 years ≈ 920 calls (~16 min).

---

## 8. ⚠️ Decisions needed before building

1. **S3 syllabus bucket is robots-disallowed.** `oit-ead-canvas-syllabus.s3.amazonaws.com/robots.txt` = `User-agent: * / Disallow: /`. This bucket holds essentially all Canvas-uploaded syllabi. The links are deliberately published on public UTA pages (state law requires public syllabi), and the robots.txt is probably meant to keep search engines from indexing the bucket. But the spec says stop and report, so I have stopped. Options:
   - **(a) Recommended:** email UTA (OIT / Provost's office, which runs the syllabus program) for written permission for a low-rate research crawl of the published syllabus links. Build M1–M3 and link discovery (`syllabi` rows with `listing_url` + file URL, no downloads) in the meantime.
   - (b) Only download the DM-signed files (allowed by robots, much smaller set). Coverage would be very low.
   - (c) You decide the S3 robots.txt doesn't apply to individually published links. That's your call to make explicitly, not mine. I won't do it by default.
2. **DM file links under `www.digitalmeasures.com/login/…`.** No login happens: the link is signed with `public=true` and returns the file directly (verified with one `HEAD`: 200, `Content-Disposition: attachment`). robots.txt allows it. OK to treat as public?
3. **Data source change vs spec:** use `DM/courses` (not PeopleSoft) as the `course_sections` source. Discover syllabus links from both `DM/courses` and DM profile Teaching groups, deduped on (term, course, section). OK?
4. **Spec path mismatch:** you asked for `docs/crawler-spec.md`; the spec is at `crawlers/CRAWLER-SPEC.md`. I wrote these notes to `docs/discovery-notes.md` as the spec says.

---

## 9. Recommended crawl strategy

1. **Roster (`roster.py`)**
   - Parse the CSE faculty directory → usernames (active = true).
   - For each term Fall 2022 → current, call `DM/courses?year&subject=CSE&coursenumber=<n>` per course number (catalog list ∪ numbers seen on profiles; split by semester if a result hits 100) → upsert `course_sections` with `instructor_raw` = name + email.
   - Resolve each distinct instructor email → `DM/courses/instructor?email=<prefix>@` → `uta_username`. People not in the directory get `active = false`.
   - Name fuzzy matching only for rows with no email; write those to the review CSV.
2. **Profiles (`faculty_profile.py`)**
   - For each username, fetch the DM profile JSON → raw_store (`.json`) + `documents(source_type='faculty_profile')`.
   - Extract fields from known item ids/headings; build `main_text_md`.
   - Parse Teaching groups → `(term_label, course_code, section, url, link_type)` → hand to the syllabus stage.
3. **Syllabi (`syllabus.py`)**
   - Merge links from profiles + `DM/courses`, then filter CSE and `term_key >= 2022-3`.
   - S3: conditional GET using `ETag`/`Last-Modified` (only if decision 1 allows).
   - DM: refetch the profile JSON first for fresh signatures. Identity = decoded file path + sha256. Skip conditional GET (no stable URL).
   - Magic-byte type check → raw_store → `documents` → `syllabi`.
4. **Coverage:** denominator = `course_sections`, excluding thesis/dissertation/research/directed-study course numbers (shown in a separate column).
5. **Not needed:** mentis.uta.edu (old profile URLs redirect to the new system), PeopleSoft, Playwright at crawl time (everything is plain JSON over httpx).
