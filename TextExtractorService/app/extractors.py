import csv
import io
import logging

from docx import Document as open_docx
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader
import pytesseract
from pdf2image import convert_from_bytes

logger = logging.getLogger(__name__)

OCR_LANGS = "rus+eng"
PDF_TEXT_THRESHOLD = 20
PDF_OCR_DPI = 300

CSV_DELIMITERS = [",", ";", "\t", "|"] 
# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def _ocr_image(image: Image.Image) -> str:
    try:
        return pytesseract.image_to_string(image, lang=OCR_LANGS)
    except pytesseract.TesseractError as e:
        logger.warning("Ошибка Tesseract: %s", e)
        return ""
    except Exception as e:
        logger.warning("Неожиданная ошибка OCR: %s", e)
        return ""


def _ocr_image_bytes(blob: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(blob))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        return _ocr_image(image)
    except Exception as e:
        logger.warning("Не удалось обработать картинку: %s", e)
        return ""


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def _iter_block_items(parent):
    """Итерирует тело документа в реальном порядке."""
    if isinstance(parent, DocxDocument):
        body = parent.element.body
        parent_obj = parent
    else:
        body = parent._element
        parent_obj = parent

    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent_obj)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent_obj)


def _paragraph_images(paragraph: Paragraph, document: DocxDocument) -> list[str]:
    """OCR всех картинок, встроенных в параграф (fallback-путь)."""
    results: list[str] = []
    blips = paragraph._p.findall(".//" + qn("a:blip"))
    for blip in blips:
        r_id = blip.get(qn("r:embed"))
        if not r_id:
            continue
        rel = document.part.rels.get(r_id)
        if rel is None or "image" not in rel.reltype:
            continue
        ocr_text = _ocr_image_bytes(rel.target_part.blob).strip()
        if ocr_text:
            results.append(ocr_text)
    return results


def _process_paragraph(paragraph: Paragraph, document: DocxDocument) -> list[str]:
    """Возвращает текст/OCR-блоки параграфа в порядке следования runs."""
    parts: list[str] = []

    for run in paragraph.runs:
        blips = run._r.findall(".//" + qn("a:blip"))
        for blip in blips:
            r_id = blip.get(qn("r:embed"))
            if not r_id:
                continue
            rel = document.part.rels.get(r_id)
            if rel is None or "image" not in rel.reltype:
                continue
            ocr_text = _ocr_image_bytes(rel.target_part.blob).strip()
            if ocr_text:
                parts.append(ocr_text)

        if run.text:
            parts.append(run.text)

    # Fallback: картинка лежит не в run
    if not any("a:blip" in run._r.xml for run in paragraph.runs):
        parts.extend(_paragraph_images(paragraph, document))

    return parts


def _process_table(table: Table, document: DocxDocument) -> list[str]:
    parts: list[str] = []
    for row in table.rows:
        for cell in row.cells:
            for block in _iter_block_items(cell):
                if isinstance(block, Paragraph):
                    parts.extend(_process_paragraph(block, document))
                elif isinstance(block, Table):
                    parts.extend(_process_table(block, document))
    return parts


def extract_text_from_docx(file_bytes: bytes) -> str:
    document = open_docx(io.BytesIO(file_bytes))
    parts: list[str] = []

    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            parts.extend(_process_paragraph(block, document))
        elif isinstance(block, Table):
            parts.extend(_process_table(block, document))

    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _pdf_has_text_layer(reader: PdfReader) -> bool:
    total = 0
    for page in reader.pages:
        try:
            total += len((page.extract_text() or "").strip())
        except Exception:
            continue
        if total >= PDF_TEXT_THRESHOLD:
            return True
    return False


def _render_pdf_pages(file_bytes: bytes) -> list[Image.Image]:
    return convert_from_bytes(file_bytes, dpi=PDF_OCR_DPI)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages_text: list[str] = []

    has_any_text = _pdf_has_text_layer(reader)

    if not has_any_text:
        logger.info("PDF без текстового слоя — OCR по всем страницам")
        for img in _render_pdf_pages(file_bytes):
            pages_text.append(_ocr_image(img))
        return "\n".join(pages_text)

    total_pages = len(reader.pages)
    for idx in range(total_pages):
        page = reader.pages[idx]
        try:
            text = page.extract_text() or ""
        except Exception as e:
            logger.warning("Ошибка extract_text на стр. %d: %s", idx + 1, e)
            text = ""

        if len(text.strip()) >= PDF_TEXT_THRESHOLD:
            pages_text.append(text)
        else:
            try:
                images = convert_from_bytes(
                    file_bytes,
                    dpi=PDF_OCR_DPI,
                    first_page=idx + 1,
                    last_page=idx + 1,
                )
                pages_text.append(_ocr_image(images[0]) if images else "")
            except Exception as e:
                logger.warning("Ошибка OCR стр. %d: %s", idx + 1, e)
                pages_text.append("")

    return "\n".join(pages_text)


# ---------------------------------------------------------------------------
# Картинки (отдельные файлы)
# ---------------------------------------------------------------------------

def extract_text_from_image(file_bytes: bytes) -> str:
    return _ocr_image_bytes(file_bytes)

# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def extract_text_from_xlsx(file_bytes: bytes) -> str:
    """
    Извлекает текст из XLSX.
    Обходит листы в их порядке, внутри листа — строки сверху вниз,
    столбцы слева направо. Пустые ячейки превращаются в пустые
    строки (сохраняется структура таблицы).
    """
    try:
        workbook = load_workbook(
            io.BytesIO(file_bytes),
            read_only=True,
            data_only=True,   # берём значения, а не формулы
        )
    except Exception as e:
        raise ValueError(f"Не удалось открыть XLSX: {e}")

    parts: list[str] = []

    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        parts.append(f"### {sheet_name}")

        for row in sheet.iter_rows(values_only=True):
            # Преобразуем значения ячеек в строки, None -> ""
            cells = ["" if v is None else str(v) for v in row]

            # Пропускаем полностью пустые строки
            if not any(cell.strip() for cell in cells):
                continue

            # Склеиваем через табуляцию — так сохраняется структура колонок
            parts.append("\t".join(cells))

        parts.append("")  # пустая строка между листами

    workbook.close()
    return "\n".join(parts).rstrip()


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _detect_csv_delimiter(sample: str) -> str:
    """
    Определяет разделитель CSV по первой непустой строке.
    По умолчанию — запятая.
    """
    first_line = ""
    for line in sample.splitlines():
        if line.strip():
            first_line = line
            break

    if not first_line:
        return ","

    # Считаем вхождения каждого разделителя
    counts = {d: first_line.count(d) for d in CSV_DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def extract_text_from_csv(file_bytes: bytes) -> str:
    """
    Извлекает текст из CSV.
    Разделитель определяется автоматически (,, ;, \\t, |).
    Кодировка — utf-8 с fallback на cp1251 и latin-1.
    """
    # Пробуем декодировать
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            text = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        raise ValueError("Не удалось определить кодировку CSV")

    delimiter = _detect_csv_delimiter(text)

    parts: list[str] = []
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        # Склеиваем через табуляцию — так удобнее читать
        parts.append("\t".join(row))

    return "\n".join(parts)

# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def extract_text(file_bytes: bytes, filename: str) -> dict:
    """Определяет формат по расширению и извлекает текст."""
    name = filename.lower()

    if name.endswith(".docx"):
        text, file_type = extract_text_from_docx(file_bytes), "docx"
    elif name.endswith(".pdf"):
        text, file_type = extract_text_from_pdf(file_bytes), "pdf"
    elif name.endswith((".xlsx", ".xlsm")):
        text, file_type = extract_text_from_xlsx(file_bytes), "xlsx"
    elif name.endswith(".csv"):
        text, file_type = extract_text_from_csv(file_bytes), "csv"
    elif name.endswith((".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp")):
        text, file_type = extract_text_from_image(file_bytes), "image"
    else:
        raise ValueError(
            f"Неподдерживаемый формат файла: {filename}. "
            "Поддерживаются: .docx, .pdf, .xlsx, .xlsm, .csv, "
            ".png, .jpg, .jpeg, .tiff, .bmp, .webp"
        )

    return {
        "filename": filename,
        "file_type": file_type,
        "text": text,
        "length": len(text),
    }