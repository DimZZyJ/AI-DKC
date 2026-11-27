import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await kp.start_producer()
    try:
        yield
    finally:
        await kp.stop_producer()


app = FastAPI(
    title="Text Extractor API",
    description="Принимает файлы, ставит задачи распознавания в Kafka и передаёт готовый "
                "текст в очередь embedding-обработки",
    version="3.0.0",
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


@app.get("/")
def root():
    return {"status": "ok", "service": "Text Extractor API"}


@app.get("/health")
def health():
    return {"status": "healthy", "kafka_producer": kp.is_ready()}


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
async def ingest(file: UploadFile = File(...)):
    """Принимает файл и ставит задачу на распознавание в очередь Kafka.

    Распознаванием занимается отдельный воркер, который затем кладёт
    готовый текст в очередь embedding-обработки.
    """
    file_bytes = await _read_and_validate(file)

    doc_id = kp.new_doc_id()
    try:
        await kp.publish_task(doc_id, file.filename, file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except Exception as e:
        logger.exception("doc_id=%s: не удалось поставить задачу в Kafka", doc_id)
        raise HTTPException(
            status_code=503,
            detail="Очередь задач временно недоступна",
        ) from e

    return {"doc_id": doc_id, "filename": file.filename, "status": "queued"}