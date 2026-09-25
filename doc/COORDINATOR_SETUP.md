# Настройка и запуск Координатора (Coordinator)

Координатор отвечает за связь с сервером Immich, наполнение входящей очереди `CaptionQueue/In` и выгрузку готовых описаний из `CaptionQueue/Out` обратно в Immich.

Обычно координатор запускается на том же сервере, где работает Immich, либо на домашнем NAS (Synology, TrueNAS, Unraid, Ubuntu Server).

---

## 1. Требования

- Python 3.9+ (достаточно стандартной библиотеки, никаких тяжёлых зависимостей для координатора не требуется).
- Сетевой доступ к Immich API (например, `http://immich-server:2283` или `http://192.168.1.100:2283`).
- API-ключ Immich (создаётся в веб-интерфейсе Immich: `Account Settings -> API Keys -> New API Key`).
- Папка очереди `CaptionQueue`, открытая по сети через Samba (SMB) или NFS для доступа воркеров.

---

## 2. Настройка конфигурации (`coordinator_config.json`)

В папке скрипта создайте или отредактируйте `coordinator_config.json`:

```json
{
  "immich": {
    "url": "http://immich-server:2283",
    "api_key": "YOUR_IMMICH_API_KEY",
    "batch_size": 100,
    "thumbnail_size": "preview"
  },
  "queue": {
    "base_dir": "/mnt/storage/CaptionQueue",
    "in_dir_name": "In",
    "out_dir_name": "Out",
    "buffer_target": 60,
    "poll_interval_seconds": 5,
    "stale_claim_timeout_minutes": 15
  }
}
```

### Параметры:
- `buffer_target` (по умолчанию 60): сколько картинок одновременно держать в папке `In/`. Как только воркеры разбирают фото, координатор автоматически подкачивает следующую партию из Immich.
- `thumbnail_size`: `"preview"` (рекомендуется) — сжатое изображение достаточного разрешения для VLM, экономит трафик и диск; либо `"thumbnail"`.
- `stale_claim_timeout_minutes`: через сколько минут считать задачу «зависшей», если воркер упал и не удалил временный файл `.claim_*.tmp`.

---

## 3. Настройка Samba (SMB) для доступа воркеров

Чтобы воркеры с Windows-машин могли читать и писать в очередь, расшарьте папку `CaptionQueue`.

### Пример для Ubuntu Server (`/etc/samba/smb.conf`):
```ini
[CaptionQueue]
   path = /mnt/storage/CaptionQueue
   browseable = yes
   read only = no
   guest ok = yes
   create mask = 0777
   directory mask = 0777
   force user = nobody
```

Примените настройки:
```bash
sudo systemctl restart smbd
```

Проверьте с Windows-клиента, открыв в Проводнике:
`\\NAS\CaptionQueue`
Убедитесь, что внутри создаются и удаляются файлы без ошибок доступа.

---

## 4. Запуск в виде Systemd-сервиса (Linux)

В корне проекта подготовлен файл [immich-caption-coordinator.service](../immich-caption-coordinator.service).

1. Скопируйте файл сервиса:
   ```bash
   sudo cp immich-caption-coordinator.service /etc/systemd/system/
   ```

2. Откройте его и проверьте пути к Python и рабочей директории:
   ```ini
   [Unit]
   Description=Immich AI Caption Coordinator Service
   After=network.target

   [Service]
   Type=simple
   User=said
   WorkingDirectory=/opt/ImmichCaptioner
   ExecStart=/usr/bin/python3 coordinator.py
   Restart=always
   RestartSec=10
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```

3. Активируйте и запустите:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable immich-caption-coordinator
   sudo systemctl start immich-caption-coordinator
   ```

4. Просмотр логов:
   ```bash
   sudo journalctl -u immich-caption-coordinator -f
   ```

---

## 5. Запуск на Windows Server

Для ручного или отладочного запуска на Windows предусмотрен батник:
- [run_coordinator.bat](../run_coordinator.bat)

Для автоматического запуска в фоне без консольного окна можно использовать планировщик задач Windows (Task Scheduler) или скрипт `run_hidden.vbs`.
