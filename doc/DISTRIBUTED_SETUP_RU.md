# Инструкция по настройке сетевого режима (Distributed)

Сетевой режим предназначен для сценария, когда сервер Immich развёрнут на домашнем сервере или NAS (Unraid, Synology, TrueNAS, Ubuntu Server), а нейросетевая обработка возлагается на отдельный мощный рабочий ПК с видеокартой NVIDIA RTX.

---

## 1. Схема работы

1. **Сервер (Immich + демон ExifTool)**:
   - `coordinator.py`: Периодически находит в Immich фото без тегов и сохраняет их превью в общую сетевую папку (`CaptionQueue/In/`).
   - `apply_metadata.py`: Читает готовые результаты из `CaptionQueue/Out/` и через ExifTool вшивает IPTC/XMP метаданные в оригинальные файлы.
2. **Рабочий ПК (GPU Клиент)**:
   - `ImmichCaptionWorker.exe`: Подключается к сетевой шаре `\\NAS\CaptionQueue`, забирает задания, отправляет в локальный LM Studio и сохраняет готовый JSON в `Out/`.

---

## 2. Настройка на сервере (Linux / Docker)

1. Создайте общую сетевую папку (SMB или NFS):
   ```bash
   mkdir -p /mnt/photos/CaptionQueue/In
   mkdir -p /mnt/photos/CaptionQueue/Out
   ```
2. Настройте файл `coordinator_config.json`:
   ```json
   {
     "immich": {
       "url": "http://localhost:2283",
       "api_key": "ВАШ_API_КЛЮЧ_ИЗ_IMMICH",
       "batch_size": 25
     },
     "queue": {
       "base_dir": "/mnt/photos/CaptionQueue"
     }
   }
   ```
3. Установите и запустите фоновую службу вшивания метаданных:
   ```bash
   sudo bash install_service.sh
   ```

---

## 3. Настройка на рабочем ПК (Windows с GPU)

1. Скопируйте `worker_config.example.json` в `worker_config.json`:
   ```json
   {
     "queue": {
       "base_dir": "\\\\NAS\\CaptionQueue",
       "in_dir_name": "In",
       "out_dir_name": "Out"
     },
     "lm_studio": {
       "url": "http://localhost:1234/v1",
       "model": "qwen2.5-vl-7b-instruct"
     },
     "worker": {
       "id": "my-rtx-worker"
     }
   }
   ```
2. Запустите `ImmichCaptionWorker.exe`.
3. Воркер начнёт автоматически обрабатывать входящую очередь файлов и передавать результат обратно на сервер.
