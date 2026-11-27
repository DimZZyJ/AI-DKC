"""Consumer очереди задач на распознавание текста."""

import base64
import json
import logging

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from app import kafka_settings as ks

logger = logging.getLogger(__name__)

_consumer: AIOKafkaConsumer | None = None


async def start_consumer() -> None:
    global _consumer
    _consumer = AIOKafkaConsumer(
        ks.TASKS_TOPIC,
        bootstrap_servers=ks.BOOTSTRAP,
        group_id=ks.CONSUMER_GROUP,
        auto_offset_reset="earliest",
        # Коммитим сами только после успешной обработки (at-least-once).
        enable_auto_commit=False,
        # Задача содержит файл в base64 — дефолтного лимита 1 МБ мало.
        max_partition_fetch_bytes=ks.MAX_MESSAGE_BYTES,
    )
    await _consumer.start()
    logger.info(
        "Consumer запущен: topic=%s group=%s bootstrap=%s",
        ks.TASKS_TOPIC, ks.CONSUMER_GROUP, ks.BOOTSTRAP,
    )


async def stop_consumer() -> None:
    global _consumer
    if _consumer is not None:
        await _consumer.stop()
        _consumer = None


def get_consumer() -> AIOKafkaConsumer:
    assert _consumer is not None, "Kafka consumer не запущен"
    return _consumer


def task_identity(msg) -> tuple[str, str]:
    """Достаёт doc_id и имя файла даже из повреждённой задачи (для DLQ)."""
    key = msg.key.decode("utf-8", "replace") if isinstance(msg.key, bytes) else msg.key
    value = msg.value
    if isinstance(value, bytes):
        try:
            value = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            value = {}
    if not isinstance(value, dict):
        value = {}

    doc_id = value.get("doc_id") or key or f"unknown-{msg.partition}-{msg.offset}"
    return str(doc_id), str(value.get("filename") or "")


def decode_task(msg) -> tuple[str, str, bytes]:
    """Разбирает сообщение задачи: doc_id, имя файла и байты файла."""
    value = msg.value
    if isinstance(value, bytes):
        value = json.loads(value.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Сообщение задачи должно быть JSON-объектом")

    key = msg.key.decode("utf-8") if isinstance(msg.key, bytes) else msg.key
    doc_id = value.get("doc_id") or key
    filename = value.get("filename")
    encoded = value.get("file_base64")

    if not doc_id:
        raise ValueError("В задаче не указан doc_id")
    if not filename:
        raise ValueError("В задаче не указано имя файла")
    if not isinstance(encoded, str):
        raise ValueError("В задаче нет поля file_base64")

    return str(doc_id), str(filename), base64.b64decode(encoded, validate=True)


async def commit_msg(msg) -> None:
    """Подтверждаем обработку конкретного сообщения."""
    consumer = get_consumer()
    await consumer.commit({
        TopicPartition(msg.topic, msg.partition): OffsetAndMetadata(msg.offset + 1, ""),
    })
