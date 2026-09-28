from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.extractors import extract_text

app = FastAPI(
    title="Text Extractor API",
    description="Сервис извлечения текста из DOCX и PDF файлов",
    version="1.0.0",
)

ALLOWED_EXTENSIONS = (".docx", ".pdf")
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


@app.get("/")
def root():
    return {"status": "ok", "service": "Text Extractor API"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Имя файла не указано")

    if not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail="Поддерживаются только файлы .docx и .pdf",
        )

    file_bytes = await file.read()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Файл пустой")

    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой. Максимум {MAX_FILE_SIZE // (1024 * 1024)} MB",
        )

    try:
        result = extract_text(file_bytes, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при обработке файла: {str(e)}",
        )

    return JSONResponse(content=result)