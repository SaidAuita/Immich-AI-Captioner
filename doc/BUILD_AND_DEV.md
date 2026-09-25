# Руководство по сборке и разработке

В этом документе описаны процесс компиляции исполняемых `.exe` файлов, структура исходного кода и рекомендации для дальнейшего расширения проекта.

---

## 1. Стек технологий и зависимости

- **Python**: 3.11+
- **Интерфейс**: CustomTkinter 6.0+, Pillow 10+
- **Трей**: pystray
- **Сборщик**: PyInstaller 6.x

Установка всех необходимых пакетов окружения:
```bash
pip install customtkinter pillow pystray pyinstaller
```

---

## 2. Сборка исполняемых файлов (.exe)

Оба приложения собираются в standalone `.exe` файлы с иконкой и ресурсами тем CustomTkinter.

### Сборка сетевого Воркера (`ImmichCaptionWorker.exe`):
В проекте подготовлен файл [ImmichCaptionWorker.spec](../ImmichCaptionWorker.spec). Для сборки выполните:

```bash
pyinstaller ImmichCaptionWorker.spec --noconfirm
```

После завершения скомпилированный бинарник будет находиться в папке `dist/ImmichCaptionWorker.exe`. Переместите его в корень проекта рядом с `worker_config.json`:
```powershell
Copy-Item dist\ImmichCaptionWorker.exe .\ImmichCaptionWorker.exe -Force
```

### Сборка автономного Captioner (`ImmichCaptioner.exe`):
Используется файл [ImmichCaptioner.spec](../ImmichCaptioner.spec):
```bash
pyinstaller ImmichCaptioner.spec --noconfirm
Copy-Item dist\ImmichCaptioner.exe .\ImmichCaptioner.exe -Force
```

---

## 3. Генерация иконки приложения

Для создания многослойной `.ico` иконки (16x16, 32x32, 48x48, 64x64, 128x128, 256x256) используется встроенный скрипт [create_icons.py](../create_icons.py):
```bash
python create_icons.py
```
Скрипт генерирует `app_icon.ico` на лету средствами библиотеки Pillow, рисуя стилизованную камеру с AI-меткой и индикатором.

---

## 4. Структура файлов проекта

```
ImmichCaptioner/
├── doc/                            # Документация проекта (все аспекты)
│   ├── README.md
│   ├── ARCHITECTURE.md
│   ├── COORDINATOR_SETUP.md
│   ├── WORKER_SETUP.md
│   ├── CONFIGURATION.md
│   ├── BUILD_AND_DEV.md
│   └── TROUBLESHOOTING.md
│
├── CaptionQueue/                   # Локальная очередь (если coordinator запущен локально)
│   ├── In/                         # Входящие задачи (.jpg)
│   └── Out/                        # Завершенные задачи (.txt)
│
├── ImmichCaptioner.exe             # Автономный GUI клиент (Direct режим)
├── ImmichCaptionWorker.exe         # Сетевой GUI воркер (Queue режим)
│
├── ui_app.py                       # Исходный код Direct GUI
├── ui_worker.py                    # Исходный код Network Worker GUI
├── coordinator.py                  # Координатор очереди (серверная часть)
├── file_worker.py                  # Консольный воркер очереди (headless)
├── captioner.py                    # Обёртка OpenAI API / LM Studio + системный промпт
├── immich_client.py                # Клиент REST API Immich
├── gpu_monitor.py                  # Телеметрия GPU, VRAM, idle time, heavy processes
├── state.py                        # Хранилище обработанных ID (state.json)
├── create_icons.py                 # Генератор app_icon.ico и иконок трея
│
├── config.json                     # Конфигурация прямого режима
├── coordinator_config.json         # Конфигурация координатора
├── worker_config.json              # Конфигурация воркера
│
├── run_coordinator.bat             # Быстрый запуск координатора в Windows
├── run_file_worker.bat             # Быстрый запуск консольного воркера в Windows
├── run_worker.bat                  # Запуск автономного скрипта
└── immich-caption-coordinator.service # Unit-файл systemd для Linux
```

---

## 5. Добавление новых моделей и изменение промпта

Системный промпт и правила формирования описания находятся в [captioner.py](../captioner.py) в переменной `SYSTEM_PROMPT`. 

Если вы хотите изменить стиль описаний:
- Откройте `captioner.py`.
- Отредактируйте `SYSTEM_PROMPT` (например, добавив требование определять сезон года, архитектурный стиль или марку автомобиля).
- Перезапустите воркер или пересоберите `.exe`.
