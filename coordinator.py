import os
import sys
import time
import json
import signal
import glob
from immich_client import ImmichClient
from state import StateManager

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coordinator_config.json")

DEFAULT_CONFIG = {
    "immich": {
        "url": "http://localhost:2283",
        "api_key": "YOUR_IMMICH_API_KEY_HERE",
        "batch_size": 100,
        "thumbnail_size": "preview"
    },
    "queue": {
        "base_dir": "./CaptionQueue",
        "in_dir_name": "In",
        "out_dir_name": "Out",
        "buffer_target": 60,
        "poll_interval_seconds": 5,
        "stale_claim_timeout_minutes": 15
    }
}

def log(msg: str):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}\n"
    try:
        sys.stdout.buffer.write(line.encode('utf-8', errors='replace'))
        sys.stdout.buffer.flush()
    except Exception:
        print(line, end='', flush=True)

class CaptionCoordinator:
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.config = self.load_config()
        self.running = True

        imm_cfg = self.config.get("immich", {})
        self.immich = ImmichClient(imm_cfg.get("url", ""), imm_cfg.get("api_key", ""))

        q_cfg = self.config.get("queue", {})
        base_dir = os.path.abspath(q_cfg.get("base_dir", "./CaptionQueue"))
        self.in_dir = os.path.join(base_dir, q_cfg.get("in_dir_name", "In"))
        self.out_dir = os.path.join(base_dir, q_cfg.get("out_dir_name", "Out"))
        self.buffer_target = int(q_cfg.get("buffer_target", 60))
        self.poll_interval = float(q_cfg.get("poll_interval_seconds", 5))
        self.stale_timeout = float(q_cfg.get("stale_claim_timeout_minutes", 15)) * 60.0

        os.makedirs(self.in_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)

        state_file = os.path.join(os.path.dirname(self.config_path), "coordinator_state.json")
        self.state = StateManager(state_file)

        self.current_page = 1
        self.consecutive_empty = 0
        self.stats_applied = 0

    def load_config(self) -> dict:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data
            except Exception as e:
                log(f"Предупреждение: ошибка чтения {self.config_path}: {e}. Используем дефолтные настройки.")
        else:
            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
                log(f"Создан конфигурационный файл: {self.config_path}")
            except Exception as e:
                log(f"Не удалось сохранить дефолтный конфиг: {e}")
        return DEFAULT_CONFIG

    def get_in_queue_status(self) -> tuple[list[str], list[str]]:
        """Returns (available_jpgs, claimed_tmps) in In directory."""
        try:
            entries = os.listdir(self.in_dir)
        except Exception:
            return [], []

        available = []
        claimed = []
        for name in entries:
            if name.endswith(".jpg") and not name.startswith("."):
                available.append(name)
            elif ".claim_" in name and name.endswith(".tmp"):
                claimed.append(name)
        return available, claimed

    def refill_in_queue(self):
        """Fetches unprocessed photos from Immich and populates In/ directory."""
        available, claimed = self.get_in_queue_status()
        active_count = len(available) + len(claimed)

        needed = self.buffer_target - active_count
        if needed <= 0:
            return

        batch_size = self.config.get("immich", {}).get("batch_size", 100)
        thumb_size = self.config.get("immich", {}).get("thumbnail_size", "preview")

        try:
            candidates = self.immich.get_unprocessed_assets(page=self.current_page, size=batch_size)
        except Exception as e:
            log(f"Ошибка получения списка из Immich: {e}")
            return

        # Existing asset IDs already in In or Out
        existing_ids = set()
        for f in available + claimed:
            asset_id = f.split(".")[0]
            existing_ids.add(asset_id)
        for f in os.listdir(self.out_dir):
            if f.endswith(".txt"):
                existing_ids.add(f.split(".")[0])

        valid_items = [
            a for a in candidates 
            if a['id'] not in existing_ids and not self.state.should_skip(a['id'])
        ]

        if not valid_items:
            self.consecutive_empty += 1
            if self.consecutive_empty > 5:
                self.current_page = 1
                self.consecutive_empty = 0
            else:
                self.current_page += 1
            return

        self.consecutive_empty = 0
        added = 0

        for asset in valid_items:
            if not self.running or added >= needed:
                break

            asset_id = asset['id']
            filename = asset.get('originalFileName', 'unknown')

            try:
                # 1. Download image bytes
                img_bytes = self.immich.download_preview_bytes(asset_id, thumb_size)
                if not img_bytes:
                    continue

                # 2. Write atomically (temp file then rename)
                part_path = os.path.join(self.in_dir, f"{asset_id}.part")
                final_path = os.path.join(self.in_dir, f"{asset_id}.jpg")

                with open(part_path, "wb") as pf:
                    pf.write(img_bytes)

                os.replace(part_path, final_path)
                added += 1
            except Exception as e:
                log(f"Ошибка выгрузки превью для [{filename}] ({asset_id}): {e}")
                self.state.mark_failed(asset_id, str(e))

        if added > 0:
            log(f"📥 Добавлено в In/: {added} новых фото (в очереди: {len(available) + len(claimed) + added}/{self.buffer_target})")

    def process_out_results(self):
        """Checks Out/ directory for completed .txt descriptions and updates Immich."""
        try:
            files = [f for f in os.listdir(self.out_dir) if f.endswith(".txt") and not f.startswith(".")]
        except Exception:
            return

        for fname in files:
            if not self.running:
                break

            asset_id = fname[:-4]  # remove .txt
            txt_path = os.path.join(self.out_dir, fname)

            try:
                with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                    description = f.read().strip()

                if description:
                    # Update Immich
                    ok = self.immich.update_description(asset_id, description)
                    if ok:
                        self.state.mark_processed(asset_id)
                        self.stats_applied += 1
                        short_desc = description.replace('\n', ' ')
                        if len(short_desc) > 80:
                            short_desc = short_desc[:77] + "..."
                        log(f"✅ Immich обновлён [{asset_id}]: \"{short_desc}\" (всего: {self.stats_applied})")
                    else:
                        log(f"⚠️ Immich вернул статус ошибки для [{asset_id}]")
                else:
                    log(f"⚠️ Пустой файл описания: {fname}")
                    self.state.mark_failed(asset_id, "Пустой результат в Out")

                # Remove .txt from Out
                try:
                    os.remove(txt_path)
                except Exception:
                    pass

                # Also cleanup any leftover in In/ if exists
                for pat in (f"{asset_id}.jpg", f"{asset_id}.part", f"{asset_id}.claim_*"):
                    for leftover in glob.glob(os.path.join(self.in_dir, pat)):
                        try:
                            os.remove(leftover)
                        except Exception:
                            pass

            except Exception as e:
                log(f"Ошибка обработки результата {fname}: {e}")

    def cleanup_stale_claims(self):
        """Recovers tasks whose worker crashed or timed out."""
        _, claimed = self.get_in_queue_status()
        now = time.time()

        for cname in claimed:
            cpath = os.path.join(self.in_dir, cname)
            try:
                mtime = os.path.getmtime(cpath)
                if now - mtime > self.stale_timeout:
                    # Parse original asset id: <asset_id>.claim_<worker>.tmp
                    asset_id = cname.split(".claim_")[0]
                    target_path = os.path.join(self.in_dir, f"{asset_id}.jpg")
                    os.replace(cpath, target_path)
                    log(f"🔄 Сброшен зависший захват задачи [{asset_id}] (таймаут {self.stale_timeout/60:.0f}м)")
            except Exception as e:
                log(f"Ошибка проверки таймаута для {cname}: {e}")

    def run(self):
        log("=== Запуск Координатора Очереди Распознавания (Immich Coordinator) ===")
        log(f"Сервер Immich: {self.config.get('immich', {}).get('url')}")
        log(f"Папка In:      {self.in_dir}")
        log(f"Папка Out:     {self.out_dir}")
        log(f"Лимит пула:    {self.buffer_target} фото")

        # Test Immich
        try:
            user = self.immich.test_connection()
            log(f"Подключение к Immich успешно: {user.get('name')} ({user.get('email')})")
        except Exception as e:
            log(f"ОШИБКА: Не удалось подключиться к Immich: {e}")
            return

        last_refill = 0
        last_stale_check = 0

        while self.running:
            now = time.time()

            # 1. Process incoming descriptions from Out/
            self.process_out_results()

            # 2. Refill In/ queue
            if now - last_refill >= self.poll_interval:
                last_refill = now
                self.refill_in_queue()

            # 3. Check for stale claims every 60 seconds
            if now - last_stale_check >= 60:
                last_stale_check = now
                self.cleanup_stale_claims()

            # Sleep
            for _ in range(int(self.poll_interval * 2)):
                if not self.running:
                    break
                time.sleep(0.5)

        log("=== Координатор остановлен ===")

def main():
    coordinator = CaptionCoordinator()

    def handle_sig(sig, frame):
        log("Получен сигнал завершения. Останавливаем координатор...")
        coordinator.running = False

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    coordinator.run()

if __name__ == '__main__':
    main()
