"""Content-addressed storage for raw downloaded files.

Each file is saved under the SHA-256 hash of its bytes:
    data/raw/3f/3f9a...c1.pdf

Why content addressing:
- Dedup for free: 11 sections of CSE 1106 linking the same syllabus = 1 file.
- A hash never changes, so the DB can point at it safely, and the embedding
  program can find a file from its row alone (no separate path column to drift).
- If a professor re-uploads a changed syllabus, the new bytes get a new hash,
  so the old version is kept for citation instead of being overwritten.
The sha[:2] subfolder keeps any one directory from holding thousands of files.

Why magic bytes instead of the URL extension: DM links end in "files?file=..."
and send `application/octet-stream`, and a ".pdf" URL can serve an HTML error
page. The first bytes of the file tell us what it really is.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from ingest.config import get_settings

MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "html": "text/html",
    "json": "application/json",
}
SYLLABUS_EXTS = {"pdf", "docx", "doc"}


def detect_type(content: bytes) -> str | None:
    """Return 'pdf' | 'docx' | 'doc' | 'html' | 'json', or None if unknown."""
    head = content[:1024]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        # DOCX is a zip archive; real Word docs contain word/document.xml.
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
        return None
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):  # OLE2 container = legacy .doc
        return "doc"
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()  # strip BOM/whitespace
    if text.startswith((b"<!doctype html", b"<html")):
        return "html"
    if text[:1] in (b"{", b"["):
        try:
            json.loads(content)
            return "json"
        except ValueError:
            return None
    return None


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class RawStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else get_settings().raw_dir

    def path_for(self, sha: str, ext: str) -> Path:
        return self.root / sha[:2] / f"{sha}.{ext}"

    def exists(self, sha: str, ext: str) -> bool:
        return self.path_for(sha, ext).exists()

    def save(self, content: bytes, ext: str) -> str:
        """Write content once; return its sha256. Saving identical bytes again is a no-op."""
        sha = sha256_of(content)
        path = self.path_for(sha, ext)
        if path.exists():
            return sha
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename: a crash mid-write never leaves a
        # half-written file under a valid hash name.
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return sha
