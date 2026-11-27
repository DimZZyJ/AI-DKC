import base64
import json
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

from app import kafka_settings as ks

# Оставлено для совместимости: максимальный размер текстового чанка.
MAX_MSG_BYTES = ks.MAX_TEXT_CHUNK_BYTES

_producer: AIOKafkaProducer | None = None


def _serialize(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


async def start_producer() -> None:
    global _producer
    _producer = AIOKafkaProducer(
        bootstrap_servers=ks.BOOTSTRAP,
        acks="all",
        enable_idempotence=True,
        # Файл в base64 больше дефолтного лимита aiokafka (1 МБ).
        max_request_size=ks.MAX_MESSAGE_BYTES,
        value_serializer=_serialize,
        key_serializer=lambda k: k.encode("utf-8"),
    )
    await _producer.start()


async def stop_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


def is_ready() -> bool:
    return _producer is not None


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


async def publish_task(doc_id: str, filename: str, file_bytes: bytes) -> None:
    """Ставит задачу на распознавание файла в очередь Kafka."""
    assert _producer is not None, "Kafka producer не запущен"

    value = {
        "doc_id": doc_id,
        "filename": filename,
        "file_base64": base64.b64encode(file_bytes).decode("ascii"),
        "size_bytes": len(file_bytes),
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }

    # base64 увеличивает данные примерно на 1/3 — проверяем до отправки,
    # чтобы получить понятную ошибку вместо таймаута в send_and_wait.
    payload_size = len(_serialize(value))
    if payload_size > ks.MAX_MESSAGE_BYTES:
        raise ValueError(
            f"Файл слишком большой для очереди Kafka: "
            f"{payload_size} байт при лимите {ks.MAX_MESSAGE_BYTES}"
        )

    await _producer.send_and_wait(
        ks.TASKS_TOPIC,
        key=doc_id,
        value=value,
    )


async def publish_extracted(doc_id: str, result: dict) -> int:
    """Публикует текст документа в очередь embedding-обработки.

    Возвращает число отправленных сообщений.
    """
    assert _producer is not None, "Kafka producer не запущен"

    parts = split_by_bytes(result["text"])
    for i, part in enumerate(parts):
        await _producer.send_and_wait(
            ks.EXTRACTED_TOPIC,
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


async def publish_failed(
    doc_id: str,
    filename: str,
    error: str,
    stage: str = "ocr",
) -> None:
    assert _producer is not None, "Kafka producer не запущен"

    await _producer.send_and_wait(
        ks.DLQ_TOPIC,
        key=doc_id,
        value={
            "doc_id": doc_id,
            "filename": filename,
            "stage": stage,
            "error": error,
        },
    )
