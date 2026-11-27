FROM python:3.12-slim

LABEL maintainer="you@example.com"
LABEL description="Text Extractor: OCR tasks queue in Kafka + extracted text queue for embeddings"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TESSERACT_LANG=rus+eng \
    KAFKA_BOOTSTRAP=kafka:9092 \
    KAFKA_TASKS_TOPIC=documents.extract.tasks \
    KAFKA_TOPIC=documents.extracted \
    KAFKA_DLQ_TOPIC=documents.failed \
    KAFKA_CONSUMER_GROUP=text-extractor-ocr \
    KAFKA_MAX_MESSAGE_BYTES=33554432

WORKDIR /app

# Системные зависимости: Tesseract + языки, Poppler для PDF->image
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-rus \
        tesseract-ocr-eng \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
    sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else sys.exit(1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]