import io
import zipfile

import docx
import pytest

from ingest.raw_store import RawStore, detect_type, sha256_of


def docx_bytes():
    buf = io.BytesIO()
    d = docx.Document()
    d.add_paragraph("syllabus")
    d.save(buf)
    return buf.getvalue()


def zip_bytes(name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, "x")
    return buf.getvalue()


@pytest.mark.parametrize("content, expected", [
    (b"%PDF-1.7\n...", "pdf"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", "doc"),
    (b"<!DOCTYPE html><html></html>", "html"),
    (b"\xef\xbb\xbf  \n<html><body>", "html"),  # BOM + whitespace before the tag
    (b'{"items": []}', "json"),
    (b"[1, 2, 3]", "json"),
    (b"{not json", None),
    (b"\x89PNG\r\n\x1a\n", None),  # an image is not a syllabus
    (b"", None),
])
def test_detect_type_from_magic_bytes(content, expected):
    assert detect_type(content) == expected


def test_detect_real_docx():
    assert detect_type(docx_bytes()) == "docx"


def test_zip_that_is_not_a_word_doc_is_rejected():
    # XLSX/PPTX/plain zips also start with "PK"; only word/document.xml makes a DOCX.
    assert detect_type(zip_bytes("xl/workbook.xml")) is None


def test_save_dedups_identical_content(tmp_path):
    store = RawStore(tmp_path)
    sha1 = store.save(b"%PDF-same bytes", "pdf")
    sha2 = store.save(b"%PDF-same bytes", "pdf")
    assert sha1 == sha2 == sha256_of(b"%PDF-same bytes")
    assert len(list(tmp_path.rglob("*.pdf"))) == 1


def test_different_content_gets_different_files(tmp_path):
    store = RawStore(tmp_path)
    a = store.save(b"%PDF-version 1", "pdf")
    b = store.save(b"%PDF-version 2", "pdf")  # e.g. a re-uploaded syllabus
    assert a != b
    assert store.exists(a, "pdf") and store.exists(b, "pdf")


def test_path_layout_and_no_temp_leftovers(tmp_path):
    store = RawStore(tmp_path)
    sha = store.save(b"hello", "json")
    assert store.path_for(sha, "json") == tmp_path / sha[:2] / f"{sha}.json"
    assert store.path_for(sha, "json").read_bytes() == b"hello"
    assert not list(tmp_path.rglob("*.part"))  # atomic write cleaned up after itself
