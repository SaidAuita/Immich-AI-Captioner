# Справочник конфигурационных файлов

В проекте используются три конфигурационных JSON-файла в зависимости от роли компонента:
1. `config.json` — Для Standalone-режима (`ImmichCaptioner.exe` / `main.py`).
2. `coordinator_config.json` — Для Координатора очереди (`coordinator.py`).
3. `worker_config.json` — Для Сетевого Воркера (`ImmichCaptionWorker.exe` / `file_worker.py`).

---

## 1. `config.json` (Standalone / Direct режим)

Используется приложением `ImmichCaptioner.exe`, когда оно напрямую обращается и к Immich API, и к LM Studio.

```json
{
  "immich": {
    "url": "https://immich.your-domain.com",
    "api_key": "YOUR_IMMICH_API_KEY",
    "batch_size": 50,
    "thumbnail_size": "preview"
  },
  "lm_studio": {
    "url": "http://localhost:1234/v1",
    "model": "qwen2.5-vl-7b-instruct",
    "temperature": 0.1,
    "max_tokens": 350
  },
  "immich_description_mode": "tags_only",
  "metadata_queue": {
    "enabled": true,
    "dir": "\\\\NAS\\ImmichMetadata\\queue"
  },
  "throttling": {
    "max_gpu_util_percent": 35,
    "check_interval_busy_seconds": 15,
    "idle_delay_between_photos_seconds": 1,
    "require_user_idle_seconds": 0,
    "heavy_processes": [
      "cyberpunk2077.exe",
      "blender.exe",
      "unrealengine.exe"
    ]
  }
}
```

### Новые параметры конфигурации:
- **`immich_description_mode`**: Режим формирования строки описания для базы Immich.
  - `"tags_only"` (по умолчанию): Записывает строго ключевые слова через запятую. Устраняет словесный мусор и обеспечивает 100% точность поиска.
  - `"title_and_tags"`: Заголовок + строка тегов.
  - `"full"`: Полный текст описания сцены + теги.
- **`metadata_queue`**: Интеграция с DropSync и демоном `apply_metadata.py` на сервере.
  - `enabled`: `true` — сохранять JSON-задания для вшивания стандартизированных IPTC/XMP в оригинальные файлы фото на сервере.
  - `dir`: Сетевой или локальный путь к папке очереди DropSync.

---

## 2. `coordinator_config.json` (Сервер / Координатор)

Используется скриптом `coordinator.py` для синхронизации очереди с Immich.

| Поле | Тип | Назначение |
| :--- | :--- | :--- |
| `immich.url` | string | Базовый URL сервера Immich (например, `http://immich-server:2283` или `https://immich.your-domain.com`). |
| `immich.api_key` | string | Секретный API-ключ Immich с правами чтения ассетов и обновления их метаданных. |
| `immich.batch_size` | int | Размер страницы при запросе списка ассетов (по умолчанию 100). |
| `immich.thumbnail_size` | string | Тип превью: `"preview"` (рекомендуется) или `"thumbnail"`. |
| `queue.base_dir` | string | Путь к корневой папке очереди на сервере (например, `./CaptionQueue` или `/mnt/storage/CaptionQueue`). |
| `queue.in_dir_name` | string | Имя подпапки входящих задач (по умолчанию `"In"`). |
| `queue.out_dir_name` | string | Имя подпапки готовых результатов (по умолчанию `"Out"`). |
| `queue.buffer_target` | int | Желаемое количество фото в папке `In/` (по умолчанию 60). Предотвращает переполнение диска. |
| `queue.poll_interval_seconds` | float | Интервал проверки папок очереди и сервера Immich (по умолчанию 5 секунд). |
| `queue.stale_claim_timeout_minutes` | float | Таймаут (в минутах), после которого зависший файл `.claim_*.tmp` считается брошенным и возвращается в очередь (по умолчанию 15 минут). |

---

## 3. `worker_config.json` (Клиент / Воркер)

Используется сетевым воркером (`ImmichCaptionWorker.exe` и `file_worker.py`).

| Секция | Поле | Тип | Описание |
| :--- | :--- | :--- | :--- |
| **queue** | `base_dir` | string | Путь к общей папке очереди. На Windows-клиентах обычно задается как UNC-путь: `\\\\NAS\\CaptionQueue`. |
| | `in_dir_name` | string | Подпапка входящих (по умолчанию `"In"`). |
| | `out_dir_name` | string | Подпапка готовых (по умолчанию `"Out"`). |
| | `poll_interval_seconds` | float | Пауза при пустой очереди перед следующей проверкой (по умолчанию 3 секунды). |
| **lm_studio** | `url` | string | Эндпоинт OpenAI API в LM Studio (по умолчанию `http://localhost:1234/v1`). |
| | `model` | string | Название модели (или фрагмент), например `qwen/qwen3-vl-8b` или `qwen2.5-vl-7b-instruct`. |
| | `temperature` | float | Температура генерации (рекомендуется `0.1`–`0.3` для фактологических описаний без галлюцинаций). |
| | `max_tokens` | int | Максимальное количество генерируемых токенов ответа (по умолчанию 350). |
| **throttling** | `max_gpu_util_percent` | int | Порог утилизации GPU в %. Если загрузка выше, воркер ждёт освобождения ресурсов. |
| | `check_interval_busy_seconds` | int | Интервал повторной проверки нагрузки во время паузы (по умолчанию 15 сек). |
| | `idle_delay_between_photos_seconds` | float | Пауза на «передышку» GPU между двумя последовательными фотографиями (1–2 сек). |
| | `require_user_idle_seconds` | int | Требовать ли простой мыши/клавиатуры (0 = отключено, обрабатывать всегда). |
| | `heavy_processes` | list[str] | Список исполняемых файлов процессов, при активности которых инференс блокируется. |
| **worker** | `id` | string | Уникальное имя воркера (например, `worker-gpu-1`, `laptop-rtx`). Отображается в имени файлов блокировки. |
