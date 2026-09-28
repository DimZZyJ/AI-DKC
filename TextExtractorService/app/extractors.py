import io
import logging

from docx import Document
from PIL import Image
from pypdf import PdfReader
import pytesseract
from pdf2image import convert_from_bytes

logger = logging.getLogger(__name__)

# Языки OCR. "rus+eng" — русский и английский. Требует установленных
# языковых пакетов tesseract-ocr-rus и tesseract-ocr-eng в образе.
OCR_LANGS = "rus+eng"

# Порог: если на странице PDF извлечено меньше символов, считаем её
# "пустой" (сканом) и запускаем OCR.
PDF_TEXT_THRESHOLD = 20


def _ocr_image(image: Image.Image) -> str:
    """Распознаёт текст на PIL-изображении."""
    try:
        return pytesseract.image_to_string(image, lang=OCR_LANGS)
    except pytesseract.TesseractError as e:
        logger.warning("Ошибка Tesseract: %s", e)
        return ""


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Извлекает текст из DOCX (абзацы + таблицы + картинки через OCR)."""
    document = Document(io.BytesIO(file_bytes))
    parts: list[str] = []

    # Абзацы
    parts.extend(p.text for p in document.paragraphs if p.text)

    # Таблицы
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text:
                    parts.append(cell.text)

    # Картинки внутри DOCX (отношения типа image)
    for rel in document.part.rels.values():
        if "image" in rel.reltype:
            try:
                image = Image.open(io.BytesIO(rel.target_part.blob))
                ocr_text = _ocr_image(image)
                if ocr_text.strip():
                    parts.append(ocr_text)
            except Exception as e:
                logger.warning("Не удалось обработать картинку в DOCX: %s", e)

    return "\n".join(parts)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Извлекает текст из PDF.
    Для страниц без текстового слоя (сканы) запускает OCR.
    """
    reader = PdfReader(io.BytesIO(file_bytes))
    pages_text: list[str] = []

    # Быстрая попытка: есть ли вообще текстовый слой
    plain_pages: list[str] = []
    for page in reader.pages:
        plain_pages.append(page.extract_text() or "")

    # Если весь PDF "пустой" — сразу OCR по всем страницам
    if sum(len(t.strip()) for t in plain_pages) < PDF_TEXT_THRESHOLD:
        logger.info("PDF похож на скан, запускаем OCR по всем страницам")
        return _ocr_pdf(file_bytes)

    # Смешанный случай: страницы с текстом берём как есть,
    # "пустые" страницы отправляем на OCR (поштучно)
    for idx, text in enumerate(plain_pages):
        if len(text.strip()) >= PDF_TEXT_THRESHOLD:
            pages_text.append(text)
        else:
            pages_text.append(_ocr_pdf_page(file_bytes, idx))

    return "\n".join(pages_text)


def _ocr_pdf(file_bytes: bytes) -> str:
    """OCR всех страниц PDF."""
    images = convert_from_bytes(file_bytes, dpi=300)
    return "\n".join(_ocr_image(img) for img in images)


def _ocr_pdf_page(file_bytes: bytes, page_index: int) -> str:
    """OCR одной конкретной страницы PDF (нумерация с 0)."""
    images = convert_from_bytes(
        file_bytes, dpi=300, first_page=page_index + 1, last_page=page_index + 1
    )
    return _ocr_image(images[0]) if images else ""


def extract_text_from_image(file_bytes: bytes) -> str:
    """OCR отдельного файла-картинки."""
    image = Image.open(io.BytesIO(file_bytes))
    # Конвертируем в RGB (на случай палитры/альфы)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    return _ocr_image(image)


def extract_text(file_bytes: bytes, filename: str) -> dict:
    """Определяет формат по расширению и извлекает текст."""
    name = filename.lower()

    if name.endswith(".docx"):
        text, file_type = extract_text_from_docx(file_bytes), "docx"
    elif name.endswith(".pdf"):
        text, file_type = extract_text_from_pdf(file_bytes), "pdf"
    elif name.endswith((".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp")):
        text, file_type = extract_text_from_image(file_bytes), "image"
    else:
        raise ValueError(
            f"Неподдерживаемый формат файла: {filename}. "
            "Поддерживаются: .docx, .pdf, .png, .jpg, .jpeg, .tiff, .bmp, .webp"
        )

    return {
        "filename": filename,
        "file_type": file_type,
        "text": text,
        "length": len(text),
    }