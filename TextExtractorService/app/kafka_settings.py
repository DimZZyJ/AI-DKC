"""Общие настройки Kafka для API и worker-а.

Значения читаются из окружения, чтобы API и воркер всегда использовали
одинаковые топики и лимиты размеров сообщений.
"""

import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")

# Очередь задач: API кладёт сюда файлы на распознавание.
TASKS_TOPIC = os.getenv("KAFKA_TASKS_TOPIC", "documents.extract.tasks")

# Очередь готового текста: worker кладёт сюда чанки, откуда их берёт n8n
# для embedding-модели и записи в PGVector.
EXTRACTED_TOPIC = os.getenv("KAFKA_TOPIC", "documents.extracted")
DLQ_TOPIC = os.getenv("KAFKA_DLQ_TOPIC", "documents.failed")

CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "text-extractor-ocr")

# Лимит брокера/клиентов. Файл до 20 МБ в base64 занимает ~27 МБ,
# поэтому лимит намеренно больше максимального размера загружаемого файла.
MAX_MESSAGE_BYTES = _int_env("KAFKA_MAX_MESSAGE_BYTES", 32 * 1024 * 1024)

# Размер текстового чанка оставляем компактным: дальше текст ещё раз
# режется в n8n перед передачей в embedding-модель.
MAX_TEXT_CHUNK_BYTES = min(500_000, MAX_MESSAGE_BYTES // 4)
