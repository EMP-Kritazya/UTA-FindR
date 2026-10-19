# UTAFindR — Hybrid Retrieval Research & Design

_Last updated: 2026-10-14. Status: living document. Decisions marked **Accepted** are settled; **Proposed** means "default until the eval set says otherwise."_

UTAFindR is a hybrid RAG assistant for UTA CS students. It answers questions about professors, courses, syllabi, grade distributions and faculty research, and every answer is grounded in cited sources.

---

## 1. Decision Log

| #   | Decision                                                                                                             | Status   | Rationale (short)                                                                          |
| --- | -------------------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------ |
| D1  | **Docling** is the primary syllabus parser; **PyMuPDF** handles triage and fallback                                  | Accepted | Docling recovers tables, heading hierarchy and page provenance; PyMuPDF is fast for checks |
| D2  | **Separate parsing from extraction**: Docling → Markdown/JSON, then LLM extraction into a Pydantic schema → Postgres | Accepted | Syllabus facts (grading weights, exam dates) must be queryable as data, not only as text   |
| D3  | Crawler **downloads and stores raw PDFs**, content-addressed by SHA-256                                              | Accepted | Re-parse without re-crawling, dedup, change detection, citation evidence                   |
| D4  | Pipeline is **four idempotent stages**: discover → fetch → parse → index                                             | Accepted | Each stage can be re-run independently; failures are isolated                              |
| D5  | **Two stores**: Postgres (entities, structured facts) + Qdrant (chunks, dense + BM25 sparse, RRF)                    | Proposed | SQL questions and text questions need different tools                                      |
| D6  | Minimal custom Python/FastAPI orchestration; no framework in the MVP                                                 | Proposed | Understand every step; add LangGraph later if routing grows                                |
| D7  | Cross-encoder reranker (BGE-reranker-v2-m3 or Qwen3-Reranker-0.6B, local)                                            | Proposed | Large retrieval gains for small cost                                                       |
| D8  | Generation on Groq (GPT-OSS 120B; 20B for routing)                                                                   | Proposed | Cheap, fast; re-check pricing before committing                                            |
| D9  | **No RateMyProfessors ingestion** — link out only                                                                    | Accepted | RMP Terms of Use prohibit scraping and republishing                                        |
| D10 | Reddit is **optional** and only with approved API access                                                             | Accepted | Reddit requires explicit approval and bans AI-training use                                 |

---

## 2. Data Sources

| Source                           | Access method                                                         | Legal status                                               | Priority      |
| -------------------------------- | --------------------------------------------------------------------- | ---------------------------------------------------------- | ------------- |
| UTA public syllabi (CSE)         | Crawl the public syllabus listing / faculty profiles                  | Required to be public (TX HB 2504, UTA policy AA-FP-PO-11) | P0            |
| UTA catalog + class schedules    | Crawl public pages                                                    | Public                                                     | P0            |
| CSE faculty pages, lab sites     | Crawl public pages                                                    | Public; link back rather than mirror                       | P0            |
| Grade distributions              | MavGrades (ACM @ UTA) partnership, or your own public-records request | Public records                                             | P0            |
| Research output                  | OpenAlex API (free key), Semantic Scholar API                         | Open APIs                                                  | P1            |
| Reddit (r/UTArlington)           | Data API, only with explicit approval                                 | Restricted; no AI training; delete removed content         | P2 / optional |
| RateMyProfessors                 | **Link out only**                                                     | ToS prohibits scraping and republishing                    | Never ingest  |
| Google Scholar                   | None (no API)                                                         | Do not scrape                                              | Never ingest  |
| UTA Libraries licensed databases | None                                                                  | Crawling may violate vendor agreements                     | Never ingest  |

**Syllabus timing.** Under UTA policy, draft syllabi become public about 30 days before a term starts and final syllabi by the first day of class. Crawl daily during those windows and weekly otherwise.

---

## 3. Ingestion Pipeline (D3, D4)

```
discover ──► fetch ──► parse ──► index
 (URLs)     (raw PDF)  (Docling)  (chunk + extract + embed + upsert)
```

Each stage reads only the previous stage's output and is safe to re-run.

### 3.1 Discover

- **First, inspect the UTA syllabus page with browser DevTools (Network tab).** If the page loads data from a JSON endpoint, call that endpoint directly. Otherwise scrape the server-rendered HTML with Scrapy. Use Playwright only if the content requires JavaScript and no API exists.
- Output: candidate URLs with whatever metadata the listing exposes (course code, section, term, instructor).

### 3.2 Fetch

- Use polite HTTP: an identifying User-Agent with contact email, about 1 request per second, and respect for robots.txt.
- Use conditional requests (`If-None-Match` / `If-Modified-Since`). A `304 Not Modified` means skip.
- Hash the response bytes with SHA-256 and store them as `raw/{sha256}.pdf`.
- If the bytes are identical to the previous version, skip. If the same URL returns a new hash, store a new version and mark the old one `superseded_by`.
- Storage: local disk for the MVP, object storage (e.g., Cloudflare R2 or S3) once deployed. Expect low single-digit GB for several terms of CSE syllabi.

### 3.3 Parse (D1)

1. **PyMuPDF triage** (milliseconds per file): page count, PDF metadata, and text-layer check. A page with little or no extractable text is a scan and must go through OCR.
2. **Docling** (seconds per file on CPU): layout analysis, table structure, heading hierarchy, and page/bounding-box provenance per element. Output is saved to `parsed/{sha256}.json` plus Markdown.
3. **Fallback:** if Docling fails or times out, use PyMuPDF plain text and flag the document `parse_quality = degraded`.

Operational notes:

- Docling depends on PyTorch and model weights, so run it only in the ingestion worker, never in the API server.
- PyMuPDF is AGPL-licensed. That is fine for an open-source repo but should be revisited if the project ever goes closed-source.
- **Validation step before trusting any parser:** run both parsers on 20 real CSE syllabi and inspect the grading tables by hand.

### 3.4 Index: chunk, extract, embed (D2)

The same parsed syllabus produces two outputs:

**a) Retrieval chunks → Qdrant**

- Split on section headings (Grading, Attendance, Exams, Late Work, Academic Integrity), never mid-section.
- Use parent-child retrieval: retrieve small child chunks (about 200–400 tokens) and send the parent section to the LLM.
- Use contextual enrichment: prepend one LLM-generated sentence to each chunk (course, term, instructor, section), then embed _and_ BM25-index the enriched text.
- Chunk payload fields: `doc_sha256, url, source_type, course_code, professor_id, term, section_heading, page_start, page_end, fetched_at, official`.

**b) Structured facts → Postgres**

- An LLM extracts JSON matching a Pydantic schema, e.g.:

```python
class GradingComponent(BaseModel):
    name: str            # "Homework", "Midterm 1", ...
    weight_pct: float

class SyllabusFacts(BaseModel):
    course_code: str     # "CSE 3318"
    term: str            # "Fall 2026"
    instructor: str
    grading: list[GradingComponent]
    late_policy_summary: str | None
    attendance_required: bool | None
    textbook: str | None
    source_pages: list[int]
```

- Validate the result in code. Grading weights must sum to 100 (within a tolerance), and the course code must match the listing metadata. Failures go to a review queue and are not saved.
- Store `doc_sha256` on every row so SQL-backed answers can cite the source document and page.

---

## 4. Data Model (Postgres, core tables)

| Table                 | Key columns                                                                                                                                    |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `documents`           | `id, url, sha256, source_type, course_code, term, instructor_raw, etag, last_modified, fetched_at, parse_status, parse_quality, superseded_by` |
| `professors`          | `id, canonical_name, aliases[], dept, profile_url, openalex_id, rmp_url`                                                                       |
| `courses`             | `code, title, description, prereqs`                                                                                                            |
| `sections`            | `term, course_code, section, professor_id`                                                                                                     |
| `syllabus_facts`      | `doc_sha256, course_code, term, professor_id, grading jsonb, late_policy_summary, …`                                                           |
| `grade_distributions` | `term, course_code, professor_id, section, counts jsonb, enrollment`                                                                           |
| `publications`        | `openalex_id, professor_id, title, year, topics[], url`                                                                                        |

Professor-name normalization (the `aliases` field) is the single biggest data-quality task. Syllabi, schedules and grade data spell names differently.

---

## 5. Retrieval & Generation

### 5.1 Query routing

A small LLM call with structured output classifies each query:

- **structured** → text-to-SQL over **whitelisted read-only views**
- **document** → hybrid retrieval
- **both** → run both and merge

It also extracts filters: course code (regex `CSE\s?\d{4}`), professor (fuzzy match against `professors.aliases`), and term. A course-alias table rewrites nicknames (e.g., "DSA" to the correct course code).

### 5.2 Hybrid retrieval

- Qdrant collection with a dense vector plus a BM25 sparse vector (computed with IDF), fused with **Reciprocal Rank Fusion**: `score(d) = Σ 1 / (k + rank_i(d))`, with k ≈ 60.
- Apply metadata filters, retrieve the top 50, then cross-encoder rerank down to the top 8.
- Why the BM25 leg matters: exact identifiers such as "CSE 3318" or a surname are weak points for dense embeddings.
- Postgres-only alternative: use ParadeDB `pg_search` (true BM25). Plain `ts_rank_cd` lacks IDF and benchmarked worse than vectors alone.

### 5.3 Generation with citations

- Number the sources in the prompt: `[S1] title | source_type | term | date | url | pages`.
- Every factual sentence must end with `[S#]`, and the model must say so explicitly when it has no source.
- **Post-hoc verification:** drop citations to sources that weren't provided, run an entailment check (NLI or LLM) on each cited sentence, and strip or flag sentences that fail.
- **Conflict policy:** official sources (syllabus, catalog) decide facts. Student opinion is shown separately under "What students say," dated and attributed as opinion.
- **Freshness:** show the term and fetch date on every citation, and warn when the syllabus term differs from the term the user asked about.

---

## 6. Stack

| Layer            | MVP                                                                         | Later                          |
| ---------------- | --------------------------------------------------------------------------- | ------------------------------ |
| Orchestration    | Python + FastAPI (custom)                                                   | LangGraph for routing          |
| Crawl            | httpx + Scrapy / Crawl4AI; GitHub Actions cron                              | Queue workers (RQ/Celery)      |
| Parse            | PyMuPDF (triage) + Docling (primary)                                        | LlamaParse for difficult scans |
| Raw storage      | Local disk, content-addressed                                               | R2 / S3                        |
| Structured DB    | Supabase or Neon Postgres                                                   | Paid Postgres                  |
| Vector + keyword | Qdrant Cloud free tier                                                      | Paid or self-hosted Qdrant     |
| Embeddings       | Local open model (bge / nomic / gte), chosen by recall@10 on the golden set | Compare against an API model   |
| Reranker         | BGE-reranker-v2-m3 or Qwen3-Reranker-0.6B                                   | Cohere / Jina API              |
| LLM              | Groq GPT-OSS 120B (answers), 20B (routing)                                  | Add a fallback provider        |
| Frontend         | Next.js on Vercel                                                           | —                              |
| Auth             | Magic link restricted to `@mavs.uta.edu`                                    | UTA SSO only with OIT approval |
| Cache            | Exact + semantic cache (Redis/Upstash)                                      | —                              |
| Observability    | Langfuse                                                                    | —                              |
| Evaluation       | RAGAS + a custom retrieval-metrics script                                   | DeepEval in CI                 |

**Rough cost:** about 6k input + 500 output tokens per answer ≈ $0.0012 on GPT-OSS 120B at the prices cited below, or about $12 per 10k answers. Re-verify pricing, since free-tier limits changed several times in 2026.

---

## 7. Evaluation

- **Golden set:** start with 50 real questions and grow it to 150+. Tag each question by type (syllabus fact, SQL, comparison, research, opinion, unanswerable) and record the gold document IDs.
- **Retrieval metrics:** recall@10, MRR, nDCG@10, computed with your own script.
- **Generation metrics:** RAGAS faithfulness and answer relevancy, citation precision, and the abstention rate on unanswerable questions.
- **Parser-specific checks:** the share of syllabi whose grading weights pass validation, and manual spot-checks of 10 random extractions per term.
- **CI gate:** no change to retrieval or prompts merges if recall@10 or faithfulness regresses.

---

## 8. Workplan

| Phase       | Dates                 | Deliverables                                                                                                                                                                              |
| ----------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0 — Setup   | Oct 14 – Oct 26, 2026 | Reddit API request (before the reported Oct 31 cutoff); contact ACM / MavGrades; repo, schema, Qdrant collection; **parser bake-off on 20 syllabi**; 50-question golden set               |
| 1 — MVP     | Oct 27 – Nov 30, 2026 | Full ingestion pipeline (discover → fetch → parse → index); hybrid retrieval + reranker; citations; professor/course cards; closed ACM beta during Spring 2027 registration (opens Nov 2) |
| 2 — Quality | Dec 2026 – Feb 2027   | Contextual retrieval, router + text-to-SQL, conflict and freshness handling; ingest Spring 2027 syllabi as they appear (a real test of incremental re-ingestion); eval in CI              |
| 3 — Launch  | Mar – Apr 2027        | Soft launch mid-March; major push before Summer/Fall registration opens Apr 5, 2027                                                                                                       |
| 4 — Handoff | May 2027 →            | Move the repo to the ACM @ UTA org, write runbooks, recruit maintainers                                                                                                                   |

---

## 9. Ethics & Risk

- **Professors:** present grade data as data, with sample sizes and terms. The LLM never characterizes a professor on its own authority. Faculty get a correction channel.
- **Grade privacy:** suppress very small sections. Never accept transcript uploads.
- **Reddit (if used):** store no usernames, show short excerpts with links, delete removed content within 48 hours, and never train on the data.
- **Users:** pseudonymous query logs and a plain-language privacy page.
- **UTA:** link to official pages rather than mirroring them, don't use logos in a way that implies endorsement, and engage the CSE department early.

| Risk                                 | Mitigation                                                            |
| ------------------------------------ | --------------------------------------------------------------------- |
| ToS / legal                          | No RMP ingestion; Reddit only with approval                           |
| Reddit API closure                   | Treat Reddit as optional                                              |
| Bad parses / wrong extracted weights | Docling + validation + review queue + spot checks                     |
| Inconsistent professor names         | Alias table and fuzzy matching with manual review                     |
| Hallucination                        | Strict citations, verification, abstention, CI eval gate              |
| Free-tier limits                     | Keep-alive pings; budget $10–30/month                                 |
| Adoption                             | Integrate with MavGrades; launch through ACM; time it to registration |
| Bus factor                           | ACM handoff before May 2027                                           |

---

## 10. Open Questions

- [ ] How is UTA's public syllabus listing served (JSON API vs. HTML)? → DevTools inspection
- [ ] Docling vs. PyMuPDF output quality on real CSE syllabi → bake-off results
- [ ] MavGrades data-sharing terms
- [ ] Reddit API approval outcome
- [ ] Embedding model choice → recall@10 on the golden set

---

## Sources

- UTA syllabus policy and resources: https://www.uta.edu/administration/provost/policies-and-resources/syllabus-resources
- UTA course policies for faculty (PDF): https://resources.uta.edu/provost/_downloads/course-related-pdfs/course-policies-faculty.pdf
- MavGrades (ACM @ UTA): https://github.com/acmuta/mavgrades
- RateMyProfessors Terms of Use: https://www.ratemyprofessors.com/terms-of-use
- Reddit Responsible Builder Policy: https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy
- Reddit Data API Wiki: https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki
- Reddit API timeline (secondary report, unverified): https://ppc.land/reddit-to-halt-public-data-api-access-in-march-2027-as-14-000-apps-register/
- Anthropic, Contextual Retrieval: https://www.anthropic.com/engineering/contextual-retrieval
- Qdrant hybrid search docs: https://qdrant.tech/documentation/search/text-search/hybrid-search/
- Qdrant hybrid search article: https://qdrant.tech/articles/hybrid-search/
- ParadeDB, Hybrid Search in PostgreSQL: https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual
- BM25 vs ts_rank_cd benchmark: https://github.com/wangweidi666-create/hybrid-retrieval-pg
- PDF parser benchmark (Docling / Unstructured / LlamaParse): https://procycons.com/en/blogs/pdf-data-extraction-benchmark/
- Reranker benchmark: https://hevmind.com/writing/jev-as-a-reranker/
- OpenAlex authentication: https://developers.openalex.org/api-reference/authentication
- UTA Libraries, electronic resources policy: https://libraries.uta.edu/policies/accessing-electronic-resources
- UTA website standards: https://resources.uta.edu/advancement/identity/web/
- Groq pricing (2026): https://markaicode.com/pricing/groq-pricing/
- UTA Spring 2027 academic calendar: https://www.uta.edu/academics/academic-calendar/spring-2027
