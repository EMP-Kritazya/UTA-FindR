from pathlib import Path
from docling.document_converter import DocumentConverter

current_dir = Path(__file__).parent
pdf_path = current_dir/"pdfs/Kritazya_UResume.pdf"

converter = DocumentConverter()
doc = converter.convert(pdf_path).document

with open("DocOut.json", "w", encoding="utf-8") as f:
    f.write(doc.model_dump_json(indent=2))

print(f"Saved structured document1")

for item, depth in doc.iterate_items():
    label = item.label.value
    text = getattr(item, "text", "")
    page = item.prov[0].page_no if item.prov else None

    print(f"[{label}] [page={page}] [depth={depth}]")
    print(text)
    print()