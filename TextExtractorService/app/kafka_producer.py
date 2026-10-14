import json
import os
import uuid

from aiokafka import AIOKafkaProducer

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "documents.extracted")
DLQ_TOPIC = os.getenv("KAFKA_DLQ_TOPIC", "documents.failed")

# Лимит брокера 2 МБ, у aiokafka max_request_size по умолчанию 1 МБ.
# Берём с запасом: кириллица = 2 байта/символ, JSON добавляет служебные поля.
MAX_MSG_BYTES = 500_000

_producer: AIOKafkaProducer | None = None


async def start_producer() -> None:
    global _producer
    _producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        acks="all",
        enable_idempotence=True,
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8"),
    )
    await _producer.start()


async def stop_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


def new_doc_id() -> str:
    return uuid.uuid4().hex


def split_by_bytes(text: str, max_bytes: int = MAX_MSG_BYTES) -> list[str]:
    """Режет текст по строкам так, чтобы каждая часть влезала в max_bytes (UTF-8)."""
    # В худшем случае символ занимает 4 байта
    step = max(1, max_bytes // 4 - 1)

    chunks: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            chunks.append("\n".join(current))
        current, size = [], 0

    for line in text.split("\n"):
        if len(line.encode("utf-8")) <= max_bytes:
            pieces = [line]
        else:
            # Очень длинная строка без переносов — режем жёстко
            pieces = [line[i:i + step] for i in range(0, len(line), step)]

        for piece in pieces:
            b = len(piece.encode("utf-8")) + 1  # +1 за перенос строки
            if size + b > max_bytes and current:
                flush()
            current.append(piece)
            size += b

    flush()
    return chunks or [""]


async def publish_extracted(doc_id: str, result: dict) -> int:
    """Публикует текст документа в топик. Возвращает число отправленных сообщений."""
    assert _producer is not None, "Kafka producer не запущен"

    parts = split_by_bytes(result["text"])
    for i, part in enumerate(parts):
        await _producer.send_and_wait(
            TOPIC,
            key=doc_id,  # один ключ -> одна партиция -> порядок частей сохраняется
            value={
                "doc_id": doc_id,
                "filename": result["filename"],
                "file_type": result["file_type"],
                "chunk_index": i,
                "chunks_total": len(parts),
                "text": part,
            },
        )
    return len(parts)


async def publish_failed(doc_id: str, filename: str, error: str) -> None:
    assert _producer is not None, "Kafka producer не запущен"

    await _producer.send_and_wait(
        DLQ_TOPIC,
        key=doc_id,
        value={"doc_id": doc_id, "filename": filename, "error": error},
    )