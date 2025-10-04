# text_utils.py

import io
from pathlib import Path

def extract_text_from_any(file) -> str:
    filename = file.filename.lower()
    if filename.endswith(".pdf"):
        return _extract_from_pdf(file)
    elif filename.endswith(".docx"):
        return _extract_from_docx(file)
    else:
        return _extract_as_plain_text(file)

def _extract_from_pdf(file) -> str:
    import pdfplumber
    with pdfplumber.open(file) as pdf:
        return "\n".join(page.extract_text() or '' for page in pdf.pages)

def _extract_from_docx(file) -> str:
    from docx import Document
    doc = Document(io.BytesIO(file.read()))
    return "\n".join(p.text for p in doc.paragraphs)

def _extract_as_plain_text(file) -> str:
    try:
        return file.read().decode("utf-8")
    except Exception:
        return file.read().decode("latin1", errors="ignore")
