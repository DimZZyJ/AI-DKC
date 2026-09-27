import io
from docx import Document
from pypdf import PdfReader


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Извлекает текст из DOCX файла."""
    document = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in document.paragraphs]

    # Извлекаем текст из таблиц
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text:
                    paragraphs.append(cell.text)

    return "\n".join(paragraphs)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Извлекает текст из PDF файла."""
    reader = PdfReader(io.BytesIO(file_bytes))
    pages_text = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages_text.append(text)
    return "\n".join(pages_text)


def extract_text(file_bytes: bytes, filename: str) -> dict:
    """Определяет формат по расширению и извлекает текст."""
    filename_lower = filename.lower()

    if filename_lower.endswith(".docx"):
        text = extract_text_from_docx(file_bytes)
        file_type = "docx"
    elif filename_lower.endswith(".pdf"):
        text = extract_text_from_pdf(file_bytes)
        file_type = "pdf"
    else:
        raise ValueError(
            f"Неподдерживаемый формат файла: {filename}. "
            "Поддерживаются только .docx и .pdf"
        )

    return {
        "filename": filename,
        "file_type": file_type,
        "text": text,
        "length": len(text),
    }