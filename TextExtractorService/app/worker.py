"""Отдельный воркер: берёт файлы из Kafka, распознаёт текст и кладёт его
в следующую очередь (documents.extracted) для embedding-модели.

Запуск: python -m app.worker
"""

import asyncio
import logging
import os

from app import kafka_consumer as kc
from app import kafka_producer as kp
from app.extractors import extract_text

logger = logging.getLogger(__name__)


async def _handle_task(msg) -> None:
    try:
        doc_id, filename, file_bytes = kc.decode_task(msg)
    except Exception as e:
        doc_id, filename = kc.task_identity(msg)
        logger.exception("doc_id=%s: некорректная задача из Kafka", doc_id)
        await kp.publish_failed(doc_id, filename, str(e), stage="queue")
        await kc.commit_msg(msg)
        return

    try:
        # OCR/парсинг блокирующие — выносим из event loop.
        result = await asyncio.to_thread(extract_text, file_bytes, filename)

        if not result["text"].strip():
            raise ValueError("В документе не найден текст")

        parts = await kp.publish_extracted(doc_id, result)
        logger.info(
            "doc_id=%s файл=%s: распознано и отправлено в embedding-очередь чанков: %d",
            doc_id, filename, parts,
        )
    except Exception as e:
        logger.exception("doc_id=%s файл=%s: ошибка распознавания", doc_id, filename)
        # Если DLQ недоступен — исключение уйдёт выше, сообщение не будет
        # закоммичено и задача останется в очереди для повторной попытки.
        await kp.publish_failed(doc_id, filename, str(e), stage="ocr")

    # Коммитим после публикации результата или ошибки в DLQ.
    await kc.commit_msg(msg)


async def run_worker() -> None:
    await kp.start_producer()
    await kc.start_consumer()
    try:
        async for msg in kc.get_consumer():
            await _handle_task(msg)
    finally:
        await kc.stop_consumer()
        await kp.stop_producer()


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_worker())
