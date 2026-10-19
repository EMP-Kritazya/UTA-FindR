"""Lightweight "does this file have text?" check, run right after download.

This is NOT parsing; Docling does that later in the embedding program. We only
want to know early whether a syllabus is usable: a scanned PDF (images only) or
an empty upload has ~0 extractable characters, and those need OCR or a human.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Below this many non-whitespace characters we call a file "no text". A real
# syllabus has thousands; a scanned PDF usually yields 0 or a few stray chars.
MIN_TEXT_CHARS = 200


@dataclass
class ContentCheck:
    page_count: int | None
    text_chars: int | None
    has_text: bool | None
    error: str | None = None


def _count(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


def check_file(path: Path, ext: str) -> ContentCheck:
    try:
        if ext == "pdf":
            import pymupdf

            with pymupdf.open(path) as doc:
                chars = sum(_count(page.get_text()) for page in doc)
                return ContentCheck(doc.page_count, chars, chars >= MIN_TEXT_CHARS)
        if ext == "docx":
            import docx

            d = docx.Document(str(path))
            parts = [p.text for p in d.paragraphs]
            # Many syllabi put the schedule and grading in tables.
            for table in d.tables:
                for row in table.rows:
                    parts.extend(cell.text for cell in row.cells)
            chars = _count("\n".join(parts))
            # DOCX has no fixed pages; page count is only known after rendering.
            return ContentCheck(None, chars, chars >= MIN_TEXT_CHARS)
    except Exception as e:  # corrupt or password-protected files shouldn't stop the crawl
        return ContentCheck(None, None, None, error=f"{type(e).__name__}: {e}")
    # Legacy .doc and anything else: unknown, left for the parser.
    return ContentCheck(None, None, None)
