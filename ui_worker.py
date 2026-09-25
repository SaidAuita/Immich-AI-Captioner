import json
import os
import sys
import time
import base64
import io
import threading
from collections import deque
from PIL import Image, ImageTk

import tkinter as tk
import customtkinter as ctk
import pystray
import winreg

from captioner import VlmCaptioner, format_immich_description
from gpu_monitor import is_system_busy, get_gpu_stats, get_user_idle_seconds
from create_icons import get_tray_icon
from i18n import t, set_language, get_language, get_supported_languages, get_language_name, get_code_by_name

# Configure CustomTkinter
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

def setup_universal_clipboard(root_widget):
    """Enables Ctrl+V, Ctrl+C, Ctrl+X, Ctrl+A in Russian and other keyboard layouts."""
    try:
        root_widget.event_add('<<Paste>>', '<Control-Key-Cyrillic_em>', '<Control-Key-Cyrillic_EM>')
        root_widget.event_add('<<Copy>>', '<Control-Key-Cyrillic_es>', '<Control-Key-Cyrillic_ES>')
        root_widget.event_add('<<Cut>>', '<Control-Key-Cyrillic_che>', '<Control-Key-Cyrillic_CHE>')
        root_widget.event_add('<<SelectAll>>', '<Control-Key-Cyrillic_ef>', '<Control-Key-Cyrillic_EF>')
    except Exception:
        pass

def attach_entry_context_menu(ctk_entry, root):
    """Attaches right-click context menu and keycode-level Ctrl+V/C/X/A handlers to CTkEntry."""
    inner = getattr(ctk_entry, "_entry", ctk_entry)
    
    def on_key_press(event):
        is_ctrl = bool(event.state & 4) or bool(event.state & 0x20000)
        if is_ctrl:
            code = event.keycode
            if code == 86 or getattr(event, 'keysym', '').lower() in ('v', 'cyrillic_em'):
                try:
                    clip = root.clipboard_get()
                    try:
                        inner.delete("sel.first", "sel.last")
                    except Exception:
                        pass
                    inner.insert("insert", clip)
                    return "break"
                except Exception:
                    pass
            elif code == 67 or getattr(event, 'keysym', '').lower() in ('c', 'cyrillic_es'):
                try:
                    sel = inner.selection_get()
                    root.clipboard_clear()
                    root.clipboard_append(sel)
                    return "break"
                except Exception:
                    pass
            elif code == 88 or getattr(event, 'keysym', '').lower() in ('x', 'cyrillic_che'):
                try:
                    sel = inner.selection_get()
                    root.clipboard_clear()
                    root.clipboard_append(sel)
                    inner.delete("sel.first", "sel.last")
                    return "break"
                except Exception:
                    pass
            elif code == 65 or getattr(event, 'keysym', '').lower() in ('a', 'cyrillic_ef'):
                inner.select_range(0, 'end')
                inner.icursor('end')
                return "break"

    inner.bind("<Control-KeyPress>", on_key_press, add="+")
    inner.bind("<KeyPress>", on_key_press, add="+")

    menu = tk.Menu(inner, tearoff=0, bg="#1e293b", fg="#f1f5f9", activebackground="#3b82f6", activeforeground="#ffffff")

    def do_paste():
        try:
            clip = root.clipboard_get()
            try:
                inner.delete("sel.first", "sel.last")
            except Exception:
                pass
            inner.insert("insert", clip)
        except Exception:
            pass

    def do_copy():
        try:
            sel = inner.selection_get()
            root.clipboard_clear()
            root.clipboard_append(sel)
        except Exception:
            pass

    def do_cut():
        try:
            sel = inner.selection_get()
            root.clipboard_clear()
            root.clipboard_append(sel)
            inner.delete("sel.first", "sel.last")
        except Exception:
            pass

    def do_select_all():
        inner.select_range(0, 'end')
        inner.icursor('end')

    def do_clear():
        inner.delete(0, 'end')

    def show_menu(event):
        menu = tk.Menu(inner, tearoff=0, bg="#1e293b", fg="#f8fafc", activebackground="#0284c7", activeforeground="#ffffff")
        menu.add_command(label=t("ctx_paste"), command=do_paste)
        menu.add_command(label=t("ctx_copy"), command=do_copy)
        menu.add_command(label=t("ctx_cut"), command=do_cut)
        menu.add_separator()
        menu.add_command(label=t("ctx_select_all"), command=do_select_all)
        menu.add_command(label=t("ctx_clear"), command=do_clear)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    inner.bind("<Button-3>", show_menu)
    ctk_entry.bind("<Button-3>", show_menu)

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

os.chdir(get_base_dir())

CONFIG_PATH = os.path.join(get_base_dir(), "worker_config.json")
APP_ICON_PATH = os.path.join(get_base_dir(), "app_icon.ico")

REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME = "ImmichCaptionWorker"

DEFAULT_CONFIG = {
    "queue": {
        "base_dir": "\\\\NAS\\CaptionQueue",
        "in_dir_name": "In",
        "out_dir_name": "Out",
        "poll_interval_seconds": 3
    },
    "lm_studio": {
        "url": "http://localhost:1234/v1",
        "model": "qwen2.5-vl-7b-instruct",
        "temperature": 0.2,
        "max_tokens": 350
    },
    "immich_description_mode": "tags_only",
    "caption_language": "ru",
    "tags_language": "en",
    "ui_language": "en",
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
    "worker": {
        "id": "worker-1"
    }
}

def load_config() -> dict:
    base_dir = get_base_dir()
    cfg = DEFAULT_CONFIG.copy()
    
    # 1. Проверяем, есть ли рядом config.json (основной конфиг приложения)
    legacy_path = os.path.join(base_dir, "config.json")
    has_legacy = False
    legacy_cfg = {}
    if os.path.exists(legacy_path):
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                legacy_cfg = json.load(f)
                has_legacy = True
        except Exception as e:
            print(f"Error loading legacy config.json: {e}")

    # Если есть config.json, переносим из него параметры
    if has_legacy:
        q_dir = legacy_cfg.get("metadata_queue", {}).get("dir")
        if q_dir:
            cfg["queue"]["base_dir"] = q_dir
        
        if "lm_studio" in legacy_cfg:
            lm = legacy_cfg["lm_studio"]
            if lm.get("url"):
                cfg["lm_studio"]["url"] = lm["url"]
            if lm.get("model"):
                cfg["lm_studio"]["model"] = lm["model"]
            if "temperature" in lm:
                cfg["lm_studio"]["temperature"] = lm["temperature"]
            if "max_tokens" in lm:
                cfg["lm_studio"]["max_tokens"] = lm["max_tokens"]
        
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

    # 2. Читаем worker_config.json, если он существует
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                w_cfg = json.load(f)
                
            w_q_dir = w_cfg.get("queue", {}).get("base_dir", "")
            if w_q_dir in ("", "\\\\NAS\\CaptionQueue") and has_legacy and legacy_cfg.get("metadata_queue", {}).get("dir"):
                pass  # Сохраняем реальный путь из config.json!
            else:
                if "queue" in w_cfg:
                    cfg["queue"].update(w_cfg["queue"])

            w_model = w_cfg.get("lm_studio", {}).get("model", "")
            if w_model in ("", "qwen2.5-vl-7b-instruct", "qwen/qwen3-vl-8b") and has_legacy and legacy_cfg.get("lm_studio", {}).get("model"):
                if "lm_studio" in w_cfg:
                    temp_lm = w_cfg["lm_studio"].copy()
                    temp_lm["model"] = legacy_cfg["lm_studio"]["model"]
                    cfg["lm_studio"].update(temp_lm)
            else:
                if "lm_studio" in w_cfg:
                    cfg["lm_studio"].update(w_cfg["lm_studio"])

            for k in ("immich_description_mode", "caption_language", "tags_language", "ui_language", "worker"):
                if k in w_cfg:
                    cfg[k] = w_cfg[k]
                    
            if "throttling" in w_cfg:
                cfg["throttling"].update(w_cfg["throttling"])
        except Exception as e:
            print(f"Error loading config: {e}")
    else:
        # Если worker_config.json не существовал, но был config.json - сразу создаем его
        if has_legacy:
            save_config(cfg)

    return cfg

def save_config(cfg: dict) -> bool:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False

def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, APP_REG_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except Exception:
        return False

def set_autostart(enable: bool, start_minimized: bool = True) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                base_dir = get_base_dir()
                exe_file = os.path.join(base_dir, "ImmichCaptionWorker.exe")
                
                if getattr(sys, 'frozen', False):
                    cmd = f'"{sys.executable}"'
                elif os.path.exists(exe_file):
                    cmd = f'"{exe_file}"'
                else:
                    py_dir = os.path.dirname(sys.executable)
                    pythonw = os.path.join(py_dir, "pythonw.exe")
                    exe = pythonw if os.path.exists(pythonw) else sys.executable
                    script = os.path.abspath(os.path.join(base_dir, "ui_worker.py"))
                    cmd = f'"{exe}" "{script}"'

                if start_minimized:
                    cmd += " --minimized"
                winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print(f"Error updating autostart in registry: {e}")
        return False

class WorkerEngineState:
    def __init__(self):
        self.is_running = True
        self.paused_by_user = False
        self.status_text = t("status_init")
        self.status_type = "info"  # "active", "paused", "busy", "error", "info"
        
        # Queue stats
        self.queue_in_count = 0
        self.queue_active_claims = 0
        self.queue_out_count = 0
        self.session_processed = 0
        self.total_processed = 0
        
        # Speed & ETA
        self.recent_times = deque(maxlen=20)
        self.recent_timestamps = deque(maxlen=20)
        self.photos_per_minute = 0.0
        self.photos_per_hour = 0.0
        self.avg_time_per_photo = 0.0
        
        # GPU telemetry (60 seconds)
        self.gpu_history = deque([0] * 60, maxlen=60)
        self.current_gpu_util = 0
        self.current_vram_used = 0
        self.current_vram_total = 12288
        
        # Last photo
        self.current_photo_id = ""
        self.last_photo_id = ""
        self.last_photo_time = 0.0
        self.last_description = t("last_photo_worker_desc")
        self.last_thumbnail_pil = None
        
        # Logs deque for UI
        self.log_lines = deque(maxlen=100)

    def log(self, msg: str):
        timestamp = time.strftime("[%H:%M:%S]")
        self.log_lines.append(f"{timestamp} {msg}")

class QueueWorkerThread(threading.Thread):
    def __init__(self, engine: WorkerEngineState, get_config_func):
        super().__init__(daemon=True)
        self.engine = engine
        self.get_config = get_config_func
        self.current_claim_path = None
        self.current_orig_path = None

    def release_claim(self):
        if self.current_claim_path and os.path.exists(self.current_claim_path) and self.current_orig_path:
            try:
                os.replace(self.current_claim_path, self.current_orig_path)
                self.engine.log(f"↩ Возвращено в очередь: {os.path.basename(self.current_orig_path)}")
            except Exception as e:
                self.engine.log(f"Ошибка освобождения файла: {e}")
            self.current_claim_path = None
            self.current_orig_path = None

    def run(self):
        while self.engine.is_running:
            cfg = self.get_config()
            q_cfg = cfg.get("queue", {})
            lm_cfg = cfg.get("lm_studio", {})
            throttle_cfg = cfg.get("throttling", {})
            worker_id = cfg.get("worker", {}).get("id", "worker")

            base_dir = os.path.abspath(q_cfg.get("base_dir", "./CaptionQueue"))
            in_dir = os.path.join(base_dir, q_cfg.get("in_dir_name", "In"))
            out_dir = os.path.join(base_dir, q_cfg.get("out_dir_name", "Out"))
            poll_interval = float(q_cfg.get("poll_interval_seconds", 3))

            captioner = VlmCaptioner(
                lm_cfg.get("url", "http://localhost:1234/v1"),
                lm_cfg.get("model", "qwen/qwen3-vl-8b"),
                temperature=lm_cfg.get("temperature", 0.2),
                max_tokens=lm_cfg.get("max_tokens", 350)
            )

            # Ensure directories
            try:
                os.makedirs(in_dir, exist_ok=True)
                os.makedirs(out_dir, exist_ok=True)
            except Exception as e:
                self.engine.status_text = f"Ошибка доступа к очереди: {e}"
                self.engine.status_type = "error"
                time.sleep(3)
                continue

            # Check user pause
            if self.engine.paused_by_user:
                self.engine.status_text = t("status_paused_user")
                self.engine.status_type = "paused"
                time.sleep(1)
                continue

            # Check LM Studio
            if not captioner.test_connection():
                self.engine.status_text = t("status_waiting_lm")
                self.engine.status_type = "paused"
                time.sleep(4)
                continue

            # Check Throttling
            busy, reason = is_system_busy(throttle_cfg)
            if busy:
                self.engine.status_text = f"Pause: {reason}" if get_language() != "ru" else f"Пауза: {reason}"
                self.engine.status_type = "busy"
                interval = throttle_cfg.get("check_interval_busy_seconds", 10)
                for _ in range(interval):
                    if not self.engine.is_running or self.engine.paused_by_user:
                        break
                    time.sleep(1)
                continue

            # Scan and count queue
            try:
                in_files = os.listdir(in_dir)
                jpg_list = [f for f in in_files if f.endswith(".jpg") and not f.startswith(".")]
                claim_list = [f for f in in_files if ".claim_" in f and f.endswith(".tmp")]
                self.engine.queue_in_count = len(jpg_list)
                self.engine.queue_active_claims = len(claim_list)
                
                out_files = os.listdir(out_dir)
                self.engine.queue_out_count = len([f for f in out_files if f.endswith(".txt")])
            except Exception as e:
                self.engine.status_text = f"Queue error: {e}" if get_language() != "ru" else f"Ошибка чтения папки очереди: {e}"
                self.engine.status_type = "error"
                time.sleep(3)
                continue

            if not jpg_list:
                self.engine.status_text = t("status_queue_empty")
                self.engine.status_type = "info"
                time.sleep(poll_interval)
                continue

            # Claim next image
            claimed_id = None
            for fname in jpg_list:
                asset_id = fname[:-4]
                orig_path = os.path.join(in_dir, fname)
                claim_name = f"{asset_id}.claim_{worker_id}_{int(time.time())}.tmp"
                claim_path = os.path.join(in_dir, claim_name)
                try:
                    os.rename(orig_path, claim_path)
                    self.current_claim_path = claim_path
                    self.current_orig_path = orig_path
                    claimed_id = asset_id
                    break
                except (OSError, FileNotFoundError):
                    continue

            if not claimed_id:
                time.sleep(1)
                continue

            # Start processing claimed photo
            self.engine.current_photo_id = claimed_id
            self.engine.status_text = t("status_processing_photo", id=claimed_id)
            self.engine.status_type = "active"
            self.engine.log(f"Взят в работу: {claimed_id}")

            t0 = time.time()
            try:
                with open(self.current_claim_path, "rb") as f:
                    raw_bytes = f.read()

                b64_img = base64.b64encode(raw_bytes).decode('utf-8')

                # Create thumbnail for UI preview
                try:
                    pil_img = Image.open(io.BytesIO(raw_bytes))
                    pil_img.thumbnail((160, 160))
                    self.engine.last_thumbnail_pil = pil_img
                except Exception:
                    pass

                # Check throttling again right before inference
                busy, reason = is_system_busy(throttle_cfg)
                while busy and self.engine.is_running and not self.engine.paused_by_user:
                    self.engine.status_text = f"Пауза перед инференсом: {reason}"
                    self.engine.status_type = "busy"
                    time.sleep(throttle_cfg.get("check_interval_busy_seconds", 10))
                    busy, reason = is_system_busy(throttle_cfg)

                if not self.engine.is_running or self.engine.paused_by_user:
                    self.release_claim()
                    break

                # Generate caption via VLM with configured languages
                cap_lang = cfg.get("caption_language", "ru")
                tags_lang = cfg.get("tags_language", "en")
                caption_res = captioner.generate_caption(b64_img, desc_lang=cap_lang, tags_lang=tags_lang)

                if isinstance(caption_res, dict):
                    immich_mode = cfg.get("immich_description_mode", "tags_only")
                    description = format_immich_description(caption_res, mode=immich_mode, desc_lang=cap_lang)
                else:
                    description = str(caption_res).strip()

                if description:
                    # Write to Out/ atomically
                    out_part = os.path.join(out_dir, f"{claimed_id}.part")
                    out_final = os.path.join(out_dir, f"{claimed_id}.txt")

                    with open(out_part, "w", encoding="utf-8") as out_f:
                        out_f.write(description)

                    os.replace(out_part, out_final)

                    # Remove claim file from In/
                    try:
                        os.remove(self.current_claim_path)
                    except Exception:
                        pass

                    self.current_claim_path = None
                    self.current_orig_path = None

                    elapsed = time.time() - t0
                    self.engine.session_processed += 1
                    self.engine.total_processed += 1
                    self.engine.last_photo_id = claimed_id
                    self.engine.last_photo_time = elapsed
                    self.engine.last_description = description

                    self.engine.recent_times.append(elapsed)
                    self.engine.recent_timestamps.append(time.time())
                    self._recalc_speeds()

                    short_desc = description.replace('\n', ' ')
                    if len(short_desc) > 75:
                        short_desc = short_desc[:72] + "..."
                    self.engine.log(f"Готово [{claimed_id}] за {elapsed:.1f}с: {short_desc}")
                    self.engine.status_text = t("status_done_photo", id=claimed_id, elapsed=elapsed)
                    self.engine.status_type = "active"
                else:
                    self.engine.log(f"Пустой ответ модели для [{claimed_id}]! Возврат в очередь.")
                    self.release_claim()

            except Exception as e:
                self.engine.log(f"Ошибка обработки [{claimed_id}]: {e}")
                self.engine.status_text = f"Ошибка [{claimed_id}]: {e}"
                self.engine.status_type = "error"
                self.release_claim()

            # Delay between photos
            rest = throttle_cfg.get("idle_delay_between_photos_seconds", 1)
            time.sleep(rest)

        self.release_claim()

    def _recalc_speeds(self):
        if len(self.engine.recent_times) > 0:
            avg_sec = sum(self.engine.recent_times) / len(self.engine.recent_times)
            self.engine.avg_time_per_photo = avg_sec
            if avg_sec > 0:
                self.engine.photos_per_minute = 60.0 / avg_sec
                self.engine.photos_per_hour = 3600.0 / avg_sec


class WorkerTelemetryThread(threading.Thread):
    def __init__(self, engine: WorkerEngineState):
        super().__init__(daemon=True)
        self.engine = engine

    def run(self):
        while self.engine.is_running:
            util, used, total = get_gpu_stats()
            self.engine.current_gpu_util = util
            self.engine.current_vram_used = used
            self.engine.current_vram_total = total
            self.engine.gpu_history.append(util)
            time.sleep(1)


class WorkerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config = load_config()
        saved_lang = self.config.get("ui_language")
        if saved_lang:
            set_language(saved_lang)

        self.title(t("worker_app_title"))
        self.geometry("920x750")
        self.minsize(840, 660)

        if os.path.exists(APP_ICON_PATH):
            try:
                self.iconbitmap(APP_ICON_PATH)
            except Exception:
                pass

        self.engine = WorkerEngineState()
        setup_universal_clipboard(self)

        # Start background threads
        self.worker_thread = QueueWorkerThread(self.engine, lambda: self.config)
        self.worker_thread.start()

        self.telemetry_thread = WorkerTelemetryThread(self.engine)
        self.telemetry_thread.start()

        # Build UI
        self._build_ui()

        # Tray Setup
        self._setup_tray()

        # Window protocols
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        if "--minimized" in sys.argv:
            self.withdraw()

        # UI loop
        self.after(200, self._update_ui_loop)

    def _build_ui(self):
        # 1. Header Frame
        header = ctk.CTkFrame(self, corner_radius=12, fg_color="#181e29")
        header.pack(fill="x", padx=16, pady=(16, 8))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=16, pady=12)

        self.title_lbl = ctk.CTkLabel(
            title_box, 
            text=t("worker_header_title"), 
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#38bdf8"
        )
        self.title_lbl.pack(anchor="w")

        worker_id = self.config.get("worker", {}).get("id", "worker")
        model = self.config.get("lm_studio", {}).get("model", "qwen")
        self.subtitle_lbl = ctk.CTkLabel(
            title_box,
            text=t("worker_subtitle", id=worker_id, model=model),
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8",
            wraplength=480,
            justify="left"
        )
        self.subtitle_lbl.pack(anchor="w")

        # Action Button (Start / Pause)
        self.btn_pause = ctk.CTkButton(
            header,
            text=t("btn_pause"),
            font=ctk.CTkFont(size=14, weight="bold"),
            width=120,
            height=38,
            fg_color="#f59e0b",
            hover_color="#d97706",
            command=self.toggle_pause
        )
        self.btn_pause.pack(side="right", padx=16, pady=12)

        # Autostart Checkbox
        self.autostart_var = ctk.BooleanVar(value=is_autostart_enabled())
        self.chk_autostart = ctk.CTkCheckBox(
            header,
            text=t("autostart"),
            variable=self.autostart_var,
            command=self.toggle_autostart,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#94a3b8",
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            border_width=2,
            fg_color="#0284c7",
            hover_color="#0369a1"
        )
        self.chk_autostart.pack(side="right", padx=(8, 14), pady=12)

        # UI Language Dropdown [ 🌐 English ▾ ]
        self.lang_var = ctk.StringVar(value=get_language_name(get_language()))
        self.lang_opt = ctk.CTkOptionMenu(
            header,
            values=list(get_supported_languages().values()),
            variable=self.lang_var,
            command=self.change_language,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=120,
            height=28,
            fg_color="#334155",
            button_color="#475569",
            button_hover_color="#1e293b",
            dropdown_fg_color="#1e293b"
        )
        self.lang_opt.pack(side="right", padx=(6, 12), pady=12)

        # --- GENERATION & IMMICH SETTINGS BAR ---
        settings_bar = ctk.CTkFrame(self, corner_radius=10, fg_color="#181e29")
        settings_bar.pack(fill="x", padx=16, pady=(0, 6))

        # Description Language
        self.caption_lang_lbl = ctk.CTkLabel(
            settings_bar, 
            text=t("lang_caption_label"), 
            font=ctk.CTkFont(size=11, weight="bold"), 
            text_color="#94a3b8"
        )
        self.caption_lang_lbl.pack(side="left", padx=(14, 4), pady=6)

        curr_cap_lang = self.config.get("caption_language", "ru")
        self.caption_lang_var = ctk.StringVar(value=get_language_name(curr_cap_lang))
        self.caption_lang_opt = ctk.CTkOptionMenu(
            settings_bar,
            values=list(get_supported_languages().values()),
            variable=self.caption_lang_var,
            command=self.on_caption_lang_change,
            font=ctk.CTkFont(size=11),
            width=115,
            height=28,
            fg_color="#334155",
            button_color="#475569",
            button_hover_color="#1e293b",
            dropdown_fg_color="#1e293b"
        )
        self.caption_lang_opt.pack(side="left", padx=(0, 10), pady=6)

        # Tags Language
        self.tags_lang_lbl = ctk.CTkLabel(
            settings_bar, 
            text=t("lang_tags_label"), 
            font=ctk.CTkFont(size=11, weight="bold"), 
            text_color="#94a3b8"
        )
        self.tags_lang_lbl.pack(side="left", padx=(4, 4), pady=6)

        curr_tags_lang = self.config.get("tags_language", "en")
        self.tags_lang_var = ctk.StringVar(value=get_language_name(curr_tags_lang))
        self.tags_lang_opt = ctk.CTkOptionMenu(
            settings_bar,
            values=list(get_supported_languages().values()),
            variable=self.tags_lang_var,
            command=self.on_tags_lang_change,
            font=ctk.CTkFont(size=11),
            width=115,
            height=28,
            fg_color="#334155",
            button_color="#475569",
            button_hover_color="#1e293b",
            dropdown_fg_color="#1e293b"
        )
        self.tags_lang_opt.pack(side="left", padx=(0, 10), pady=6)

        # Status Badge (positioned centrally in settings_bar)
        self.status_badge = ctk.CTkLabel(
            settings_bar,
            text=f"● {t('status_init')}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#34d399",
            padx=10,
            pady=4
        )
        self.status_badge.pack(side="left", padx=10, pady=6)

        # Write to Immich Mode (on the right)
        self.mode_map_inv = {
            "tags_only": t("desc_mode_tags_only"), 
            "title_and_tags": t("desc_mode_title_and_tags"), 
            "full": t("desc_mode_full")
        }
        curr_mode = self.config.get("immich_description_mode", "tags_only")
        self.desc_mode_opt = ctk.CTkOptionMenu(
            settings_bar,
            values=[t("desc_mode_tags_only"), t("desc_mode_title_and_tags"), t("desc_mode_full")],
            font=ctk.CTkFont(size=11),
            width=140,
            height=28,
            fg_color="#334155",
            button_color="#475569",
            button_hover_color="#1e293b",
            dropdown_fg_color="#1e293b",
            command=self.on_desc_mode_change
        )
        self.desc_mode_opt.set(self.mode_map_inv.get(curr_mode, t("desc_mode_tags_only")))
        self.desc_mode_opt.pack(side="right", padx=(4, 12), pady=6)

        self.mode_lbl = ctk.CTkLabel(
            settings_bar, 
            text=t("desc_mode_label"), 
            font=ctk.CTkFont(size=11), 
            text_color="#94a3b8"
        )
        self.mode_lbl.pack(side="right", padx=(4, 2), pady=6)

        # 2. Main Tabs View (Дашборд / Настройки / Лог)
        self.tabview = ctk.CTkTabview(self, corner_radius=12, fg_color="#131720")
        self.tabview.pack(fill="both", expand=True, padx=16, pady=6)

        self.tab_dash = self.tabview.add(t("tab_dashboard"))
        self.tab_settings = self.tabview.add(t("tab_settings"))
        self.tab_logs = self.tabview.add(t("tab_logs"))

        self._build_dashboard_tab()
        self._build_settings_tab()
        self._build_logs_tab()

    def _build_dashboard_tab(self):
        # Stats Cards
        stats_frame = ctk.CTkFrame(self.tab_dash, fg_color="transparent")
        stats_frame.pack(fill="x", padx=8, pady=(4, 6))
        stats_frame.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stats")

        self.card_in_queue = self._create_card(stats_frame, 0, t("card_in_queue"), "0", t("card_in_queue_sub"))
        self.card_processed = self._create_card(stats_frame, 1, t("card_worker_processed"), "0", t("card_session", count=0))
        self.card_speed = self._create_card(stats_frame, 2, t("card_speed"), "~0", "--")
        self.card_hour = self._create_card(stats_frame, 3, t("card_throughput"), "~0", t("card_active_claims", count=0))

        # GPU Monitor Box
        gpu_box = ctk.CTkFrame(self.tab_dash, corner_radius=10, fg_color="#181e29")
        gpu_box.pack(fill="both", expand=True, padx=8, pady=4)

        gpu_header = ctk.CTkFrame(gpu_box, fg_color="transparent")
        gpu_header.pack(fill="x", padx=14, pady=(8, 4))

        self.gpu_title_lbl = ctk.CTkLabel(
            gpu_header,
            text=t("gpu_load", gpu="GPU", util=0),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#e2e8f0"
        )
        self.gpu_title_lbl.pack(side="left")

        self.vram_lbl = ctk.CTkLabel(
            gpu_header,
            text="VRAM: 0 / 0 MB",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        )
        self.vram_lbl.pack(side="right")

        self.gpu_canvas = ctk.CTkCanvas(
            gpu_box,
            height=110,
            bg="#0f131a",
            highlightthickness=0
        )
        self.gpu_canvas.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        # Bottom Preview of Last Processed Photo
        last_frame = ctk.CTkFrame(self.tab_dash, corner_radius=10, fg_color="#181e29")
        last_frame.pack(fill="x", padx=8, pady=(4, 8))

        self.last_title = ctk.CTkLabel(
            last_frame,
            text=t("last_photo_worker_title"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#cbd5e1"
        )
        self.last_title.pack(anchor="w", padx=14, pady=(8, 2))

        content_box = ctk.CTkFrame(last_frame, fg_color="transparent")
        content_box.pack(fill="x", padx=14, pady=(0, 10))

        self.thumb_label = ctk.CTkLabel(
            content_box,
            text=t("preview_placeholder"),
            width=120,
            height=90,
            fg_color="#0f131a",
            corner_radius=8
        )
        self.thumb_label.pack(side="left", padx=(0, 12))

        desc_box = ctk.CTkFrame(content_box, fg_color="transparent")
        desc_box.pack(side="left", fill="both", expand=True)

        self.photo_info_lbl = ctk.CTkLabel(
            desc_box,
            text=t("last_photo_worker_waiting"),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#38bdf8",
            anchor="w"
        )
        self.photo_info_lbl.pack(anchor="w")

        self.photo_desc_lbl = ctk.CTkLabel(
            desc_box,
            text=t("last_photo_worker_desc"),
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
            justify="left",
            wraplength=660,
            anchor="w"
        )
        self.photo_desc_lbl.pack(anchor="w", pady=(2, 0))

    def _create_card(self, parent, col, title, main_val, sub_val):
        card = ctk.CTkFrame(parent, corner_radius=10, fg_color="#181e29")
        card.grid(row=0, column=col, padx=4, pady=4, sticky="nsew")

        lbl_t = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=10, weight="bold"), text_color="#64748b")
        lbl_t.pack(anchor="w", padx=12, pady=(8, 0))

        lbl_v = ctk.CTkLabel(card, text=main_val, font=ctk.CTkFont(size=17, weight="bold"), text_color="#f8fafc")
        lbl_v.pack(anchor="w", padx=12, pady=(2, 0))

        lbl_s = ctk.CTkLabel(card, text=sub_val, font=ctk.CTkFont(size=11), text_color="#94a3b8")
        lbl_s.pack(anchor="w", padx=12, pady=(0, 8))

        return {"title": lbl_t, "main": lbl_v, "sub": lbl_s}

    def _build_settings_tab(self):
        scroll = ctk.CTkScrollableFrame(self.tab_settings, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        # Group 0: AI Languages & Output format
        g0 = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#181e29")
        g0.pack(fill="x", pady=6)
        self.lbl_g0_title = ctk.CTkLabel(g0, text="🌐 " + t("settings_group_ai"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8")
        self.lbl_g0_title.pack(anchor="w", padx=14, pady=(10, 6))

        # Description Language
        row_clang = ctk.CTkFrame(g0, fg_color="transparent")
        row_clang.pack(fill="x", padx=14, pady=4)
        self.lbl_row_clang = ctk.CTkLabel(row_clang, text=t("lang_caption_label"), width=220, anchor="w")
        self.lbl_row_clang.pack(side="left")
        self.entry_cap_lang_opt = ctk.CTkOptionMenu(
            row_clang,
            values=list(get_supported_languages().values()),
            variable=self.caption_lang_var,
            command=self.on_caption_lang_change,
            width=160
        )
        self.entry_cap_lang_opt.pack(side="left", padx=4)

        # Tags Language
        row_tlang = ctk.CTkFrame(g0, fg_color="transparent")
        row_tlang.pack(fill="x", padx=14, pady=4)
        self.lbl_row_tlang = ctk.CTkLabel(row_tlang, text=t("lang_tags_label"), width=220, anchor="w")
        self.lbl_row_tlang.pack(side="left")
        self.entry_tags_lang_opt = ctk.CTkOptionMenu(
            row_tlang,
            values=list(get_supported_languages().values()),
            variable=self.tags_lang_var,
            command=self.on_tags_lang_change,
            width=160
        )
        self.entry_tags_lang_opt.pack(side="left", padx=4)

        # Output format mode
        row_mode = ctk.CTkFrame(g0, fg_color="transparent")
        row_mode.pack(fill="x", padx=14, pady=(4, 10))
        self.lbl_row_mode = ctk.CTkLabel(row_mode, text=t("desc_mode_label"), width=220, anchor="w")
        self.lbl_row_mode.pack(side="left")
        self.entry_mode_opt = ctk.CTkOptionMenu(
            row_mode,
            values=[t("desc_mode_tags_only"), t("desc_mode_title_and_tags"), t("desc_mode_full")],
            command=self.on_desc_mode_change,
            width=160
        )
        self.entry_mode_opt.set(self.mode_map_inv.get(self.config.get("immich_description_mode", "tags_only"), t("desc_mode_tags_only")))
        self.entry_mode_opt.pack(side="left", padx=4)

        # Group 1: Queue Config
        g1 = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#181e29")
        g1.pack(fill="x", pady=6)
        self.lbl_g1_title = ctk.CTkLabel(g1, text="📁 " + t("settings_group_queue"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8")
        self.lbl_g1_title.pack(anchor="w", padx=14, pady=(10, 6))

        # Base Dir
        row_dir = ctk.CTkFrame(g1, fg_color="transparent")
        row_dir.pack(fill="x", padx=14, pady=4)
        self.lbl_queue_path = ctk.CTkLabel(row_dir, text=t("setting_queue_dir"), width=240, anchor="w")
        self.lbl_queue_path.pack(side="left")
        self.entry_queue_dir = ctk.CTkEntry(row_dir, placeholder_text="\\\\NAS\\CaptionQueue")
        self.entry_queue_dir.pack(side="left", fill="x", expand=True, padx=(4, 8))
        self.entry_queue_dir.insert(0, self.config.get("queue", {}).get("base_dir", ""))
        attach_entry_context_menu(self.entry_queue_dir, self)
        self.btn_browse = ctk.CTkButton(row_dir, text=t("btn_browse"), width=85, command=self._browse_queue_dir)
        self.btn_browse.pack(side="right")

        # Worker ID
        row_wid = ctk.CTkFrame(g1, fg_color="transparent")
        row_wid.pack(fill="x", padx=14, pady=4)
        self.lbl_worker_id = ctk.CTkLabel(row_wid, text=t("setting_worker_id"), width=240, anchor="w")
        self.lbl_worker_id.pack(side="left")
        self.entry_worker_id = ctk.CTkEntry(row_wid)
        self.entry_worker_id.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.entry_worker_id.insert(0, self.config.get("worker", {}).get("id", "worker-1"))
        attach_entry_context_menu(self.entry_worker_id, self)

        # Poll Interval
        row_poll = ctk.CTkFrame(g1, fg_color="transparent")
        row_poll.pack(fill="x", padx=14, pady=(4, 10))
        self.lbl_poll_interval = ctk.CTkLabel(row_poll, text=t("setting_poll_interval"), width=240, anchor="w")
        self.lbl_poll_interval.pack(side="left")
        self.entry_poll_sec = ctk.CTkEntry(row_poll, width=100)
        self.entry_poll_sec.pack(side="left", padx=(4, 0))
        self.entry_poll_sec.insert(0, str(self.config.get("queue", {}).get("poll_interval_seconds", 3)))
        attach_entry_context_menu(self.entry_poll_sec, self)

        # Group 2: LM Studio
        g2 = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#181e29")
        g2.pack(fill="x", pady=6)
        self.lbl_g2_title = ctk.CTkLabel(g2, text="🤖 " + t("settings_group_lm"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8")
        self.lbl_g2_title.pack(anchor="w", padx=14, pady=(10, 6))

        row_lm_url = ctk.CTkFrame(g2, fg_color="transparent")
        row_lm_url.pack(fill="x", padx=14, pady=4)
        self.lbl_lm_url = ctk.CTkLabel(row_lm_url, text=t("setting_lm_url"), width=240, anchor="w")
        self.lbl_lm_url.pack(side="left")
        self.entry_lm_url = ctk.CTkEntry(row_lm_url)
        self.entry_lm_url.pack(side="left", fill="x", expand=True, padx=(4, 8))
        self.entry_lm_url.insert(0, self.config.get("lm_studio", {}).get("url", "http://localhost:1234/v1"))
        attach_entry_context_menu(self.entry_lm_url, self)
        
        self.btn_test_lm = ctk.CTkButton(row_lm_url, text=t("btn_test_conn"), width=110, fg_color="#0284c7", command=self._test_lm_connection)
        self.btn_test_lm.pack(side="right")

        row_lm_mod = ctk.CTkFrame(g2, fg_color="transparent")
        row_lm_mod.pack(fill="x", padx=14, pady=(4, 10))
        self.lbl_lm_model = ctk.CTkLabel(row_lm_mod, text=t("setting_lm_model"), width=240, anchor="w")
        self.lbl_lm_model.pack(side="left")
        self.entry_lm_model = ctk.CTkEntry(row_lm_mod)
        self.entry_lm_model.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.entry_lm_model.insert(0, self.config.get("lm_studio", {}).get("model", "qwen/qwen3-vl-8b"))
        attach_entry_context_menu(self.entry_lm_model, self)

        # Group 3: Throttling & Priority
        g3 = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#181e29")
        g3.pack(fill="x", pady=6)
        self.lbl_g3_title = ctk.CTkLabel(g3, text="⚡ " + t("settings_group_throttle"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8")
        self.lbl_g3_title.pack(anchor="w", padx=14, pady=(10, 6))

        row_th1 = ctk.CTkFrame(g3, fg_color="transparent")
        row_th1.pack(fill="x", padx=14, pady=4)
        self.lbl_max_gpu = ctk.CTkLabel(row_th1, text=t("setting_max_gpu"), width=250, anchor="w")
        self.lbl_max_gpu.pack(side="left")
        self.entry_max_gpu = ctk.CTkEntry(row_th1, width=80)
        self.entry_max_gpu.pack(side="left", padx=4)
        self.entry_max_gpu.insert(0, str(self.config.get("throttling", {}).get("max_gpu_util_percent", 35)))
        attach_entry_context_menu(self.entry_max_gpu, self)

        row_th2 = ctk.CTkFrame(g3, fg_color="transparent")
        row_th2.pack(fill="x", padx=14, pady=4)
        self.lbl_delay = ctk.CTkLabel(row_th2, text=t("setting_photo_delay"), width=250, anchor="w")
        self.lbl_delay.pack(side="left")
        self.entry_delay = ctk.CTkEntry(row_th2, width=80)
        self.entry_delay.pack(side="left", padx=4)
        self.entry_delay.insert(0, str(self.config.get("throttling", {}).get("idle_delay_between_photos_seconds", 1)))
        attach_entry_context_menu(self.entry_delay, self)

        row_th3 = ctk.CTkFrame(g3, fg_color="transparent")
        row_th3.pack(fill="x", padx=14, pady=(4, 10))
        self.lbl_heavy = ctk.CTkLabel(row_th3, text=t("setting_heavy_processes"), width=250, anchor="w")
        self.lbl_heavy.pack(side="left")
        self.entry_heavy = ctk.CTkEntry(row_th3)
        self.entry_heavy.pack(side="left", fill="x", expand=True, padx=(4, 0))
        heavy_list = self.config.get("throttling", {}).get("heavy_processes", ["cyberpunk2077.exe", "blender.exe"])
        self.entry_heavy.insert(0, ", ".join(heavy_list))
        attach_entry_context_menu(self.entry_heavy, self)

        # Action Buttons: Save and Import from config.json
        btns_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        btns_frame.pack(fill="x", pady=(10, 16))

        self.btn_save = ctk.CTkButton(
            btns_frame,
            text=t("btn_save_settings"),
            font=ctk.CTkFont(size=14, weight="bold"),
            height=40,
            fg_color="#10b981",
            hover_color="#059669",
            command=self._save_settings
        )
        self.btn_save.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_import = ctk.CTkButton(
            btns_frame,
            text=t("btn_import_config"),
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40,
            fg_color="#334155",
            hover_color="#475569",
            command=self._import_from_config_json
        )
        self.btn_import.pack(side="right", fill="x", expand=True, padx=(6, 0))

    def on_caption_lang_change(self, choice: str):
        code = get_code_by_name(choice)
        self.config["caption_language"] = code
        self.caption_lang_var.set(choice)
        save_config(self.config)
        self.status_badge.configure(text=f"● {t('lang_caption_label')} {choice}", text_color="#38bdf8")

    def on_tags_lang_change(self, choice: str):
        code = get_code_by_name(choice)
        self.config["tags_language"] = code
        self.tags_lang_var.set(choice)
        save_config(self.config)
        self.status_badge.configure(text=f"● {t('lang_tags_label')} {choice}", text_color="#38bdf8")

    def on_desc_mode_change(self, choice: str):
        rev = {v: k for k, v in self.mode_map_inv.items()}
        m = rev.get(choice, "tags_only")
        self.config["immich_description_mode"] = m
        self.desc_mode_opt.set(choice)
        if hasattr(self, 'entry_mode_opt'):
            self.entry_mode_opt.set(choice)
        save_config(self.config)
        self.status_badge.configure(text=f"● {t('desc_mode_label')} {choice}", text_color="#38bdf8")

    def change_language(self, choice: str):
        lang = get_code_by_name(choice)
        set_language(lang)
        self.config["ui_language"] = lang
        save_config(self.config)
        self._refresh_language_texts()

    def _refresh_language_texts(self):
        self.title(t("worker_app_title"))
        self.title_lbl.configure(text=t("worker_header_title"))
        worker_id = self.config.get("worker", {}).get("id", "worker")
        model = self.config.get("lm_studio", {}).get("model", "qwen")
        self.subtitle_lbl.configure(text=t("worker_subtitle", id=worker_id, model=model))
        self.btn_pause.configure(text=t("btn_start") if self.engine.paused_by_user else t("btn_pause"))
        self.chk_autostart.configure(text=t("autostart"))
        self.lang_opt.set(get_language_name(get_language()))

        # Settings bar
        self.caption_lang_lbl.configure(text=t("lang_caption_label"))
        self.tags_lang_lbl.configure(text=t("lang_tags_label"))
        self.mode_lbl.configure(text=t("desc_mode_label"))

        # Re-map description dropdown
        self.mode_map_inv = {
            "tags_only": t("desc_mode_tags_only"), 
            "title_and_tags": t("desc_mode_title_and_tags"), 
            "full": t("desc_mode_full")
        }
        curr_mode = self.config.get("immich_description_mode", "tags_only")
        modes_list = [t("desc_mode_tags_only"), t("desc_mode_title_and_tags"), t("desc_mode_full")]
        self.desc_mode_opt.configure(values=modes_list)
        self.desc_mode_opt.set(self.mode_map_inv.get(curr_mode, t("desc_mode_tags_only")))
        if hasattr(self, 'entry_mode_opt'):
            self.entry_mode_opt.configure(values=modes_list)
            self.entry_mode_opt.set(self.mode_map_inv.get(curr_mode, t("desc_mode_tags_only")))

        # Cards titles
        self.card_in_queue["title"].configure(text=t("card_in_queue"))
        self.card_in_queue["sub"].configure(text=t("card_in_queue_sub"))
        self.card_processed["title"].configure(text=t("card_worker_processed"))
        self.card_speed["title"].configure(text=t("card_speed"))
        self.card_hour["title"].configure(text=t("card_throughput"))

        # Last photo
        self.last_title.configure(text=t("last_photo_worker_title"))
        if self.engine.last_photo_id in ("Нет данных", "No data", ""):
            self.photo_info_lbl.configure(text=t("last_photo_worker_waiting"))
            self.photo_desc_lbl.configure(text=t("last_photo_worker_desc"))
        self.thumb_label.configure(text=t("preview_placeholder"))

        # Settings tab labels
        if hasattr(self, 'lbl_g0_title'):
            self.lbl_g0_title.configure(text="🌐 " + t("settings_group_ai"))
            self.lbl_row_clang.configure(text=t("lang_caption_label"))
            self.lbl_row_tlang.configure(text=t("lang_tags_label"))
            self.lbl_row_mode.configure(text=t("desc_mode_label"))
            self.lbl_g1_title.configure(text="📁 " + t("settings_group_queue"))
            self.lbl_g2_title.configure(text="🤖 " + t("settings_group_lm"))
            self.lbl_g3_title.configure(text="⚡ " + t("settings_group_throttle"))
            self.btn_save.configure(text=t("btn_save_settings"))
            if hasattr(self, 'btn_import'):
                self.btn_import.configure(text=t("btn_import_config"))

            # Field labels & buttons
            if hasattr(self, 'lbl_queue_path'):
                self.lbl_queue_path.configure(text=t("setting_queue_dir"))
                self.btn_browse.configure(text=t("btn_browse"))
                self.lbl_worker_id.configure(text=t("setting_worker_id"))
                self.lbl_poll_interval.configure(text=t("setting_poll_interval"))
                self.lbl_lm_url.configure(text=t("setting_lm_url"))
                self.btn_test_lm.configure(text=t("btn_test_conn"))
                self.lbl_lm_model.configure(text=t("setting_lm_model"))
                self.lbl_max_gpu.configure(text=t("setting_max_gpu"))
                self.lbl_delay.configure(text=t("setting_photo_delay"))
                self.lbl_heavy.configure(text=t("setting_heavy_processes"))

        # Update CTkTabview tab button texts
        try:
            old_tabs = list(self.tabview._tab_dict.keys())
            new_names = [t("tab_dashboard"), t("tab_settings"), t("tab_logs")]
            for k, new_text in zip(old_tabs, new_names):
                if k in self.tabview._segmented_button._buttons_dict:
                    self.tabview._segmented_button._buttons_dict[k].configure(text=new_text)
        except Exception:
            pass

    def _browse_queue_dir(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(initialdir=self.entry_queue_dir.get() or ".")
        if path:
            self.entry_queue_dir.delete(0, "end")
            self.entry_queue_dir.insert(0, os.path.normpath(path))

    def _test_lm_connection(self):
        url = self.entry_lm_url.get().strip()
        mod = self.entry_lm_model.get().strip()
        self.btn_test_lm.configure(text=t("test_conn_testing"), state="disabled")
        
        def run_test():
            captioner = VlmCaptioner(url, mod)
            ok = captioner.test_connection()
            if ok:
                self.after(0, lambda: self.btn_test_lm.configure(text=t("test_conn_success"), fg_color="#10b981", state="normal"))
            else:
                self.after(0, lambda: self.btn_test_lm.configure(text=t("test_conn_error"), fg_color="#ef4444", state="normal"))
            self.after(3000, lambda: self.btn_test_lm.configure(text=t("btn_test_conn"), fg_color="#0284c7"))

        threading.Thread(target=run_test, daemon=True).start()

    def _save_settings(self):
        try:
            self.config["queue"]["base_dir"] = self.entry_queue_dir.get().strip()
            self.config["worker"]["id"] = self.entry_worker_id.get().strip()
            self.config["queue"]["poll_interval_seconds"] = float(self.entry_poll_sec.get().strip())
            
            self.config["lm_studio"]["url"] = self.entry_lm_url.get().strip()
            self.config["lm_studio"]["model"] = self.entry_lm_model.get().strip()
            
            self.config["throttling"]["max_gpu_util_percent"] = int(self.entry_max_gpu.get().strip())
            self.config["throttling"]["idle_delay_between_photos_seconds"] = float(self.entry_delay.get().strip())
            
            heavy_raw = self.entry_heavy.get().strip()
            procs = [p.strip() for p in heavy_raw.split(",") if p.strip()]
            self.config["throttling"]["heavy_processes"] = procs

            # AI Languages
            self.config["caption_language"] = get_code_by_name(self.caption_lang_var.get())
            self.config["tags_language"] = get_code_by_name(self.tags_lang_var.get())
            rev = {v: k for k, v in self.mode_map_inv.items()}
            self.config["immich_description_mode"] = rev.get(self.desc_mode_opt.get(), "tags_only")

            save_config(self.config)
            
            # Update subtitle
            worker_id = self.config.get("worker", {}).get("id", "worker")
            model = self.config.get("lm_studio", {}).get("model", "qwen")
            self.subtitle_lbl.configure(text=t("worker_subtitle", id=worker_id, model=model))
            
            self.engine.log(t("log_settings_saved"))
        except Exception as e:
            self.engine.log(f"Error saving settings: {e}")

    def _import_from_config_json(self):
        base_dir = get_base_dir()
        legacy_path = os.path.join(base_dir, "config.json")
        if not os.path.exists(legacy_path):
            self.engine.log(f"⚠️ Файл {legacy_path} не найден для импорта.")
            return

        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                legacy = json.load(f)

            q_dir = legacy.get("metadata_queue", {}).get("dir")
            if q_dir:
                self.entry_queue_dir.delete(0, "end")
                self.entry_queue_dir.insert(0, q_dir)

            lm = legacy.get("lm_studio", {})
            if lm.get("url"):
                self.entry_lm_url.delete(0, "end")
                self.entry_lm_url.insert(0, lm["url"])
            if lm.get("model"):
                self.entry_lm_model.delete(0, "end")
                self.entry_lm_model.insert(0, lm["model"])

            th = legacy.get("throttling", {})
            if "max_gpu_util_percent" in th:
                self.entry_max_gpu.delete(0, "end")
                self.entry_max_gpu.insert(0, str(th["max_gpu_util_percent"]))
            if "idle_delay_between_photos_seconds" in th:
                self.entry_delay.delete(0, "end")
                self.entry_delay.insert(0, str(th["idle_delay_between_photos_seconds"]))
            if "heavy_processes" in th:
                self.entry_heavy.delete(0, "end")
                self.entry_heavy.insert(0, ", ".join(th["heavy_processes"]))

            if "caption_language" in legacy:
                code = legacy["caption_language"]
                name = get_language_name(code)
                self.caption_lang_var.set(name)
                self.caption_lang_opt.set(name)
            if "tags_language" in legacy:
                code = legacy["tags_language"]
                name = get_language_name(code)
                self.tags_lang_var.set(name)
                self.tags_lang_opt.set(name)
            if "immich_description_mode" in legacy:
                m = legacy["immich_description_mode"]
                name = self.mode_map_inv.get(m, t("desc_mode_tags_only"))
                self.desc_mode_opt.set(name)
                if hasattr(self, 'entry_mode_opt'):
                    self.entry_mode_opt.set(name)

            self._save_settings()
            self.engine.log("✓ " + t("log_settings_imported"))
            self.status_badge.configure(text="● " + t("status_imported"), text_color="#10b981")
        except Exception as e:
            self.engine.log(f"Error importing from config.json: {e}")

    def _build_logs_tab(self):
        log_box = ctk.CTkFrame(self.tab_logs, corner_radius=10, fg_color="#181e29")
        log_box.pack(fill="both", expand=True, padx=8, pady=8)

        self.txt_logs = ctk.CTkTextbox(log_box, font=ctk.CTkFont(family="Consolas", size=11), fg_color="#0f131a", text_color="#cbd5e1")
        self.txt_logs.pack(fill="both", expand=True, padx=8, pady=8)

    def _setup_tray(self):
        def on_open(icon, item):
            self.after(0, self.show_from_tray)

        def on_toggle_pause(icon, item):
            self.after(0, self.toggle_pause)

        def on_toggle_autostart(icon, item):
            new_val = not is_autostart_enabled()
            set_autostart(new_val)
            self.after(0, lambda: self.autostart_var.set(new_val))

        def on_exit(icon, item):
            self.engine.is_running = False
            icon.stop()
            self.after(0, self.destroy)

        self.tray_menu = pystray.Menu(
            pystray.MenuItem(t("tray_open"), on_open, default=True),
            pystray.MenuItem(lambda text: t("tray_resume") if self.engine.paused_by_user else t("tray_pause"), on_toggle_pause),
            pystray.MenuItem(lambda text: f"✓ {t('autostart')}" if is_autostart_enabled() else t("autostart"), on_toggle_autostart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda text: f"In: {self.engine.queue_in_count} | Done: {self.engine.session_processed}", None, enabled=False),
            pystray.MenuItem(lambda text: f"Speed: ~{self.engine.photos_per_hour:.0f} p/h", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray_hide"), lambda icon, item: self.after(0, self.hide_to_tray)),
            pystray.MenuItem(t("tray_exit"), on_exit)
        )

        self.tray_icon = pystray.Icon(
            "ImmichCaptionWorker",
            get_tray_icon("active"),
            "Immich Caption Worker",
            self.tray_menu
        )
        self.tray_icon.run_detached()

    def hide_to_tray(self):
        self.withdraw()

    def show_from_tray(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def toggle_autostart(self):
        enabled = self.autostart_var.get()
        set_autostart(enabled)

    def toggle_pause(self):
        self.engine.paused_by_user = not self.engine.paused_by_user
        if self.engine.paused_by_user:
            self.btn_pause.configure(text=t("btn_start"), fg_color="#10b981", hover_color="#059669")
            self.tray_icon.icon = get_tray_icon("paused")
        else:
            self.btn_pause.configure(text=t("btn_pause"), fg_color="#f59e0b", hover_color="#d97706")
            self.tray_icon.icon = get_tray_icon("active")

    def _update_ui_loop(self):
        # 1. Update Status Badge
        status = self.engine.status_text
        st_type = self.engine.status_type
        if st_type == "active":
            self.status_badge.configure(text=f"● {status}", text_color="#34d399")
        elif st_type in ("paused", "busy"):
            self.status_badge.configure(text=f"● {status}", text_color="#fbbf24")
        elif st_type == "error":
            self.status_badge.configure(text=f"● {status}", text_color="#f87171")
        else:
            self.status_badge.configure(text=f"● {status}", text_color="#94a3b8")

        # 2. Update Cards
        in_cnt = self.engine.queue_in_count
        self.card_in_queue["main"].configure(text=f"{in_cnt}")

        done = self.engine.session_processed
        self.card_processed["main"].configure(text=f"+{done}")
        self.card_processed["sub"].configure(text=t("card_session", count=done))

        ppm = self.engine.photos_per_minute
        avg_s = self.engine.avg_time_per_photo
        self.card_speed["main"].configure(text=f"~{ppm:.1f} " + t("card_speed_main", ppm="").replace("~", "").strip())
        self.card_speed["sub"].configure(text=f"{avg_s:.1f} s" if avg_s > 0 else "--")

        pph = self.engine.photos_per_hour
        active_claims = self.engine.queue_active_claims
        self.card_hour["main"].configure(text=f"~{pph:.0f} / " + t("card_speed_sub", pph="").replace("~", "").strip())
        self.card_hour["sub"].configure(text=t("card_active_claims", count=active_claims))

        # 3. Update GPU Header
        gpu_u = self.engine.current_gpu_util
        vram_u = self.engine.current_vram_used
        vram_t = self.engine.current_vram_total
        self.gpu_title_lbl.configure(text=f"GPU: {gpu_u}%")
        if vram_t > 0:
            self.vram_lbl.configure(text=f"VRAM: {vram_u} / {vram_t} MB ({vram_u/vram_t*100:.0f}%)")

        self._draw_gpu_graph()

        # 4. Update Last Photo
        if self.engine.last_photo_id not in ("Нет данных", "No data", ""):
            self.photo_info_lbl.configure(text=f"{self.engine.last_photo_id} ({self.engine.last_photo_time:.1f}s)")
            desc = self.engine.last_description.replace('\n', ' ')
            if len(desc) > 280:
                desc = desc[:277] + "..."
            self.photo_desc_lbl.configure(text=desc)

            if self.engine.last_thumbnail_pil:
                try:
                    ctk_img = ctk.CTkImage(self.engine.last_thumbnail_pil, size=self.engine.last_thumbnail_pil.size)
                    self.thumb_label.configure(image=ctk_img, text="")
                except Exception:
                    pass

        # 5. Update Logs
        if self.engine.log_lines:
            curr_lines = list(self.engine.log_lines)
            content = "\n".join(curr_lines)
            current_text = self.txt_logs.get("1.0", "end-1c")
            if current_text != content:
                self.txt_logs.delete("1.0", "end")
                self.txt_logs.insert("end", content)
                self.txt_logs.see("end")

        if self.engine.is_running:
            self.after(300, self._update_ui_loop)

    def _draw_gpu_graph(self):
        c = self.gpu_canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 50 or h < 30:
            return

        c.delete("all")

        # Grid lines (25%, 50%, 75%)
        for pct, color in [(25, "#1f2937"), (50, "#1f2937"), (75, "#1f2937")]:
            y = h - (pct / 100.0 * h)
            c.create_line(0, y, w, y, fill=color, dash=(2, 4))
            c.create_text(24, y - 6, text=f"{pct}%", fill="#475569", font=("Segoe UI", 8))

        # Throttling limit line
        limit = self.config.get("throttling", {}).get("max_gpu_util_percent", 35)
        y_lim = h - (limit / 100.0 * h)
        c.create_line(0, y_lim, w, y_lim, fill="#854d0e", dash=(4, 3))
        c.create_text(w - 60, y_lim - 7, text=t("gpu_limit_label", limit=limit), fill="#ca8a04", font=("Segoe UI", 9, "bold"))

        data = list(self.engine.gpu_history)
        n = len(data)
        if n < 2:
            return

        step_x = w / (n - 1)
        coords = []
        for i, val in enumerate(data):
            x = i * step_x
            y = h - (max(0, min(100, val)) / 100.0 * (h - 8)) - 4
            coords.append((x, y))

        poly_coords = [0, h]
        for x, y in coords:
            poly_coords.extend([x, y])
        poly_coords.extend([w, h])
        c.create_polygon(poly_coords, fill="#0c4a6e", outline="")

        flat_coords = []
        for x, y in coords:
            flat_coords.extend([x, y])
        c.create_line(flat_coords, fill="#38bdf8", width=2, smooth=True)

        last_x, last_y = coords[-1]
        c.create_oval(last_x - 4, last_y - 4, last_x + 4, last_y + 4, fill="#38bdf8", outline="#ffffff", width=1)


if __name__ == '__main__':
    app = WorkerApp()
    app.mainloop()
