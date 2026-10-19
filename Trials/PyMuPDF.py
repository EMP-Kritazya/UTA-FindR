from pathlib import Path
import pymupdf

current_dir = Path(__file__).parent
pdf_path = current_dir/"pdfs/Kritazya_UResume.pdf"
doc = pymupdf.open(pdf_path)

pyOut = open("pyOut.txt", "wb")

for page in doc:
  text = page.get_text().encode("utf-8")
  pyOut.write(text)
  pyOut.write(bytes((12,)))

pyOut.close()
doc.close()