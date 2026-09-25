# 📷 Immich AI Captioner & Metadata Sync

> **Автоматическое нейросетевое распознавание (VLM), генерация точных поисковых тегов и вечное вшивание IPTC/XMP метаданных для [Immich](https://immich.app/) с помощью локальных моделей (LM Studio, Ollama, Qwen-VL) и ExifTool.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Immich API](https://img.shields.io/badge/Immich-REST%20API-orange.svg)](https://immich.app/)
[![UI: CustomTkinter](https://img.shields.io/badge/UI-CustomTkinter-2563eb.svg)](https://github.com/TomSchimansky/CustomTkinter)
[![ExifTool](https://img.shields.io/badge/Metadata-ExifTool-green.svg)](https://exiftool.org/)

[📖 Read documentation in English](README.md)

---

## 🌟 Какую проблему решает Immich AI Captioner?

[Immich](https://immich.app/) — прекрасный self-hosted сервер для хранения фото и видео. Однако у его встроенного поиска есть ограничения:
1. **Неточный семантический поиск**: Поиск CLIP часто выдаёт нерелевантные фото при попытке найти конкретные предметы, надписи или сложные запросы.
2. **Метаданные заперты внутри базы данных**: Все теги и описания живут только в базе PostgreSQL. В случае переустановки, сбоя или переноса архива на другую систему все описания и теги будут безвозвратно утеряны.

### Решение:
* **Интеллектуальный анализ изображений**: Модели зрения (например, `Qwen2.5-VL-7B` или `Qwen3-VL-8B` в LM Studio/Ollama) детально анализируют изображение, формируя список из 5–15 конкретных существительных-тегов, лаконичный заголовок и считывают текст с вывесок/номеров (OCR).
* **Вечное сохранение (Source of Truth)**: Фоновый демон ExifTool вшивает стандартные метаданные прямо в заголовки файлов (`IPTC:Keywords`, `XMP-dc:Subject`, `XMP-photoshop:Headline`), а для RAW-снимков и видео — в `.xmp` sidecar-файлы. Фотографии будут искаться в Adobe Lightroom, Bridge, Photoshop, DigiKam и обычном Проводнике Windows даже без Immich.
* **100% локально и конфиденциально**: Никаких облаков и платных API. Вся обработка происходит исключительно на вашей собственной видеокарте.

---

## 🖥️ Возможности графического интерфейса

Приложение имеет современный тёмный интерфейс на **CustomTkinter**:
- **Живой монитор GPU и VRAM**: График нагрузки видеокарты в реальном времени с настраиваемыми порогами.
- **Переключатель режима троттлинга (`Auto 85% / ON`)**:
  - `Auto 85%`: Автоматически приостанавливает обработку, если GPU нагружен свыше 85% (например, при запуске 3D-игр или рендера).
  - `ON`: Работает на 100% мощности без пауз для максимальной скорости пакетной обработки архива.
- **Подсчёт скорости и ETA**: Расчёт среднего времени на кадр, скорости (фото в час) и оставшегося времени до конца архива.
- **Безопасная переделка архива**: Возможность в один клик запустить переиндексацию всей библиотеки с самого начала.
- **Инструменты тестирования**: Проверка одного фото по ссылке из браузера Immich или тест пачки из 5 фото прямо из окна программы.
- **Сворачивание в трей**: Приложение может работать в фоне в системном трее Windows.

---

## 🏗️ Архитектура и режимы работы

Поддерживаются две схемы развёртывания:

```
─────────────────────────────────────────────────────────────────────────────
РЕЖИМ 1: STANDALONE (ЛОКАЛЬНЫЙ) — Для одного ПК или простой домашней сети
─────────────────────────────────────────────────────────────────────────────

[ ПК с видеокартой NVIDIA (RTX 3060/3080/4090) ]
     │
     ├─► 1. ImmichCaptioner.exe подключается к REST API Immich
     ├─► 2. Передаёт превью в локальный LM Studio / Ollama
     ├─► 3. Записывает теги прямо в Immich (/api/tags, /api/assets)
     └─► 4. (Опционально) Отправляет задания в локальную/сетевую очередь ExifTool


─────────────────────────────────────────────────────────────────────────────
РЕЖИМ 2: DISTRIBUTED (КЛАСТЕР) — Сервер с Immich + Мощный рабочий ПК с GPU
─────────────────────────────────────────────────────────────────────────────

[ Домашний сервер / NAS с Immich ]       [ Рабочий ПК с видеокартой ]
           │                                          │
           ├─► coordinator.py                         │
           │   Выбирает фото без тегов и кладёт       │
           │   превью в общую SMB/NFS папку           │
           │   (\\\\NAS\\CaptionQueue\\In\\)           │
           │                                          │
           │                          ◄───────────────┤
           │                   ImmichCaptionWorker.exe│
           │                   Забирает пачки из In/, │
           │                   распознаёт в LM Studio,│
           │                   пишет результат в Out/ │
           │                                          │
           │◄─────────────────────────────────────────┤
           ▼
 apply_metadata.py (демон на Linux)
 Читает Out/, вшивает IPTC/XMP в оригиналы файлов
 на сервере через ExifTool, обновляет stats.json
```

---

## ⚡ Быстрый старт

### 1. Требования
- **Сервер Immich**: Доступный инстанс и API-ключ ([документация Immich по получению ключа](https://immich.app/docs/features/command-line-interface#obtain-the-api-key)).
- **Сервер модели (VLM)**: Установленный [LM Studio](https://lmstudio.ai/) или [Ollama](https://ollama.ai/) на машине с GPU.
  - Рекомендуемые модели: `qwen2.5-vl-7b-instruct` или `qwen3-vl-8b-instruct`.
  - Запустите локальный сервер (`http://localhost:1234/v1` в LM Studio).

### 2. Запуск Standalone-версии

1. Скачайте свежий `ImmichCaptioner.exe` из раздела [Releases](https://github.com/SaidAuita/Immich-AI-Captioner/releases).
2. Скопируйте файл `config.example.json` рядом с программой и переименуйте в `config.json`:
   ```json
   {
     "immich": {
       "url": "http://192.168.1.100:2283",
       "api_key": "ВАШ_API_КЛЮЧ_ИЗ_IMMICH"
     },
     "lm_studio": {
       "url": "http://localhost:1234/v1",
       "model": "qwen2.5-vl-7b-instruct"
     },
     "throttling": {
       "mode": "auto_85",
       "max_gpu_util_percent": 85
     }
   }
   ```
3. Запустите `ImmichCaptioner.exe` и нажмите **▶ Старт**!

---

## ⚙️ Справочник параметров конфигурации

| Параметр | Тип | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `immich.url` | string | `http://localhost:2283` | Базовый адрес вашего сервера Immich |
| `immich.api_key` | string | - | Пользовательский API ключ Immich |
| `immich.thumbnail_size` | string | `preview` | Размер превью для VLM (`preview` или `thumbnail`) |
| `lm_studio.url` | string | `http://localhost:1234/v1` | Адрес OpenAI-совместимого сервера модели |
| `lm_studio.model` | string | `qwen2.5-vl-7b-instruct` | Название загруженной модели |
| `immich_description_mode` | string | `tags_only` | Режим записи: `tags_only` (только теги), `title_and_tags`, `full` |
| `throttling.mode` | string | `auto_85` | Режим нагрузки: `auto_85` (лимит 85%) или `always_on` (без пауз) |
| `throttling.max_gpu_util_percent` | int | `85` | Порог загрузки GPU для авто-паузы |
| `metadata_queue.enabled` | bool | `false` | Включение записи задач в очередь для ExifTool |

---

## 🌐 Локализация (i18n)

Интерфейс поддерживает переключение языков. Переключить язык между **English** и **Русский** можно прямо в шапке окна.

Файлы переводов находятся в папке `locales/`:
- `locales/en.json` — Английский
- `locales/ru.json` — Русский

---

## 🛠️ Сборка из исходников

```bash
# Клонируйте репозиторий
git clone https://github.com/SaidAuita/Immich-AI-Captioner.git
cd Immich-AI-Captioner

# Установите зависимости
pip install -r requirements.txt

# Запуск автономного GUI
python ui_app.py

# Запуск сетевого воркера GUI
python ui_worker.py

# Сборка бинарников под Windows
pyinstaller ImmichCaptioner.spec --noconfirm
```

---

## 📄 Лицензия

Проект распространяется под свободной лицензией [MIT](LICENSE).
Immich является зарегистрированной торговой маркой своих владельцев. Данная утилита разработана независимым сообществом.
