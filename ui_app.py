import json
import os
import sys
import time
import base64
import io
import threading
import re
import urllib.request
from collections import deque
from PIL import Image, ImageTk

import tkinter as tk
import customtkinter as ctk
import pystray
import winreg

from immich_client import ImmichClient
from captioner import VlmCaptioner, format_immich_description
from gpu_monitor import is_system_busy, get_gpu_stats, get_user_idle_seconds, get_gpu_name
from state import StateManager
from create_icons import get_tray_icon
from i18n import t, set_language, get_language

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
        # Check Ctrl modifier (state & 4 on Windows)
        is_ctrl = bool(event.state & 4) or bool(event.state & 0x20000)
        if is_ctrl:
            code = event.keycode
            # Keycode 86 = V (Paste)
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
            # Keycode 67 = C (Copy)
            elif code == 67 or getattr(event, 'keysym', '').lower() in ('c', 'cyrillic_es'):
                try:
                    sel = inner.selection_get()
                    root.clipboard_clear()
                    root.clipboard_append(sel)
                    return "break"
                except Exception:
                    pass
            # Keycode 88 = X (Cut)
            elif code == 88 or getattr(event, 'keysym', '').lower() in ('x', 'cyrillic_che'):
                try:
                    sel = inner.selection_get()
                    root.clipboard_clear()
                    root.clipboard_append(sel)
                    inner.delete("sel.first", "sel.last")
                    return "break"
                except Exception:
                    pass
            # Keycode 65 = A (Select All)
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

    menu.add_command(label="Вставить (Ctrl+V)", command=do_paste)
    menu.add_command(label="Копировать (Ctrl+C)", command=do_copy)
    menu.add_command(label="Вырезать (Ctrl+X)", command=do_cut)
    menu.add_separator()
    menu.add_command(label="Выделить всё (Ctrl+A)", command=do_select_all)
    menu.add_command(label="Очистить", command=do_clear)

    def show_menu(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    inner.bind("<Button-3>", show_menu)
    ctk_entry.bind("<Button-3>", show_menu)

def get_active_model_name(config: dict) -> str:
    """Detects active model name from LM Studio API or config."""
    try:
        lm_url = config.get("lm_studio", {}).get("url", "http://localhost:1234/v1")
        req = urllib.request.Request(f"{lm_url}/models", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            cfg_model = config.get("lm_studio", {}).get("model", "")
            if cfg_model in models:
                return cfg_model
            for m in models:
                if "vl" in m.lower():
                    return m
            if models:
                return models[0]
    except Exception:
        pass
    return config.get("lm_studio", {}).get("model", "qwen3-vl-8b")


def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# Ensure working directory is set to app root
os.chdir(get_base_dir())

CONFIG_PATH = os.path.join(get_base_dir(), "config.json")
APP_ICON_PATH = os.path.join(get_base_dir(), "app_icon.ico")

REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME = "ImmichCaptioner"

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
                exe_file = os.path.join(base_dir, "ImmichCaptioner.exe")
                
                if getattr(sys, 'frozen', False):
                    cmd = f'"{sys.executable}"'
                elif os.path.exists(exe_file):
                    cmd = f'"{exe_file}"'
                else:
                    py_dir = os.path.dirname(sys.executable)
                    pythonw = os.path.join(py_dir, "pythonw.exe")
                    exe = pythonw if os.path.exists(pythonw) else sys.executable
                    script = os.path.abspath(os.path.join(base_dir, "ui_app.py"))
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

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

class EngineState:
    def __init__(self):
        self.is_running = True
        self.paused_by_user = False
        self.test_limit_remaining = 0
        self.force_reprocess = False
        self.throttle_mode = "auto_85"
        self.config = {}
        self.status_text = "Инициализация..."
        self.status_type = "info"  # "active", "paused", "busy", "error", "info"
        
        # Stats
        self.total_images = 0
        self.processed_total = 0
        self.unprocessed_total = 0
        self.session_processed = 0
        
        # Speeds & ETA
        self.recent_times = deque(maxlen=20)  # last N durations (seconds)
        self.recent_timestamps = deque(maxlen=20)
        self.photos_per_minute = 0.0
        self.photos_per_hour = 0.0
        self.eta_seconds = 0
        
        # GPU telemetry (60 seconds)
        self.gpu_history = deque([0] * 60, maxlen=60)
        self.current_gpu_util = 0
        self.current_vram_used = 0
        self.current_vram_total = 12288
        
        # Last photo
        self.last_photo_name = "Нет данных"
        self.last_photo_time = 0.0
        self.last_description = "Ожидание запуска первой обработки..."
        self.last_thumbnail_pil = None


class WorkerThread(threading.Thread):
    def __init__(self, engine: EngineState, config: dict):
        super().__init__(daemon=True)
        self.engine = engine
        self.config = config
        self.state_mgr = StateManager()

    def run(self):
        immich_cfg = self.config.get("immich", {})
        lm_cfg = self.config.get("lm_studio", {})
        throttle_cfg = self.config.get("throttling", {})

        immich = ImmichClient(immich_cfg["url"], immich_cfg["api_key"])
        captioner = VlmCaptioner(
            lm_cfg["url"],
            lm_cfg["model"],
            temperature=lm_cfg.get("temperature", 0.2),
            max_tokens=lm_cfg.get("max_tokens", 350)
        )

        # Initial check with reconnect loop (never die on transient network timeouts)
        while self.engine.is_running:
            if self.engine.paused_by_user:
                self.engine.status_text = "Приостановлено пользователем"
                self.engine.status_type = "paused"
                time.sleep(1)
                continue
            try:
                user_info = immich.test_connection()
                self.engine.status_text = f"Подключено к Immich ({user_info.get('name', 'User')})"
                self.engine.status_type = "active"
                break
            except Exception as e:
                self.engine.status_text = f"Ожидание ответа Immich: {e}"
                self.engine.status_type = "busy"
                time.sleep(4)

        current_page = 1
        consecutive_empty = 0

        while self.engine.is_running:
            # Check user pause
            if self.engine.paused_by_user:
                self.engine.status_text = "Приостановлено пользователем"
                self.engine.status_type = "paused"
                time.sleep(1)
                continue

            # Check if reset was requested
            if getattr(self.engine, "reset_requested", False):
                self.engine.reset_requested = False
                current_page = 1
                consecutive_empty = 0
                self.state_mgr.load()

            # Check LM Studio
            if not captioner.test_connection():
                self.engine.status_text = "Ожидание LM Studio (проверьте порт 1234)..."
                self.engine.status_type = "paused"
                time.sleep(5)
                continue

            # Check throttling (GPU / User Idle / Heavy apps)
            if getattr(self.engine, "throttle_mode", "auto_85") != "always_on":
                busy, reason = is_system_busy(throttle_cfg)
                if busy:
                    self.engine.status_text = f"Пауза: {reason}"
                    self.engine.status_type = "busy"
                    interval = throttle_cfg.get("check_interval_busy_seconds", 10)
                    for _ in range(interval):
                        if (
                            not self.engine.is_running 
                            or self.engine.paused_by_user 
                            or getattr(self.engine, "reset_requested", False)
                            or getattr(self.engine, "throttle_mode", "auto_85") == "always_on"
                        ):
                            break
                        time.sleep(1)
                    continue

            force_all = getattr(self.engine, "force_reprocess", False)
            if force_all:
                self.engine.status_text = f"Поиск фото в архиве (стр. {current_page})..."
            else:
                self.engine.status_text = f"Поиск фото без описания (стр. {current_page})..."
            self.engine.status_type = "active"

            # Fetch photos (or all photos if force_reprocess is active)
            try:
                unprocessed = immich.get_unprocessed_assets(page=current_page, size=50, force_all=force_all)
            except Exception as e:
                self.engine.status_text = f"Сбой API Immich: {e}"
                self.engine.status_type = "error"
                time.sleep(5)
                continue

            if not unprocessed:
                current_page = 1
                consecutive_empty = 0
                self.engine.force_reprocess = False
                self.engine.status_text = "Все фото в очереди обработаны. Ожидание..."
                self.engine.status_type = "info"
                time.sleep(15)
                continue

            candidates = [a for a in unprocessed if not self.state_mgr.should_skip(a['id'])]

            if not candidates:
                current_page += 1
                continue

            consecutive_empty = 0

            # Process candidates
            for asset in candidates:
                if not self.engine.is_running or self.engine.paused_by_user or getattr(self.engine, "reset_requested", False):
                    break

                # Re-check throttling before each photo
                if getattr(self.engine, "throttle_mode", "auto_85") != "always_on":
                    busy, reason = is_system_busy(throttle_cfg)
                    while busy and self.engine.is_running and not self.engine.paused_by_user and getattr(self.engine, "throttle_mode", "auto_85") != "always_on":
                        self.engine.status_text = f"Пауза: {reason}"
                        self.engine.status_type = "busy"
                        time.sleep(throttle_cfg.get("check_interval_busy_seconds", 10))
                        busy, reason = is_system_busy(throttle_cfg)

                if not self.engine.is_running or self.engine.paused_by_user or getattr(self.engine, "reset_requested", False):
                    break

                asset_id = asset['id']
                filename = asset.get('originalFileName', 'photo.jpg')
                disp_fn = filename if len(filename) <= 22 else filename[:19] + "..."
                self.engine.status_text = f"Загрузка: {disp_fn}..."
                self.engine.status_type = "active"

                t0 = time.time()
                try:
                    # Download preview
                    b64_img = immich.download_preview_b64(asset_id, immich_cfg.get("thumbnail_size", "preview"))
                    
                    # Create thumbnail for UI preview
                    try:
                        raw_bytes = base64.b64decode(b64_img)
                        pil_img = Image.open(io.BytesIO(raw_bytes))
                        pil_img.thumbnail((140, 140))
                        self.engine.last_thumbnail_pil = pil_img
                    except Exception:
                        pass

                    self.engine.status_text = f"Распознавание: {disp_fn}..."

                    # Call VLM
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
                        self.engine.status_text = f"Сохранение тегов: {disp_fn}..."
                        desc_mode = self.config.get("immich_description_mode", "tags_only")
                        immich_desc = format_immich_description(caption_data, mode=desc_mode)

                        # 1. Update Immich description
                        immich.update_description(asset_id, immich_desc)

                        # 2. Apply tags in Immich
                        applied_tags = 0
                        if tags:
                            applied_tags = immich.apply_tags_to_asset(asset_id, tags)

                        # 3. Write metadata task for cladovka (DropSync queue)
                        q_cfg = self.config.get("metadata_queue", {})
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
                                print(f"Warning: error writing to queue: {q_err}")

                        self.state_mgr.mark_processed(asset_id)
                        elapsed = time.time() - t0

                        # Update metrics
                        self.engine.session_processed += 1
                        self.engine.processed_total += 1
                        self.engine.unprocessed_total = max(0, self.engine.unprocessed_total - 1)
                        self.engine.last_photo_name = filename
                        self.engine.last_photo_time = elapsed
                        
                        display_text = f"【{title}】\n{desc_text}"
                        if tags:
                            display_text += f"\nТеги: {', '.join(tags)}"
                        self.engine.last_description = display_text
                        
                        self.engine.recent_times.append(elapsed)
                        self.engine.recent_timestamps.append(time.time())
                        self._recalc_speeds()

                        if self.engine.test_limit_remaining > 0:
                            self.engine.test_limit_remaining -= 1
                            if self.engine.test_limit_remaining == 0:
                                self.engine.paused_by_user = True
                                self.engine.status_text = f"Тест завершен на [{disp_fn}] (на паузе)"
                                self.engine.status_type = "paused"
                                continue

                        self.engine.status_text = f"Готово [{disp_fn}] за {elapsed:.1f}с ({applied_tags} тегов)"
                        self.engine.status_type = "active"
                    else:
                        self.state_mgr.mark_failed(asset_id, "Пустое описание")
                        self.engine.status_text = f"Модель вернула пустое описание: {disp_fn}"
                except Exception as e:
                    self.state_mgr.mark_failed(asset_id, str(e))
                    self.engine.status_text = f"Ошибка [{disp_fn}]: {e}"
                    self.engine.status_type = "error"

                # Rest between photos
                time.sleep(throttle_cfg.get("idle_delay_between_photos_seconds", 1))

    def _recalc_speeds(self):
        if len(self.engine.recent_times) > 0:
            avg_sec = sum(self.engine.recent_times) / len(self.engine.recent_times)
            if avg_sec > 0:
                self.engine.photos_per_minute = 60.0 / avg_sec
                self.engine.photos_per_hour = 3600.0 / avg_sec
                if self.engine.unprocessed_total > 0:
                    self.engine.eta_seconds = int(self.engine.unprocessed_total * avg_sec)


class TelemetryThread(threading.Thread):
    """Samples GPU utilization and refreshes Immich totals."""
    def __init__(self, engine: EngineState, config: dict):
        super().__init__(daemon=True)
        self.engine = engine
        self.config = config

    def run(self):
        immich_cfg = self.config.get("immich", {})
        immich = ImmichClient(immich_cfg["url"], immich_cfg["api_key"])
        last_immich_check = 0

        while self.engine.is_running:
            # 1. Sample GPU
            util, used, total = get_gpu_stats()
            self.engine.current_gpu_util = util
            self.engine.current_vram_used = used
            self.engine.current_vram_total = total
            self.engine.gpu_history.append(util)

            # 2. Refresh Immich totals every 60 seconds (or immediately if total <= 1)
            now = time.time()
            if now - last_immich_check > 60 or self.engine.total_images <= 1:
                last_immich_check = now
                try:
                    stats = immich.get_server_statistics()
                    total = stats.get('photos', 0)
                    if total > 0:
                        self.engine.total_images = total
                        st = StateManager()
                        if not getattr(self.engine, "force_reprocess", False):
                            self.engine.processed_total = max(self.engine.processed_total, st.state.get("processed_count", 0))
                        self.engine.unprocessed_total = max(0, self.engine.total_images - self.engine.processed_total)
                except Exception:
                    pass
            time.sleep(1)


class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, parent, title: str, message: str, on_confirm):
        super().__init__(parent)
        self.title(title)
        self.geometry("540x340")
        self.resizable(False, False)
        self.configure(fg_color="#0f131a")
        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        try:
            x = parent.winfo_x() + (parent.winfo_width() // 2) - 270
            y = parent.winfo_y() + (parent.winfo_height() // 2) - 170
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

        lbl_warn = ctk.CTkLabel(
            self, 
            text=t("confirm_reset_warn"), 
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#ef4444"
        )
        lbl_warn.pack(pady=(18, 6))

        lbl_msg = ctk.CTkLabel(
            self, 
            text=message, 
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
            wraplength=480,
            justify="center"
        )
        lbl_msg.pack(padx=20, pady=(6, 16))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", pady=(0, 22))

        def do_confirm():
            self.destroy()
            on_confirm()

        btn_cancel = ctk.CTkButton(
            btn_frame, 
            text=t("btn_cancel"), 
            width=120, 
            height=32, 
            fg_color="#334155", 
            hover_color="#475569", 
            command=self.destroy
        )
        btn_cancel.pack(side="left", padx=8)

        btn_ok = ctk.CTkButton(
            btn_frame, 
            text=t("btn_confirm_reset"), 
            width=160, 
            height=32, 
            fg_color="#dc2626", 
            hover_color="#b91c1c", 
            command=do_confirm
        )
        btn_ok.pack(side="left", padx=8)


class MainApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config = load_config()
        # Initialize UI language from config if present
        saved_lang = self.config.get("ui_language")
        if saved_lang:
            set_language(saved_lang)

        self.title(t("app_title"))
        self.geometry("900x720")
        self.minsize(800, 640)

        if os.path.exists(APP_ICON_PATH):
            try:
                self.iconbitmap(APP_ICON_PATH)
            except Exception:
                pass

        self.engine = EngineState()
        self.engine.config = self.config
        self.engine.throttle_mode = self.config.get("throttling", {}).get("mode", "auto_85")
        
        # Setup universal clipboard (RU and EN layout support for Ctrl+V/C/X/A)
        setup_universal_clipboard(self)

        # Detect hardware and active model
        self.gpu_name = get_gpu_name()
        self.active_model = get_active_model_name(self.config)

        # Initial state load
        self.state_mgr = StateManager()
        self.engine.processed_total = self.state_mgr.state.get("processed_count", 0)

        # Start background threads
        self.worker = WorkerThread(self.engine, self.config)
        self.worker.start()

        self.telemetry = TelemetryThread(self.engine, self.config)
        self.telemetry.start()

        # Build UI
        self._build_ui()

        # Start Tray
        self._setup_tray()

        # Window protocols
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        # Silent launch when started via Windows Autostart
        if "--minimized" in sys.argv:
            self.withdraw()

        # UI Update Loop (10 FPS)
        self.after(200, self._update_ui_loop)

    def _build_ui(self):
        # Header Frame
        header = ctk.CTkFrame(self, corner_radius=12, fg_color="#181e29")
        header.pack(fill="x", padx=16, pady=(16, 8))

        # Title & Subtitle
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=16, pady=12)

        self.title_lbl = ctk.CTkLabel(
            title_box, 
            text="Immich AI Captioner", 
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#60a5fa"
        )
        self.title_lbl.pack(anchor="w")

        self.subtitle_lbl = ctk.CTkLabel(
            title_box,
            text=t("subtitle", model=self.active_model, gpu=self.gpu_name),
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        )
        self.subtitle_lbl.pack(anchor="w")

        # Action Button (Start / Pause)
        self.btn_pause = ctk.CTkButton(
            header,
            text=t("btn_pause"),
            font=ctk.CTkFont(size=14, weight="bold"),
            width=130,
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
            fg_color="#3b82f6",
            hover_color="#2563eb"
        )
        self.chk_autostart.pack(side="right", padx=(8, 10), pady=12)

        # Language Selector [ EN | RU ]
        self.lang_var = ctk.StringVar(value=get_language().upper())
        self.lang_segmented = ctk.CTkSegmentedButton(
            header,
            values=["EN", "RU"],
            variable=self.lang_var,
            command=self.change_language,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=76,
            height=28,
            selected_color="#3b82f6",
            selected_hover_color="#2563eb",
            unselected_color="#0f131a",
            unselected_hover_color="#1e293b"
        )
        self.lang_segmented.pack(side="right", padx=(6, 10), pady=12)

        # Status Badge in header
        self.status_badge = ctk.CTkLabel(
            header,
            text=f"● {t('status_init')}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#34d399",
            padx=12,
            pady=4
        )
        self.status_badge.pack(side="right", padx=6)

        # --- TEST / SINGLE PHOTO RUNNER BAR ---
        test_bar = ctk.CTkFrame(self, corner_radius=10, fg_color="#181e29")
        test_bar.pack(fill="x", padx=16, pady=(0, 6))

        # --- TEST / SINGLE PHOTO RUNNER BAR ---
        test_bar = ctk.CTkFrame(self, corner_radius=10, fg_color="#181e29")
        test_bar.pack(fill="x", padx=16, pady=(0, 6))

        self.test_lbl = ctk.CTkLabel(test_bar, text=t("test_label"), font=ctk.CTkFont(size=12, weight="bold"), text_color="#38bdf8")
        self.test_lbl.pack(side="left", padx=(14, 8), pady=8)

        self.test_input = ctk.CTkEntry(
            test_bar, 
            placeholder_text=t("test_placeholder"),
            font=ctk.CTkFont(size=11),
            height=28
        )
        self.test_input.pack(side="left", fill="x", expand=True, padx=4, pady=8)
        attach_entry_context_menu(self.test_input, self)

        self.btn_paste = ctk.CTkButton(
            test_bar,
            text=t("btn_paste"),
            font=ctk.CTkFont(size=11),
            width=78,
            height=28,
            fg_color="#334155",
            hover_color="#475569",
            command=self.paste_to_test_input
        )
        self.btn_paste.pack(side="left", padx=(1, 4), pady=8)

        self.btn_run_test = ctk.CTkButton(
            test_bar,
            text=t("btn_recognize"),
            font=ctk.CTkFont(size=11, weight="bold"),
            width=105,
            height=28,
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self.run_single_test_photo
        )
        self.btn_run_test.pack(side="left", padx=4, pady=8)

        self.btn_test_5 = ctk.CTkButton(
            test_bar,
            text=t("btn_test_5"),
            font=ctk.CTkFont(size=11, weight="bold"),
            width=110,
            height=28,
            fg_color="#475569",
            hover_color="#334155",
            command=self.run_test_batch_5
        )
        self.btn_test_5.pack(side="left", padx=4, pady=8)

        self.mode_map_inv = {
            "tags_only": t("desc_mode_tags_only"), 
            "title_and_tags": t("desc_mode_title_and_tags"), 
            "full": t("desc_mode_full")
        }
        curr_mode = self.config.get("immich_description_mode", "tags_only")
        self.desc_mode_opt = ctk.CTkOptionMenu(
            test_bar,
            values=[t("desc_mode_tags_only"), t("desc_mode_title_and_tags"), t("desc_mode_full")],
            font=ctk.CTkFont(size=11),
            width=140,
            height=28,
            fg_color="#334155",
            command=self.on_desc_mode_change
        )
        self.desc_mode_opt.set(self.mode_map_inv.get(curr_mode, t("desc_mode_tags_only")))
        self.desc_mode_opt.pack(side="right", padx=(4, 12), pady=8)

        self.mode_lbl = ctk.CTkLabel(test_bar, text=t("desc_mode_label"), font=ctk.CTkFont(size=11), text_color="#94a3b8")
        self.mode_lbl.pack(side="right", padx=(4, 2), pady=8)

        # --- STATS CARDS GRID ---
        stats_frame = ctk.CTkFrame(self, fg_color="transparent")
        stats_frame.pack(fill="x", padx=16, pady=6)
        stats_frame.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stats")

        # Card 1: Progress
        self.card_progress = self._create_card(stats_frame, 0, t("card_processed"), "0 / 0", "Сессия: +0")
        # Card 2: Remaining
        self.card_remaining = self._create_card(stats_frame, 1, t("card_remaining"), "0", t("card_remaining_sub"))
        # Card 3: Speed
        self.card_speed = self._create_card(stats_frame, 2, t("card_speed"), "~0 фото/мин", "~0 в час")
        # Card 4: ETA
        self.card_eta = self._create_card(stats_frame, 3, t("card_eta"), "--", t("card_eta_sub"))

        # Global progress bar under cards
        self.prog_bar = ctk.CTkProgressBar(self, height=8, corner_radius=4, progress_color="#3b82f6")
        self.prog_bar.set(0.0)
        self.prog_bar.pack(fill="x", padx=18, pady=(4, 8))

        # --- MIDDLE: GPU GRAPH & MONITOR ---
        gpu_box = ctk.CTkFrame(self, corner_radius=12, fg_color="#181e29")
        gpu_box.pack(fill="both", expand=True, padx=16, pady=6)

        gpu_header = ctk.CTkFrame(gpu_box, fg_color="transparent")
        gpu_header.pack(fill="x", padx=16, pady=(10, 4))

        self.gpu_title_lbl = ctk.CTkLabel(
            gpu_header,
            text=t("gpu_load", gpu=self.gpu_name, util=0),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#e2e8f0"
        )
        self.gpu_title_lbl.pack(side="left")

        init_vram_t = self.engine.current_vram_total if self.engine.current_vram_total > 0 else 24576
        self.vram_lbl = ctk.CTkLabel(
            gpu_header,
            text=t("vram_load", used=0, total=init_vram_t, pct=0),
            font=ctk.CTkFont(size=13),
            text_color="#94a3b8"
        )
        self.vram_lbl.pack(side="right")

        # Throttle Mode Selector: [Auto 85% | ON]
        throttle_ctrl = ctk.CTkFrame(gpu_header, fg_color="transparent")
        throttle_ctrl.pack(side="right", padx=(0, 20))

        self.lbl_throttle_ctrl = ctk.CTkLabel(
            throttle_ctrl, 
            text=t("gpu_load_mode"), 
            font=ctk.CTkFont(size=11, weight="bold"), 
            text_color="#94a3b8"
        )
        self.lbl_throttle_ctrl.pack(side="left", padx=(0, 6))

        curr_th_mode = self.config.get("throttling", {}).get("mode", "auto_85")
        self.seg_throttle = ctk.CTkSegmentedButton(
            throttle_ctrl,
            values=[t("gpu_mode_auto"), t("gpu_mode_on")],
            command=self.on_throttle_mode_change,
            font=ctk.CTkFont(size=11, weight="bold"),
            unselected_color="#0f131a",
            unselected_hover_color="#1e293b",
            height=26,
            width=150
        )
        if curr_th_mode == "always_on":
            self.seg_throttle.set(t("gpu_mode_on"))
            self.seg_throttle.configure(selected_color="#10b981", selected_hover_color="#059669")
        else:
            self.seg_throttle.set(t("gpu_mode_auto"))
            self.seg_throttle.configure(selected_color="#3b82f6", selected_hover_color="#2563eb")
        self.seg_throttle.pack(side="left")

        # Canvas for live GPU load curve
        self.canvas_w = 860
        self.canvas_h = 130
        self.gpu_canvas = ctk.CTkCanvas(
            gpu_box,
            height=self.canvas_h,
            bg="#0f131a",
            highlightthickness=0
        )
        self.gpu_canvas.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        # --- BOTTOM: LAST PROCESSED PHOTO PREVIEW ---
        last_frame = ctk.CTkFrame(self, corner_radius=12, fg_color="#181e29")
        last_frame.pack(fill="x", padx=16, pady=(6, 14))

        self.last_title = ctk.CTkLabel(
            last_frame,
            text=t("last_photo_title"),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#cbd5e1"
        )
        self.last_title.pack(anchor="w", padx=16, pady=(10, 4))

        content_box = ctk.CTkFrame(last_frame, fg_color="transparent")
        content_box.pack(fill="x", padx=16, pady=(0, 12))

        # Thumbnail Label
        self.thumb_label = ctk.CTkLabel(content_box, text=t("preview_placeholder"), width=110, height=80, fg_color="#0f131a", corner_radius=8)
        self.thumb_label.pack(side="left", padx=(0, 12))

        # Details Box
        desc_box = ctk.CTkFrame(content_box, fg_color="transparent")
        desc_box.pack(side="left", fill="both", expand=True)

        self.photo_info_lbl = ctk.CTkLabel(
            desc_box,
            text=t("last_photo_waiting"),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#38bdf8",
            anchor="w"
        )
        self.photo_info_lbl.pack(anchor="w")

        self.photo_desc_lbl = ctk.CTkLabel(
            desc_box,
            text=t("last_photo_waiting_desc"),
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
            justify="left",
            wraplength=660,
            anchor="w"
        )
        self.photo_desc_lbl.pack(anchor="w", pady=(2, 0))

        # --- SERVER STATUS BAR (DISTRIBUTED EXIFTOOL) ---
        server_bar = ctk.CTkFrame(self, corner_radius=10, fg_color="#181e29")
        server_bar.pack(fill="x", padx=16, pady=(0, 14))

        # Pack buttons FIRST with side="right" so they NEVER get pushed or clipped
        self.btn_restart_server = ctk.CTkButton(
            server_bar,
            text=t("btn_server_worker"),
            font=ctk.CTkFont(size=11, weight="bold"),
            width=135,
            height=28,
            fg_color="#334155",
            hover_color="#475569",
            command=self.request_server_restart
        )
        self.btn_restart_server.pack(side="right", padx=(4, 12), pady=6)

        self.btn_reset_all = ctk.CTkButton(
            server_bar,
            text=t("btn_reset_all"),
            font=ctk.CTkFont(size=11, weight="bold"),
            width=140,
            height=28,
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            command=self.confirm_reset_and_reprocess
        )
        self.btn_reset_all.pack(side="right", padx=4, pady=6)

        self.server_status_lbl = ctk.CTkLabel(
            server_bar,
            text=t("server_status_waiting"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#94a3b8",
            anchor="w"
        )
        self.server_status_lbl.pack(side="left", fill="x", expand=True, padx=14, pady=8)

    def confirm_reset_and_reprocess(self):
        msg = t("confirm_reset_msg")

        def on_confirmed():
            self.state_mgr.reset_processed()
            if hasattr(self, 'worker') and hasattr(self.worker, 'state_mgr'):
                self.worker.state_mgr.reset_processed()
            self.engine.processed_total = 0
            self.engine.session_processed = 0
            self.engine.force_reprocess = True
            self.engine.reset_requested = True
            self.engine.unprocessed_total = self.engine.total_images
            self.status_badge.configure(text=f"● {t('status_active')}", text_color="#f59e0b")
            if self.engine.paused_by_user:
                self.toggle_pause()

        ConfirmDialog(self, t("confirm_reset_title"), msg, on_confirm=on_confirmed)

    def request_server_restart(self):
        cmd_dir = self.config.get("metadata_queue", {}).get("commands_dir", "./commands")
        try:
            os.makedirs(cmd_dir, exist_ok=True)
            with open(os.path.join(cmd_dir, "restart.cmd"), "w", encoding="utf-8") as f:
                f.write(f"restart at {time.time()}")
            self.server_status_lbl.configure(text="📡 Server: Restart signal sent...", text_color="#f59e0b")
        except Exception as e:
            self.server_status_lbl.configure(text=f"Error sending command: {e}", text_color="#ef4444")

    def on_throttle_mode_change(self, value: str):
        if value == "ON":
            self.engine.throttle_mode = "always_on"
            self.seg_throttle.configure(selected_color="#10b981", selected_hover_color="#059669")
            self.config.setdefault("throttling", {})["mode"] = "always_on"
            save_config(self.config)
            self.status_badge.configure(text="● Режим нагрузки: Всегда ON (без лимита)", text_color="#10b981")
            if self.engine.status_type == "busy":
                self.engine.status_text = "Режим ON: Работа без ограничений"
                self.engine.status_type = "active"
        else:
            self.engine.throttle_mode = "auto_85"
            self.seg_throttle.configure(selected_color="#3b82f6", selected_hover_color="#2563eb")
            self.config.setdefault("throttling", {})["mode"] = "auto_85"
            save_config(self.config)
            self.status_badge.configure(text="● Режим нагрузки: Auto (лимит 85%)", text_color="#38bdf8")

    def on_desc_mode_change(self, choice: str):
        mode_map = {"Только теги": "tags_only", "Заголовок + Теги": "title_and_tags", "Полное описание": "full"}
        m = mode_map.get(choice, "tags_only")
        self.config["immich_description_mode"] = m
        self.engine.config["immich_description_mode"] = m
        if hasattr(self, 'worker') and hasattr(self.worker, 'config'):
            self.worker.config["immich_description_mode"] = m
        save_config(self.config)
        self.status_badge.configure(text=f"● Режим описания: {choice}", text_color="#38bdf8")

    def run_test_batch_5(self):
        self.engine.test_limit_remaining = 5
        if self.engine.paused_by_user:
            self.toggle_pause()
        self.status_badge.configure(text="● Тест: обработка 5 фото...", text_color="#38bdf8")

    def paste_to_test_input(self):
        try:
            text = self.clipboard_get()
            if text:
                self.test_input.delete(0, 'end')
                self.test_input.insert(0, text.strip())
                self.status_badge.configure(text="● Ссылка вставлена из буфера", text_color="#38bdf8")
        except Exception:
            self.status_badge.configure(text="● Буфер обмена пуст", text_color="#f87171")

    def run_single_test_photo(self):
        raw = self.test_input.get().strip()
        if not raw:
            self.status_badge.configure(text="● Введите URL или ID фото!", text_color="#f87171")
            return

        # Robust UUID extraction (handles full URLs, query params, raw IDs)
        uuid_match = re.search(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', raw)
        if uuid_match:
            asset_id = uuid_match.group(0)
        elif "/photos/" in raw:
            asset_id = raw.split("/photos/")[-1].split("?")[0].split("/")[0].strip()
        else:
            asset_id = raw

        self.btn_run_test.configure(state="disabled", text="⏳ Работа...")
        self.status_badge.configure(text=f"● Распознавание [{asset_id[:8]}...]", text_color="#38bdf8")


        def task():
            try:
                imm_cfg = self.config.get("immich", {})
                lm_cfg = self.config.get("lm_studio", {})
                immich = ImmichClient(imm_cfg["url"], imm_cfg["api_key"])
                captioner = VlmCaptioner(lm_cfg["url"], lm_cfg["model"], temperature=0.1)

                # 1. Fetch info
                asset = immich.get_asset_info(asset_id)
                
                filename = asset.get('originalFileName', asset_id)
                b64_img = immich.download_preview_b64(asset_id, "preview")

                # 2. Recognize
                t0 = time.time()
                caption_data = captioner.generate_caption(b64_img)
                elapsed = time.time() - t0

                title = caption_data.get("title", "").strip()
                desc_text = caption_data.get("description", "").strip()
                tags = caption_data.get("tags", [])
                ocr = caption_data.get("ocr", "").strip()

                # 3. Format description
                desc_mode = self.config.get("immich_description_mode", "tags_only")
                immich_desc = format_immich_description(caption_data, mode=desc_mode)

                immich.update_description(asset_id, immich_desc)
                applied_tags = 0
                if tags:
                    applied_tags = immich.apply_tags_to_asset(asset_id, tags)

                # 4. Write queue
                q_cfg = self.config.get("metadata_queue", {})
                if q_cfg.get("enabled", True):
                    q_dir = q_cfg.get("dir", r"\\NAS\ImmichMetadata\queue")
                    os.makedirs(q_dir, exist_ok=True)
                    task_file = os.path.join(q_dir, f"{asset_id}.json")
                    task_data = {
                        "asset_id": asset_id,
                        "container_path": asset.get("originalPath", ""),
                        "title": title,
                        "description": desc_text,
                        "tags": tags,
                        "ocr": ocr,
                        "timestamp": time.time()
                    }
                    with open(task_file, "w", encoding="utf-8") as tf:
                        json.dump(task_data, tf, ensure_ascii=False, indent=2)

                # 5. Update UI
                self.engine.last_photo_name = filename
                self.engine.last_photo_time = elapsed
                disp = f"【{title}】\nВ Immich записано: {immich_desc}"
                if tags and desc_mode != "tags_only":
                    disp += f"\nТеги: {', '.join(tags)}"
                self.engine.last_description = disp

                try:
                    raw_bytes = base64.b64decode(b64_img)
                    pil_img = Image.open(io.BytesIO(raw_bytes))
                    pil_img.thumbnail((140, 140))
                    self.engine.last_thumbnail_pil = pil_img
                except Exception:
                    pass

                success_msg = f"Тест [{filename}] готов ({elapsed:.1f}с, {applied_tags} тегов)"
                self.engine.status_text = success_msg
                self.engine.status_type = "active"
                self.after(0, lambda: self.status_badge.configure(text=f"● {success_msg}", text_color="#34d399"))
            except Exception as e:
                err_msg = f"Ошибка теста: {e}"
                self.engine.status_text = err_msg
                self.engine.status_type = "error"
                self.after(0, lambda: self.status_badge.configure(text=f"● {err_msg}", text_color="#ef4444"))
            finally:
                self.after(0, lambda: self.btn_run_test.configure(state="normal", text="⚡ Распознать"))

        threading.Thread(target=task, daemon=True).start()

    def change_language(self, choice: str):
        lang = choice.lower()
        set_language(lang)
        self.config["ui_language"] = lang
        save_config(self.config)
        self._refresh_language_texts()

    def _refresh_language_texts(self):
        self.title(t("app_title"))
        self.subtitle_lbl.configure(text=t("subtitle", model=self.active_model, gpu=self.gpu_name))
        self.btn_pause.configure(text=t("btn_start") if self.engine.paused_by_user else t("btn_pause"))
        self.chk_autostart.configure(text=t("autostart"))
        self.test_lbl.configure(text=t("test_label"))
        self.test_input.configure(placeholder_text=t("test_placeholder"))
        self.btn_paste.configure(text=t("btn_paste"))
        self.btn_run_test.configure(text=t("btn_recognize"))
        self.btn_test_5.configure(text=t("btn_test_5"))
        self.mode_lbl.configure(text=t("desc_mode_label"))

        # Re-map description dropdown
        self.mode_map_inv = {
            "tags_only": t("desc_mode_tags_only"), 
            "title_and_tags": t("desc_mode_title_and_tags"), 
            "full": t("desc_mode_full")
        }
        curr_mode = self.config.get("immich_description_mode", "tags_only")
        self.desc_mode_opt.configure(values=[t("desc_mode_tags_only"), t("desc_mode_title_and_tags"), t("desc_mode_full")])
        self.desc_mode_opt.set(self.mode_map_inv.get(curr_mode, t("desc_mode_tags_only")))

        # Cards titles
        self.card_progress["title"].configure(text=t("card_processed"))
        self.card_remaining["title"].configure(text=t("card_remaining"))
        self.card_remaining["sub"].configure(text=t("card_remaining_sub"))
        self.card_speed["title"].configure(text=t("card_speed"))
        self.card_eta["title"].configure(text=t("card_eta"))
        self.card_eta["sub"].configure(text=t("card_eta_sub"))

        # GPU / Throttle
        self.lbl_throttle_ctrl.configure(text=t("gpu_load_mode"))
        self.seg_throttle.configure(values=[t("gpu_mode_auto"), t("gpu_mode_on")])
        if getattr(self.engine, "throttle_mode", "auto_85") == "always_on":
            self.seg_throttle.set(t("gpu_mode_on"))
        else:
            self.seg_throttle.set(t("gpu_mode_auto"))

        # Last photo
        self.last_title.configure(text=t("last_photo_title"))
        if self.engine.last_photo_name in ("Нет данных", "No data"):
            self.photo_info_lbl.configure(text=t("last_photo_waiting"))
            self.photo_desc_lbl.configure(text=t("last_photo_waiting_desc"))
        self.thumb_label.configure(text=t("preview_placeholder"))

        # Server buttons
        self.btn_restart_server.configure(text=t("btn_server_worker"))
        self.btn_reset_all.configure(text=t("btn_reset_all"))

    def _create_card(self, parent, col, title, main_val, sub_val):
        card = ctk.CTkFrame(parent, corner_radius=10, fg_color="#181e29")
        card.grid(row=0, column=col, padx=4, pady=4, sticky="nsew")

        lbl_t = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=11, weight="bold"), text_color="#64748b")
        lbl_t.pack(anchor="w", padx=12, pady=(10, 0))

        lbl_v = ctk.CTkLabel(card, text=main_val, font=ctk.CTkFont(size=18, weight="bold"), text_color="#f8fafc")
        lbl_v.pack(anchor="w", padx=12, pady=(2, 0))

        lbl_s = ctk.CTkLabel(card, text=sub_val, font=ctk.CTkFont(size=11), text_color="#94a3b8")
        lbl_s.pack(anchor="w", padx=12, pady=(0, 10))

        return {"title": lbl_t, "main": lbl_v, "sub": lbl_s}

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
            pystray.MenuItem(lambda text: f"Done: {self.engine.processed_total} | Left: {self.engine.unprocessed_total}", None, enabled=False),
            pystray.MenuItem(lambda text: f"Speed: ~{self.engine.photos_per_hour:.0f} p/h", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray_hide"), lambda icon, item: self.after(0, self.hide_to_tray)),
            pystray.MenuItem(t("tray_exit"), on_exit)
        )

        self.tray_icon = pystray.Icon(
            "ImmichCaptioner",
            get_tray_icon("active"),
            "Immich AI Captioner",
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
        # Update Status Badge
        status = self.engine.status_text
        st_type = self.engine.status_type
        disp_status = status if len(status) <= 45 else (status[:42] + "...")
        if st_type == "active":
            self.status_badge.configure(text=f"● {disp_status}", text_color="#34d399")
        elif st_type in ("paused", "busy"):
            self.status_badge.configure(text=f"● {disp_status}", text_color="#fbbf24")
        elif st_type == "error":
            self.status_badge.configure(text=f"● {disp_status}", text_color="#f87171")
        else:
            self.status_badge.configure(text=f"● {disp_status}", text_color="#94a3b8")

        # Update Cards
        total = self.engine.total_images
        done = self.engine.processed_total
        pct = (done / total * 100.0) if total > 0 else 0.0

        self.card_progress["main"].configure(text=f"{done:,} / {total:,}".replace(',', ' '))
        self.card_progress["sub"].configure(text=t("card_processed_sub", count=self.engine.session_processed, pct=f"{pct:.1f}"))
        self.prog_bar.set(pct / 100.0)

        unprocessed = self.engine.unprocessed_total
        self.card_remaining["main"].configure(text=f"{unprocessed:,}".replace(',', ' '))

        ppm = self.engine.photos_per_minute
        pph = self.engine.photos_per_hour
        self.card_speed["main"].configure(text=t("card_speed_main", ppm=f"{ppm:.1f}"))
        self.card_speed["sub"].configure(text=t("card_speed_sub", pph=f"{pph:.0f}"))

        eta_sec = self.engine.eta_seconds
        if eta_sec > 0 and not self.engine.paused_by_user:
            hours = eta_sec // 3600
            mins = (eta_sec % 3600) // 60
            if hours > 24:
                days = hours // 24
                h = hours % 24
                eta_str = f"~{days}d {h}h" if get_language() == "en" else f"~{days}д {h}ч"
            else:
                eta_str = f"~{hours}h {mins}m" if get_language() == "en" else f"~{hours}ч {mins}мин"
        else:
            eta_str = "--"
        self.card_eta["main"].configure(text=eta_str)

        # Update GPU Header
        gpu_u = self.engine.current_gpu_util
        vram_u = self.engine.current_vram_used
        vram_t = self.engine.current_vram_total
        gpu_name = getattr(self, "gpu_name", "GPU")
        self.gpu_title_lbl.configure(text=t("gpu_load", gpu=gpu_name, util=gpu_u))
        v_pct = (vram_u / vram_t * 100) if vram_t > 0 else 0
        self.vram_lbl.configure(text=t("vram_load", used=vram_u, total=vram_t, pct=f"{v_pct:.0f}"))

        # Periodically refresh active model subtitle if changed in LM Studio
        if not hasattr(self, "_tick_count"):
            self._tick_count = 0
        self._tick_count += 1
        if self._tick_count % 50 == 0:
            m = get_active_model_name(self.config)
            if m != getattr(self, "active_model", ""):
                self.active_model = m
                self.subtitle_lbl.configure(text=t("subtitle", model=self.active_model, gpu=self.gpu_name))


        # Draw GPU Graph
        self._draw_gpu_graph()

        # Update Last Photo
        if self.engine.last_photo_name not in ("Нет данных", "No data"):
            time_lbl = "time" if get_language() == "en" else "время"
            self.photo_info_lbl.configure(text=f"{self.engine.last_photo_name} ({time_lbl}: {self.engine.last_photo_time:.1f}s)")
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

        # Poll Server stats.json
        stats_path = self.config.get("metadata_queue", {}).get("stats_path", "./stats.json")
        if os.path.exists(stats_path):
            try:
                with open(stats_path, "r", encoding="utf-8") as sf:
                    s_data = json.load(sf)
                s_status = s_data.get("status", "unknown")
                s_embedded = s_data.get("total_embedded", 0)
                s_queue = s_data.get("in_queue", 0)
                s_err = s_data.get("total_errors", 0)
                s_last = s_data.get("last_file", "")
                
                txt = f"📡 Server: {s_status.upper()} | " + t("server_status_embedded", count=s_embedded) + " | " + t("server_status_queue", count=s_queue)
                if s_err > 0:
                    err_lbl = "Errors" if get_language() == "en" else "Ошибок"
                    txt += f" | {err_lbl}: {s_err}"
                if s_last:
                    disp_last = s_last if len(s_last) <= 22 else (s_last[:19] + "...")
                    txt += f" | {disp_last}"
                
                color = "#34d399" if s_status == "running" else "#fbbf24"
                self.server_status_lbl.configure(text=txt, text_color=color)
            except Exception:
                pass

        # Schedule next update
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

        # Throttling limit line (Auto 85% vs ON)
        if getattr(self.engine, "throttle_mode", "auto_85") == "always_on":
            c.create_text(w - 75, 12, text="Режим: Всегда ON", fill="#10b981", font=("Segoe UI", 9, "bold"))
        else:
            limit = self.config.get("throttling", {}).get("max_gpu_util_percent", 85)
            y_lim = h - (limit / 100.0 * h)
            c.create_line(0, y_lim, w, y_lim, fill="#854d0e", dash=(4, 3))
            c.create_text(w - 60, y_lim - 7, text=f"Лимит: {limit}%", fill="#ca8a04", font=("Segoe UI", 9, "bold"))

        # History points
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

        # Draw filled polygon under curve
        poly_coords = [0, h]
        for x, y in coords:
            poly_coords.extend([x, y])
        poly_coords.extend([w, h])
        c.create_polygon(poly_coords, fill="#0c4a6e", outline="")

        # Draw main curve
        flat_coords = []
        for x, y in coords:
            flat_coords.extend([x, y])
        c.create_line(flat_coords, fill="#38bdf8", width=2, smooth=True)

        # Draw current point glowing circle
        last_x, last_y = coords[-1]
        c.create_oval(last_x - 4, last_y - 4, last_x + 4, last_y + 4, fill="#38bdf8", outline="#ffffff", width=1)


if __name__ == '__main__':
    app = MainApp()
    app.mainloop()
