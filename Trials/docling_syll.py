from pathlib import Path
from docling.document_converter import DocumentConverter

current_dir = Path(__file__).parent
pdf_path = current_dir/"syllabus/2026-FALL_2268-CSE-3330-001.pdf"

converter = DocumentConverter()
doc = converter.convert(pdf_path).document

with open("/output_syllabus/DocSyll.txt", "w", encoding="utf-8") as file:
  file.write(doc.model_dump_json(indent=2))

print("SAVED SYLLABUS")
