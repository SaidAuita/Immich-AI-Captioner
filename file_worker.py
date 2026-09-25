import os
import sys
import time
import json
import socket
import signal
import base64
from captioner import VlmCaptioner, format_immich_description
from gpu_monitor import is_system_busy, get_gpu_stats, get_user_idle_seconds

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker_config.json")

def get_default_worker_id() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "Worker"

DEFAULT_CONFIG = {
    "queue": {
        "base_dir": "./CaptionQueue",
        "in_dir_name": "In",
        "out_dir_name": "Out",
        "poll_interval_seconds": 3
    },
    "lm_studio": {
        "url": "http://localhost:1234/v1",
        "model": "qwen/qwen3-vl-8b",
        "temperature": 0.2,
        "max_tokens": 350
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
    },
    "worker": {
        "id": get_default_worker_id()
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

class CaptionFileWorker:
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.config = self.load_config()
        self.running = True
        self.current_claim_path = None
        self.current_orig_path = None

        q_cfg = self.config.get("queue", {})
        base_dir = os.path.abspath(q_cfg.get("base_dir", "./CaptionQueue"))
        self.in_dir = os.path.join(base_dir, q_cfg.get("in_dir_name", "In"))
        self.out_dir = os.path.join(base_dir, q_cfg.get("out_dir_name", "Out"))
        self.poll_interval = float(q_cfg.get("poll_interval_seconds", 3))

        os.makedirs(self.in_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)

        lm_cfg = self.config.get("lm_studio", {})
        self.captioner = VlmCaptioner(
            lm_cfg.get("url", "http://localhost:1234/v1"),
            lm_cfg.get("model", "qwen/qwen3-vl-8b"),
            temperature=lm_cfg.get("temperature", 0.2),
            max_tokens=lm_cfg.get("max_tokens", 350)
        )

        w_cfg = self.config.get("worker", {})
        self.worker_id = w_cfg.get("id") or get_default_worker_id()
        self.throttle_cfg = self.config.get("throttling", {})

        self.processed_count = 0

    def load_config(self) -> dict:
        base_dir = os.path.dirname(os.path.abspath(self.config_path))
        cfg = DEFAULT_CONFIG.copy()

        legacy_path = os.path.join(base_dir, "config.json")
        has_legacy = False
        legacy_cfg = {}
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    legacy_cfg = json.load(f)
                    has_legacy = True
            except Exception as e:
                log(f"Предупреждение: ошибка чтения {legacy_path}: {e}")

        if has_legacy:
            q_dir = legacy_cfg.get("metadata_queue", {}).get("dir")
            if q_dir:
                cfg["queue"]["base_dir"] = q_dir
            if "lm_studio" in legacy_cfg:
                lm = legacy_cfg["lm_studio"]
                if lm.get("url"): cfg["lm_studio"]["url"] = lm["url"]
                if lm.get("model"): cfg["lm_studio"]["model"] = lm["model"]
                if "temperature" in lm: cfg["lm_studio"]["temperature"] = lm["temperature"]
                if "max_tokens" in lm: cfg["lm_studio"]["max_tokens"] = lm["max_tokens"]
            if "throttling" in legacy_cfg:
                cfg["throttling"] = legacy_cfg["throttling"]
            if "immich_description_mode" in legacy_cfg:
                cfg["immich_description_mode"] = legacy_cfg["immich_description_mode"]
            if "caption_language" in legacy_cfg:
                cfg["caption_language"] = legacy_cfg["caption_language"]
            if "tags_language" in legacy_cfg:
                cfg["tags_language"] = legacy_cfg["tags_language"]
            if "ui_language" in legacy_cfg:
                cfg["ui_language"] = legacy_cfg["ui_language"]

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    w_cfg = json.load(f)
                w_q_dir = w_cfg.get("queue", {}).get("base_dir", "")
                if w_q_dir in ("", "\\\\NAS\\CaptionQueue") and has_legacy and legacy_cfg.get("metadata_queue", {}).get("dir"):
                    pass
                else:
                    if "queue" in w_cfg: cfg["queue"].update(w_cfg["queue"])

                w_model = w_cfg.get("lm_studio", {}).get("model", "")
                if w_model in ("", "qwen2.5-vl-7b-instruct", "qwen/qwen3-vl-8b") and has_legacy and legacy_cfg.get("lm_studio", {}).get("model"):
                    if "lm_studio" in w_cfg:
                        temp_lm = w_cfg["lm_studio"].copy()
                        temp_lm["model"] = legacy_cfg["lm_studio"]["model"]
                        cfg["lm_studio"].update(temp_lm)
                else:
                    if "lm_studio" in w_cfg: cfg["lm_studio"].update(w_cfg["lm_studio"])

                for k in ("immich_description_mode", "caption_language", "tags_language", "ui_language", "worker"):
                    if k in w_cfg: cfg[k] = w_cfg[k]
                if "throttling" in w_cfg: cfg["throttling"].update(w_cfg["throttling"])
            except Exception as e:
                log(f"Предупреждение: ошибка чтения {self.config_path}: {e}")
        else:
            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                log(f"Создан конфигурационный файл: {self.config_path}")
            except Exception as e:
                log(f"Не удалось сохранить конфиг: {e}")
        return cfg

    def claim_next_image(self) -> tuple[str, str, str]:
        """
        Attempts to atomically claim a .jpg from the In/ directory.
        Returns (asset_id, claim_file_path, original_file_path) or (None, None, None).
        """
        try:
            entries = [f for f in os.listdir(self.in_dir) if f.endswith(".jpg") and not f.startswith(".")]
        except Exception as e:
            log(f"Ошибка доступа к папке In ({self.in_dir}): {e}")
            return None, None, None

        for fname in entries:
            asset_id = fname[:-4]
            orig_path = os.path.join(self.in_dir, fname)
            claim_name = f"{asset_id}.claim_{self.worker_id}_{int(time.time())}.tmp"
            claim_path = os.path.join(self.in_dir, claim_name)

            try:
                # Atomic rename (on Windows & POSIX)
                os.rename(orig_path, claim_path)
                return asset_id, claim_path, orig_path
            except (OSError, FileNotFoundError):
                # Another worker claimed it first or file busy, continue to next
                continue

        return None, None, None

    def release_current_claim(self):
        """If worker is interrupted, rename claim back to original .jpg so others can process it."""
        if self.current_claim_path and os.path.exists(self.current_claim_path) and self.current_orig_path:
            try:
                os.replace(self.current_claim_path, self.current_orig_path)
                log(f"↩ Освобождена задача [{os.path.basename(self.current_orig_path)}] обратно в очередь.")
            except Exception:
                pass
            self.current_claim_path = None
            self.current_orig_path = None

    def run(self):
        log(f"=== Запуск Воркера Распознавания [{self.worker_id}] ===")
        log(f"Папка In:      {self.in_dir}")
        log(f"Папка Out:     {self.out_dir}")
        log(f"LM Studio:     {self.config.get('lm_studio', {}).get('url')} (модель: {self.config.get('lm_studio', {}).get('model')})")

        while self.running:
            # 1. Check if LM Studio is reachable
            if not self.captioner.test_connection():
                log(f"Ожидание LM Studio ({self.config.get('lm_studio', {}).get('url')})...")
                for _ in range(10):
                    if not self.running:
                        break
                    time.sleep(1)
                continue

            # 2. Check system load / throttling
            busy, reason = is_system_busy(self.throttle_cfg)
            if busy:
                log(f"Пауза: {reason}. Ожидание...")
                interval = self.throttle_cfg.get("check_interval_busy_seconds", 15)
                for _ in range(interval):
                    if not self.running:
                        break
                    time.sleep(1)
                continue

            # 3. Claim next task
            asset_id, claim_path, orig_path = self.claim_next_image()
            if not asset_id:
                # Queue is empty, wait
                time.sleep(self.poll_interval)
                continue

            self.current_claim_path = claim_path
            self.current_orig_path = orig_path

            t_start = time.time()
            try:
                # Read image file and convert to base64
                with open(claim_path, "rb") as img_file:
                    b64_img = base64.b64encode(img_file.read()).decode('utf-8')

                # Re-check load before inference
                busy, reason = is_system_busy(self.throttle_cfg)
                while busy and self.running:
                    log(f"Пауза перед инференсом: {reason}...")
                    time.sleep(self.throttle_cfg.get("check_interval_busy_seconds", 10))
                    busy, reason = is_system_busy(self.throttle_cfg)

                if not self.running:
                    self.release_current_claim()
                    break

                # Generate caption
                cap_lang = self.config.get("caption_language", "ru")
                tags_lang = self.config.get("tags_language", "en")
                caption_res = self.captioner.generate_caption(b64_img, desc_lang=cap_lang, tags_lang=tags_lang)

                if isinstance(caption_res, dict):
                    immich_mode = self.config.get("immich_description_mode", "tags_only")
                    description = format_immich_description(caption_res, mode=immich_mode, desc_lang=cap_lang)
                else:
                    description = str(caption_res).strip()

                if description:
                    # Write to Out/<asset_id>.txt atomically (via temp file)
                    out_part = os.path.join(self.out_dir, f"{asset_id}.part")
                    out_final = os.path.join(self.out_dir, f"{asset_id}.txt")

                    with open(out_part, "w", encoding="utf-8") as out_f:
                        out_f.write(description)

                    os.replace(out_part, out_final)

                    # Delete claim file from In/
                    try:
                        os.remove(claim_path)
                    except Exception:
                        pass

                    self.current_claim_path = None
                    self.current_orig_path = None

                    self.processed_count += 1
                    elapsed = time.time() - t_start
                    gpu_util, mem_used, _ = get_gpu_stats()

                    short_desc = description.replace('\n', ' ')
                    if len(short_desc) > 80:
                        short_desc = short_desc[:77] + "..."
                    log(f"[{self.worker_id}] OK [{asset_id}] за {elapsed:.1f}с (GPU {gpu_util}%, VRAM {mem_used}MB): \"{short_desc}\" (всего: {self.processed_count})")
                else:
                    log(f"[{self.worker_id}] ВНИМАНИЕ: модель вернула пустой ответ для [{asset_id}]")
                    # Release back so it can be handled or coordinator marks failed
                    self.release_current_claim()

            except Exception as e:
                log(f"[{self.worker_id}] Ошибка обработки [{asset_id}]: {e}")
                self.release_current_claim()

            # Delay between photos
            rest = self.throttle_cfg.get("idle_delay_between_photos_seconds", 1)
            time.sleep(rest)

        self.release_current_claim()
        log(f"=== Воркер [{self.worker_id}] остановлен ===")

def main():
    worker = CaptionFileWorker()

    def handle_sig(sig, frame):
        log("Получен сигнал завершения. Завершаем работу...")
        worker.running = False
        worker.release_current_claim()

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    worker.run()

if __name__ == '__main__':
    main()
