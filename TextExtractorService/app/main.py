import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app import kafka_producer as kp
from app.extractors import extract_text

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = (
    ".docx", ".pdf",
    ".xlsx", ".xlsm", ".csv",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp",
)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

# Сколько документов обрабатывается (парсинг + OCR) одновременно.
# OCR тяжёлый, поэтому по умолчанию небольшое число.
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
_jobs_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await kp.start_producer()
    try:
        yield
    finally:
        await kp.stop_producer()


app = FastAPI(
    title="Text Extractor API",
    description="Сервис извлечения текста из DOCX / PDF / XLSX / CSV / изображений (с OCR) "
                "с публикацией результата в Kafka",
    version="2.0.0",
    lifespan=lifespan,
)


async def _read_and_validate(file: UploadFile) -> bytes:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Имя файла не указано")

    if not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail="Формат файла не поддерживается",
        )

    file_bytes = await file.read()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Файл пустой")

    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой. Максимум {MAX_FILE_SIZE // (1024 * 1024)} MB",
        )

    return file_bytes


async def _process_and_publish(doc_id: str, file_bytes: bytes, filename: str) -> None:
    """Фоновая задача: извлечь текст и отправить его в Kafka (или в DLQ при ошибке)."""
    try:
        async with _jobs_semaphore:
            # Тяжёлый парсинг + OCR — уводим из event loop
            result = await run_in_threadpool(extract_text, file_bytes, filename)

        if not result["text"].strip():
            raise ValueError("В документе не найден текст")

        parts = await kp.publish_extracted(doc_id, result)
        logger.info("doc_id=%s файл=%s: отправлено сообщений: %d", doc_id, filename, parts)
    except Exception as e:
        logger.exception("doc_id=%s файл=%s: ошибка обработки", doc_id, filename)
        try:
            await kp.publish_failed(doc_id, filename, str(e))
        except Exception:
            logger.exception("doc_id=%s: не удалось отправить ошибку в DLQ", doc_id)


@app.get("/")
def root():
    return {"status": "ok", "service": "Text Extractor API"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    """Синхронное извлечение текста (удобно для отладки). В Kafka ничего не отправляет."""
    file_bytes = await _read_and_validate(file)

    try:
        result = await run_in_threadpool(extract_text, file_bytes, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при обработке файла: {str(e)}",
        )

    return JSONResponse(content=result)


@app.post("/ingest", status_code=202)
async def ingest(background: BackgroundTasks, file: UploadFile = File(...)):
    """Принимает файл, сразу отвечает 202, а текст извлекается в фоне и уходит в Kafka."""
    file_bytes = await _read_and_validate(file)

    doc_id = kp.new_doc_id()
    background.add_task(_process_and_publish, doc_id, file_bytes, file.filename)

    return {"doc_id": doc_id, "filename": file.filename, "status": "queued"}