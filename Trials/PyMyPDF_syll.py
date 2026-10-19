from pathlib import Path
import pymupdf
import json

current_dir = Path(__file__).parent
pdf_path = current_dir/"syllabus/2026-FALL_2268-CSE-3330-001.pdf"
doc = pymupdf.open(pdf_path)

pyOut = open("output_syllabus/pyOut_syll.txt", "wb")

for page in doc:
  text = page.get_text().encode("utf-8")
  pyOut.write(text)
  pyOut.write(bytes((12,)))


output = {
  "page_count": doc.page_count,
  "metadata": doc.metadata,
  "table_of_contents": doc.get_toc()
}

with open("output_syllabus/pyOut_desc.txt", "w", encoding="utf-8") as f:
  f.write(json.dumps(output, indent = 2))


pyOut.close()
doc.close()