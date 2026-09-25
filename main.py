import json
import os
import sys
import time
import signal
from immich_client import ImmichClient
from captioner import VlmCaptioner, format_immich_description
from gpu_monitor import is_system_busy, get_gpu_stats, get_user_idle_seconds
from state import StateManager

# Safe UTF-8 printing for Windows console
def log(msg: str):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}\n"
    try:
        sys.stdout.buffer.write(line.encode('utf-8', errors='replace'))
        sys.stdout.buffer.flush()
    except Exception:
        print(line, end='', flush=True)

    # Also append to log file
    try:
        log_file = os.path.join(os.path.dirname(__file__), "captioner.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

running = True

def handle_signal(sig, frame):
    global running
    log("Получен сигнал завершения. Завершаем текущую задачу и выходим...")
    running = False

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

def run():
    config = load_config()
    immich_cfg = config.get("immich", {})
    lm_cfg = config.get("lm_studio", {})
    throttle_cfg = config.get("throttling", {})

    log("=== Запуск Immich AI Captioner ===")
    log(f"Сервер Immich: {immich_cfg.get('url')}")
    log(f"LM Studio: {lm_cfg.get('url')} (модель: {lm_cfg.get('model')})")
    
    immich = ImmichClient(immich_cfg["url"], immich_cfg["api_key"])
    captioner = VlmCaptioner(
        lm_cfg["url"],
        lm_cfg["model"],
        temperature=lm_cfg.get("temperature", 0.2),
        max_tokens=lm_cfg.get("max_tokens", 350)
    )
    state = StateManager()

    # Verify Immich connection
    try:
        user_info = immich.test_connection()
        log(f"Успешное подключение к Immich! Пользователь: {user_info.get('name')} ({user_info.get('email')})")
    except Exception as e:
        log(f"ОШИБКА подключения к Immich API: {e}")
        return

    # Main loop
    consecutive_empty_pages = 0
    current_page = 1

    while running:
        # 1. Check if LM Studio is reachable
        if not captioner.test_connection():
            log(f"Ожидание LM Studio ({lm_cfg['url']}). Проверьте, что сервер запущен в LM Studio...")
            for _ in range(10):
                if not running:
                    break
                time.sleep(1)
            continue

        # 2. Check system load / user activity
        busy, reason = is_system_busy(throttle_cfg)
        if busy:
            log(f"Пауза: {reason}. Ожидание освобождения ресурсов...")
            interval = throttle_cfg.get("check_interval_busy_seconds", 15)
            for _ in range(interval):
                if not running:
                    break
                time.sleep(1)
            continue

        # 3. Fetch a batch of unprocessed assets
        batch_size = immich_cfg.get("batch_size", 50)
        try:
            unprocessed = immich.get_unprocessed_assets(page=current_page, size=batch_size)
        except Exception as e:
            log(f"Ошибка получения списка фото из Immich: {e}. Повтор через 10с.")
            time.sleep(10)
            continue

        # Filter out already handled in state
        candidates = [a for a in unprocessed if not state.should_skip(a['id'])]

        if not candidates:
            consecutive_empty_pages += 1
            if consecutive_empty_pages > 5:
                # Wrap back to page 1 to catch any newly uploaded photos
                current_page = 1
                consecutive_empty_pages = 0
                log("Все текущие фото обработаны. Ожидание новых загрузок (проверка через 30с)...")
                for _ in range(30):
                    if not running:
                        break
                    time.sleep(1)
            else:
                current_page += 1
            continue

        consecutive_empty_pages = 0
        log(f"Найдено фото для обработки на стр. {current_page}: {len(candidates)}")

        for asset in candidates:
            if not running:
                break

            asset_id = asset['id']
            filename = asset.get('originalFileName', 'unknown')

            # Re-check load before each photo
            busy, reason = is_system_busy(throttle_cfg)
            while busy and running:
                log(f"Пауза перед фото [{filename}]: {reason}...")
                interval = throttle_cfg.get("check_interval_busy_seconds", 15)
                for _ in range(interval):
                    if not running:
                        break
                    time.sleep(1)
                busy, reason = is_system_busy(throttle_cfg)

            if not running:
                break

            t_start = time.time()
            try:
                # Download preview
                b64_img = immich.download_preview_b64(asset_id, immich_cfg.get("thumbnail_size", "preview"))
                
                # Recognize with VLM
                caption_data = captioner.generate_caption(b64_img)
                
                if isinstance(caption_data, dict):
                    title = caption_data.get("title", "").strip()
                    desc_text = caption_data.get("description", "").strip()
                    tags = caption_data.get("tags", [])
                    ocr = caption_data.get("ocr", "").strip()
                else:
                    title = ""
                    desc_text = str(caption_data).strip()
                    tags = []
                    ocr = ""

                if desc_text or title or tags:
                    # Format description for Immich based on config mode (default: tags_only)
                    desc_mode = config.get("immich_description_mode", "tags_only")
                    immich_desc = format_immich_description(caption_data, mode=desc_mode)

                    # 1. Update Immich description
                    immich.update_description(asset_id, immich_desc)

                    # 2. Apply tags in Immich
                    applied_tags = 0
                    if tags:
                        applied_tags = immich.apply_tags_to_asset(asset_id, tags)

                    # 3. Write metadata task for cladovka (DropSync queue)
                    q_cfg = config.get("metadata_queue", {})
                    if q_cfg.get("enabled", False):
                        q_dir = q_cfg.get("dir", "./queue")
                        try:
                            os.makedirs(q_dir, exist_ok=True)
                            task_file = os.path.join(q_dir, f"{asset_id}.json")
                            task_data = {
                                "asset_id": asset_id,
                                "container_path": asset.get("originalPath", ""),
                                "title": title,
                                "description": desc_text,
                                "tags": tags,
                                "ocr": ocr
                            }
                            with open(task_file, "w", encoding="utf-8") as tf:
                                json.dump(task_data, tf, ensure_ascii=False, indent=2)
                        except Exception as q_err:
                            log(f"Предупреждение: ошибка записи в DropSync очередь: {q_err}")

                    state.mark_processed(asset_id)
                    elapsed = time.time() - t_start
                    
                    gpu_util, mem_used, _ = get_gpu_stats()
                    log(f"OK [{filename}] ({elapsed:.1f}с, GPU {gpu_util}%, VRAM {mem_used}MB):")
                    log(f"   Заголовок: \"{title}\" | Тегов: {applied_tags}/{len(tags)}")
                    log(f"   Описание:  \"{desc_text[:80]}{'...' if len(desc_text) > 80 else ''}\"")
                else:
                    log(f"Предупреждение: модель вернула пустое описание для [{filename}]")
                    state.mark_failed(asset_id, "Пустое описание")

            except Exception as e:
                log(f"Ошибка при обработке [{filename}] ({asset_id}): {e}")
                state.mark_failed(asset_id, str(e))

            # Rest between photos
            rest = throttle_cfg.get("idle_delay_between_photos_seconds", 2)
            time.sleep(rest)

    log("=== Immich AI Captioner остановлен ===")

if __name__ == '__main__':
    run()
