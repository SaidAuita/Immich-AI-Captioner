# Инструкция по настройке автономного режима (Standalone)

В автономном (Standalone) режиме **Immich AI Captioner** работает на вашем рабочем компьютере или ноутбуке с видеокартой NVIDIA. Программа подключается к серверу Immich по сети через официальный REST API, а распознавание выполняет локальный сервер LM Studio или Ollama.

---

## 1. Подготовка

1. **Сервер Immich**: работающий инстанс (например, `http://192.168.1.100:2283` или `https://immich.your-domain.com`).
2. **Получение API ключа**:
   - Войдите в веб-интерфейс Immich.
   - Нажмите на иконку профиля (вверху справа) ➔ **Настройки аккаунта** (Account Settings) ➔ **Ключи API** (API Keys).
   - Нажмите **Создать ключ** (New API Key), назовите его `Captioner` и скопируйте сгенерированный токен.
3. **Локальная нейросеть (Vision LLM)**:
   - Установите [LM Studio](https://lmstudio.ai/).
   - В поиске найдите и скачайте модель зрения, например `qwen2.5-vl-7b-instruct` или `qwen3-vl-8b-instruct`.
   - В LM Studio перейдите во вкладку **Local Server** (`<->`), выберите загруженную модель и нажмите **Start Server** (порт по умолчанию: `1234`).

---

## 2. Настройка файла `config.json`

Создайте файл `config.json` рядом с `ImmichCaptioner.exe`:

```json
{
  "immich": {
    "url": "http://192.168.1.100:2283",
    "api_key": "ВАШ_API_КЛЮЧ_ИЗ_IMMICH",
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
  "throttling": {
    "mode": "auto_85",
    "max_gpu_util_percent": 85,
    "check_interval_busy_seconds": 15,
    "idle_delay_between_photos_seconds": 1,
    "require_user_idle_seconds": 0,
    "heavy_processes": [
      "cyberpunk2077.exe",
      "blender.exe",
      "unrealengine.exe"
    ]
  },
  "ui_language": "ru"
}
```

---

## 3. Запуск и управление

1. Запустите `ImmichCaptioner.exe`.
2. В строке статуса должно отобразиться сообщение: `Подключено к Immich (ИмяПользователя)`.
3. Для проверки распознавания:
   - Скопируйте ссылку на любое фото из браузера Immich (например, `https://.../photos/<uuid>`).
   - Вставьте в поле `🧪 Тест фото` и нажмите `⚡ Распознать`.
   - В блоке превью отобразятся сгенерированные теги и заголовок.
4. Нажмите **▶ Старт** для запуска автоматической пакетной обработки всего архива.
5. Окно программы можно закрыть — оно продолжит тихо работать в системном трее Windows.
