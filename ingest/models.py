from datetime import datetime
from sqlalchemy import DateTime, Text, func, ForeignKey, CheckConstraint, Boolean, Integer, UniqueConstraint, BigInteger
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
  pass

class CrawlRun(Base):
  __tablename__ = "crawl_runs"

  id: Mapped[int] = mapped_column(primary_key=True)
  crawler: Mapped[str] = mapped_column(Text)
  started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
  status: Mapped[str] = mapped_column(Text, default = 'running')
  stats: Mapped[dict] = mapped_column(JSONB, default=dict)


class Document(Base):
  __tablename__ = "documents"
  __table_args__ = (
    CheckConstraint("parse_status IN ('pending', 'done', 'failed')", name = "ck_documents_parse_status"),
    CheckConstraint("source_type IN ('syllabus', 'faculty_profile', 'lab_page')", name="ck_documents_source_type")
  )

  id: Mapped[int] = mapped_column(primary_key=True)
  url: Mapped[str] = mapped_column(Text, nullable=False)
  final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
  sha256: Mapped[str] = mapped_column(Text, unique=True)
  mime_type: Mapped[str] = mapped_column(Text, nullable=False)
  ext: Mapped[str] = mapped_column(Text, nullable=False)
  size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
  source_type: Mapped[str] = mapped_column(Text, nullable=False)
  source_key: Mapped[str | None] = mapped_column(Text, nullable=True)
  etag: Mapped[str | None] = mapped_column(Text, nullable=True)
  last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
  http_status: Mapped[int] = mapped_column(Integer, nullable=False)
  fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  crawl_run_id: Mapped[int | None] = mapped_column(
    ForeignKey("crawl_runs.id"),
    nullable=True
  )
  superseded_by: Mapped[int | None] = mapped_column(
    ForeignKey("documents.id"),
    nullable=True
  )
  parse_status: Mapped[str] = mapped_column(Text, server_default="pending", index=True)
  page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
  text_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
  has_text: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class DocumentUrl(Base):
  # One file, many URLs. Example: the same CSE 4309 syllabus reached from the
  # DM/courses API row AND from a cross-listed HONR-EN section on a profile.
  # documents.sha256 stays unique; this table records every place we saw it.
  __tablename__ = "document_urls"
  __table_args__ = (
    UniqueConstraint("document_id", "url", name="uq_document_urls_doc_url"),
  )

  id: Mapped[int] = mapped_column(primary_key=True)
  document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
  # Canonical URL: for DM signed links the expiring `expire` and `sgntr`
  # params are stripped, otherwise every crawl would look like a new URL.
  url: Mapped[str] = mapped_column(Text, index=True)
  first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  # Validators live per URL because conditional GET is asked per URL.
  etag: Mapped[str | None] = mapped_column(Text)
  last_modified: Mapped[str | None] = mapped_column(Text)
  last_http_status: Mapped[int | None] = mapped_column(Integer)
  last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Professor(Base):
  __tablename__ = "professors"
  __table_args__ = (
    CheckConstraint(
      "employment_status IN ('current', 'emeritus', 'not_in_directory', 'no_public_profile')",
      name="ck_professors_employment_status"),
  )

  id: Mapped[int] = mapped_column(primary_key=True)
  canonical_name: Mapped[str] = mapped_column(Text)
  # Other spellings seen ("Jeff (Yu) Lei", "Yu Lei"), so name matching can use them.
  aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
  # Both nullable + unique: Postgres allows many NULLs in a unique column,
  # so instructors without a username/email are kept, but no duplicates.
  uta_username: Mapped[str | None] = mapped_column(Text, unique=True)
  email: Mapped[str | None] = mapped_column(Text, unique=True)
  dm_user_id: Mapped[str | None] = mapped_column(Text)  # "userId" from DM/courses
  title: Mapped[str | None] = mapped_column(Text)
  department: Mapped[str | None] = mapped_column(Text)
  profile_url: Mapped[str | None] = mapped_column(Text)
  lab_urls: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
  research_interests: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
  first_seen_term: Mapped[str | None] = mapped_column(Text)  # term_key, e.g. "2023-1"
  last_seen_term: Mapped[str | None] = mapped_column(Text)
  updated_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
  # Observed facts only (spec §4.2): never "retired", always with source + time.
  employment_status: Mapped[str | None] = mapped_column(Text)
  status_source: Mapped[str | None] = mapped_column(Text)
  status_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProfessorProfile(Base):
  # One extracted snapshot per downloaded profile JSON. Kept per document (not
  # overwritten) so we can see how a profile changed between crawls.
  __tablename__ = "professor_profiles"

  id: Mapped[int] = mapped_column(primary_key=True)
  professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id"), index=True)
  document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
  # Structured fields (education, awards, publications, ...). JSONB because each
  # profile has a different mix of sections, and Postgres can still query inside it.
  fields: Mapped[dict] = mapped_column(JSONB)
  # The whole profile as Markdown: what your embedding program will chunk.
  main_text_md: Mapped[str] = mapped_column(Text)
  extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseSection(Base):
  # Every CSE section we know about: the coverage denominator.
  __tablename__ = "course_sections"
  __table_args__ = (
    # A section exists once per term, whichever source(s) told us about it.
    UniqueConstraint("term_key", "course_code", "section", name="uq_course_sections_term_course_section"),
    CheckConstraint(
      "section_kind IN ('lecture_lab', 'thesis', 'dissertation', 'independent_study', 'research')",
      name="ck_course_sections_section_kind"),
    CheckConstraint(
      "syllabus_status IN ('available', 'available_no_text', 'link_broken', 'not_posted', "
      "'instructor_unreachable', 'not_applicable')",
      name="ck_course_sections_syllabus_status"),
  )

  id: Mapped[int] = mapped_column(primary_key=True)
  term_key: Mapped[str] = mapped_column(Text, index=True)  # "2023-1"
  term_label: Mapped[str] = mapped_column(Text)  # "Spring 2023"
  strm: Mapped[str] = mapped_column(Text)  # "2232"
  course_code: Mapped[str] = mapped_column(Text)  # "CSE 3318"
  section: Mapped[str] = mapped_column(Text)  # "001"
  title: Mapped[str | None] = mapped_column(Text)
  session: Mapped[str | None] = mapped_column(Text)
  # Lets the coverage report exclude thesis/research sections from the denominator.
  section_kind: Mapped[str] = mapped_column(Text)
  professor_id: Mapped[int | None] = mapped_column(ForeignKey("professors.id"), index=True)
  # Kept even when matched, so a wrong match can be spotted and fixed later.
  instructor_raw: Mapped[str | None] = mapped_column(Text)
  instructor_email: Mapped[str | None] = mapped_column(Text)
  sources: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")  # 'dm_courses', 'profile'
  # Recomputed on every crawl run (a syllabus can be uploaded late).
  syllabus_status: Mapped[str | None] = mapped_column(Text)
  syllabus_status_detail: Mapped[str | None] = mapped_column(Text)  # e.g. "HTTP 404"
  syllabus_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyllabusLink(Base):
  # The "to-do list": every syllabus link discovered, downloaded or not.
  __tablename__ = "syllabus_links"
  __table_args__ = (
    # Dedup across sources: the same link found via DM/courses AND a profile
    # is one row, with both names in `sources`.
    UniqueConstraint("term_key", "course_code", "section", "url", name="uq_syllabus_links_section_url"),
    CheckConstraint("link_type IN ('s3', 'dm')", name="ck_syllabus_links_link_type"),
    CheckConstraint(
      "download_status IN ('pending', 'ok', 'not_modified', 'failed', 'skipped')",
      name="ck_syllabus_links_download_status"),
  )

  id: Mapped[int] = mapped_column(primary_key=True)
  term_key: Mapped[str] = mapped_column(Text, index=True)
  course_code: Mapped[str] = mapped_column(Text)
  section: Mapped[str] = mapped_column(Text)
  url: Mapped[str] = mapped_column(Text)  # canonical
  link_type: Mapped[str] = mapped_column(Text)
  sources: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
  professor_id: Mapped[int | None] = mapped_column(ForeignKey("professors.id"))
  discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  download_status: Mapped[str] = mapped_column(Text, server_default="pending", index=True)
  http_status: Mapped[int | None] = mapped_column(Integer)
  # Nullable: no file until a download succeeds (your answer 4).
  document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))


class Syllabus(Base):
  # The "done list": a section and the file it links to. Created only after a
  # successful download, so document_id is NOT NULL.
  __tablename__ = "syllabi"
  __table_args__ = (
    UniqueConstraint("term_key", "course_code", "section", "document_id",
                     name="uq_syllabi_term_course_section_doc"),
  )

  id: Mapped[int] = mapped_column(primary_key=True)
  term_key: Mapped[str] = mapped_column(Text, index=True)
  term_label: Mapped[str] = mapped_column(Text)
  course_code: Mapped[str] = mapped_column(Text)
  section: Mapped[str | None] = mapped_column(Text)
  professor_id: Mapped[int | None] = mapped_column(ForeignKey("professors.id"))
  instructor_raw: Mapped[str | None] = mapped_column(Text)
  document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
  listing_url: Mapped[str | None] = mapped_column(Text)  # page where we found the link
  discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())