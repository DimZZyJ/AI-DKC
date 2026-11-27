# TextExtractorService

Сервис принимает файлы, ставит распознавание текста (OCR) в очередь Kafka,
а готовый текст отправляет в следующую очередь — на обработку embedding-моделью
и запись в векторную базу (PGVector через n8n).

## Поток данных

```text
POST /ingest (API)
    │  задача: doc_id, filename, file_base64
    ▼
documents.extract.tasks  ──► text-extractor-worker (OCR)
                                    │  текст, разбитый на чанки
                                    ▼
                          documents.extracted ──► n8n → embedding → PGVector

Любая ошибка ──► documents.failed (DLQ)
```

## Топики

| Топик | Назначение | Ключ | Партиций |
|---|---|---|---|
| `documents.extract.tasks` | Задачи на распознавание файлов | `doc_id` | 3 |
| `documents.extracted` | Готовый текст для embedding-модели | `doc_id` | 3 |
| `documents.failed` | Ошибки OCR/валидации задачи | `doc_id` | 1 |

Сообщение задачи:

```json
{
  "doc_id": "...",
  "filename": "document.pdf",
  "file_base64": "...",
  "size_bytes": 12345,
  "enqueued_at": "2026-11-26T20:00:00+00:00"
}
```

Сообщение с текстом (поле `text` ограничено 500 КБ UTF-8):

```json
{
  "doc_id": "...",
  "filename": "document.pdf",
  "file_type": "pdf",
  "chunk_index": 0,
  "chunks_total": 2,
  "text": "..."
}
```

Поддерживаемые форматы: `.docx`, `.pdf`, `.xlsx`, `.xlsm`, `.csv`, `.md`,
`.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.webp`. Markdown читается
как обычный текст с определением кодировки (UTF-8, CP1251, Latin-1).

## Конфигурация

| Переменная | По умолчанию | Описание |
|---|---|---|
| `KAFKA_BOOTSTRAP` | `kafka:9092` | Адрес брокера |
| `KAFKA_TASKS_TOPIC` | `documents.extract.tasks` | Очередь OCR-задач |
| `KAFKA_TOPIC` | `documents.extracted` | Очередь текста для embedding |
| `KAFKA_DLQ_TOPIC` | `documents.failed` | Очередь ошибок |
| `KAFKA_CONSUMER_GROUP` | `text-extractor-ocr` | Группа consumer-ов воркера |
| `KAFKA_MAX_MESSAGE_BYTES` | `33554432` | Лимит сообщения (файл до 20 МБ в base64) |
| `LOG_LEVEL` | `INFO` | Уровень логов воркера |

## Запуск

```sh
docker compose up -d --build
docker compose logs -f text-extractor-worker

# Несколько воркеров для параллельного OCR:
docker compose up -d --scale text-extractor-worker=3
```

Проверка:

```sh
curl -F "file=@document.pdf" http://localhost:18000/ingest
```

## Загрузка файлов из папки через n8n

В корне репозитория лежит `FolderToExtractorQueue.json` — импортируйте его
в n8n. Воркфлоу читает все поддерживаемые файлы из папки `/files` внутри
контейнера n8n и отправляет каждый файл в `/ingest`.

В `docker-compose.yml` путь `/files` добавлен в `N8N_RESTRICT_FILE_ACCESS_TO` —
иначе n8n 2.x блокирует чтение (`Access to the file is not allowed.`), потому что
по умолчанию узел Read/Write Files имеет доступ только к `~/.n8n-files`.

Папку хоста можно задать через переменную `DOCS_FOLDER` (по умолчанию `./files`
рядом с `docker-compose.yml`):

```powershell
$env:DOCS_FOLDER = "C:\Моя\Папка"
docker compose up -d n8n text-extractor text-extractor-worker
```

Чтобы изменить путь уже внутри n8n, откройте узел **Set Folder** и поменяйте
значение поля `folder` (например, на `/home/node/.n8n/files`).

## Гарантии

- Задача коммитится только после публикации текста или ошибки в DLQ
  (at-least-once). При сбое между публикацией и коммитом возможны повторы —
  потребителям следует учитывать пару `doc_id` + `chunk_index`.
- Чанки с одинаковым `doc_id` попадают в одну партицию, поэтому порядок
  чанков сохраняется.
- `/extract` остался синхронным режимом для отладки и в Kafka ничего не пишет.
