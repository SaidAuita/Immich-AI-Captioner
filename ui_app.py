import json
import os
import sys
import time
import base64
import io
import threading
import re
import urllib.request
import urllib.parse
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

class ToolTip:
    """Shows a stylish tooltip when hovering over a widget."""
    def __init__(self, widget, text: str, delay_ms: int = 350):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.tip_window = None
        self.after_id = None

        try:
            self.widget.bind("<Enter>", self._on_enter, add="+")
            self.widget.bind("<Leave>", self._on_leave, add="+")
            self.widget.bind("<ButtonPress>", self._on_leave, add="+")
        except Exception:
            pass

    def _on_enter(self, event=None):
        self._schedule()

    def _on_leave(self, event=None):
        self._unschedule()
        self._hide()

    def _schedule(self):
        self._unschedule()
        try:
            self.after_id = self.widget.after(self.delay_ms, self._show)
        except Exception:
            pass

    def _unschedule(self):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def _show(self):
        if not self.text or self.tip_window:
            return
        try:
            x = self.widget.winfo_rootx() + (self.widget.winfo_width() // 2)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return

        try:
            self.tip_window = tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.attributes("-topmost", True)

            frame = tk.Frame(tw, background="#0f172a", highlightbackground="#38bdf8", highlightthickness=1)
            frame.pack()
            lbl = tk.Label(
                frame,
                text=self.text,
                background="#0f172a",
                foreground="#f8fafc",
                font=("Segoe UI", 9),
                padx=8,
                pady=4,
                justify="left",
                wraplength=340
            )
            lbl.pack()

            tw.update_idletasks()
            w = tw.winfo_width()
            x = max(10, x - (w // 2))
            tw.wm_geometry(f"+{x}+{y}")
        except Exception:
            self.tip_window = None

    def _hide(self):
        if self.tip_window:
            try:
                self.tip_window.destroy()
            except Exception:
                pass
            self.tip_window = None

def get_active_model_name(config: dict) -> str:
    """Detects active model name from LM Studio API (prioritizing loaded model in memory) or config."""
    try:
        lm_url = config.get("lm_studio", {}).get("url", "http://localhost:1234/v1")
        # 1. Native LM Studio API check: which model is actually LOADED in VRAM right now
        try:
            parsed = urllib.parse.urlparse(lm_url)
            root_url = f"{parsed.scheme}://{parsed.netloc}"
            req_v0 = urllib.request.Request(f"{root_url}/api/v0/models", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req_v0, timeout=1.5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                models = data.get("data", [])
                loaded = [m for m in models if m.get("state") == "loaded"]
                if loaded:
                    vlm_loaded = [m for m in loaded if m.get("type") == "vlm" or any(k in m.get("id", "").lower() for k in ["vl", "vision", "gemma"])]
                    if vlm_loaded:
                        return vlm_loaded[0].get("id")
                    return loaded[0].get("id")
        except Exception:
            pass

        # 2. Standard /v1/models fallback
        req = urllib.request.Request(f"{lm_url}/models", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            cfg_model = config.get("lm_studio", {}).get("model", "")
            if cfg_model in models:
                return cfg_model
            for m in models:
                if any(k in m.lower() for k in ["gemma", "vl", "vision"]):
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
        self.is_processing_active = False
        self.paused_by_user = False
        self.force_wake = False
        self.test_limit_remaining = 0
        self.force_reprocess = False
        self.throttle_mode = "auto_85"
        self.config = {}
        self.status_text = "Инициализация..."
        self.status_type = "info"  # "active", "paused", "busy", "error", "info"
        
        # Date period filter
        self.date_filter_enabled = False
        self.date_from_str = ""
        self.date_to_str = ""
        self.date_from_iso = None
        self.date_to_iso = None
        
        # Photo per-day slice filter (optional, default disabled)
        self.photo_slice_enabled = False
        self.photo_slice_from = 1
        self.photo_slice_to = 5
        
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
        self.last_title = ""
        self.last_desc_text = ""
        self.last_tags = []
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
                self.engine.status_text = t("status_paused")
                self.engine.status_type = "paused"
                time.sleep(1)
                continue
            try:
                user_info = immich.test_connection()
                self.engine.status_text = t("worker_status_connected", name=user_info.get('name', 'User'))
                self.engine.status_type = "info"
                break
            except Exception as e:
                self.engine.status_text = t("worker_status_waiting_immich", err=str(e))
                self.engine.status_type = "busy"
                time.sleep(4)

        current_page = 1
        consecutive_empty = 0
        day_photo_counts = {}

        while self.engine.is_running:
            # Check user pause
            if self.engine.paused_by_user:
                self.engine.status_text = t("status_paused")
                self.engine.status_type = "paused"
                time.sleep(0.5)
                continue

            # Wait for user to explicitly click Start
            if not getattr(self.engine, "is_processing_active", False):
                time.sleep(0.2)
                continue

            # Check if reset was requested
            if getattr(self.engine, "reset_requested", False):
                self.engine.reset_requested = False
                current_page = 1
                consecutive_empty = 0
                day_photo_counts.clear()
                self.state_mgr.load()

            # Check LM Studio
            if not captioner.test_connection():
                self.engine.status_text = t("status_waiting_lm")
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
            date_filter_on = getattr(self.engine, "date_filter_enabled", False)
            slice_on = getattr(self.engine, "photo_slice_enabled", False)
            taken_after = getattr(self.engine, "date_from_iso", None) if date_filter_on else None
            taken_before = getattr(self.engine, "date_to_iso", None) if date_filter_on else None

            date_hint = ""
            if date_filter_on and (taken_after or taken_before):
                desc_p = getattr(self.engine, "date_period_desc", "")
                if desc_p:
                    date_hint = f" [{desc_p}]"
                else:
                    df = getattr(self.engine, "date_from_str", "") or t("date_period_start")
                    dt = getattr(self.engine, "date_to_str", "") or t("date_period_end")
                    date_hint = f" [{df} — {dt}]"

            if force_all:
                self.engine.status_text = t("worker_status_search_archive", hint=date_hint, page=current_page)
            else:
                self.engine.status_text = t("worker_status_search_unprocessed", hint=date_hint, page=current_page)
            self.engine.status_type = "active"

            # Always use "desc" order to index downwards into the past (от новых к старым, к 1980)
            order_param = "desc"

            # When photo slice is enabled, fetch all items to calculate accurate per-day positions
            fetch_all = force_all or (date_filter_on and slice_on)
            try:
                unprocessed = immich.get_unprocessed_assets(
                    page=current_page, 
                    size=50, 
                    force_all=fetch_all,
                    taken_after=taken_after,
                    taken_before=taken_before,
                    order=order_param
                )
            except Exception as e:
                self.engine.status_text = t("worker_status_immich_err", err=str(e))
                self.engine.status_type = "error"
                time.sleep(5)
                continue

            has_more = getattr(immich, "last_has_more", False)
            raw_count = getattr(immich, "last_items_count", len(unprocessed))

            # 1. Immich returned 0 photos in total: end of search / archive reached!
            if raw_count == 0:
                current_page = 1
                consecutive_empty = 0
                day_photo_counts.clear()
                self.engine.force_reprocess = False
                self.engine.is_processing_active = False
                if date_filter_on:
                    self.engine.status_text = t("worker_status_period_done", hint=date_hint)
                else:
                    self.engine.status_text = t("worker_status_queue_done")
                self.engine.status_type = "info"
                continue

            candidates = [a for a in unprocessed if not self.state_mgr.should_skip(a['id'])]

            # 2. Photos were returned, but all on this page are already processed or skipped
            if not candidates:
                if has_more:
                    current_page += 1
                else:
                    current_page = 1
                    consecutive_empty = 0
                    day_photo_counts.clear()
                    self.engine.force_reprocess = False
                    self.engine.is_processing_active = False
                    if date_filter_on:
                        self.engine.status_text = t("worker_status_period_done", hint=date_hint)
                    else:
                        self.engine.status_text = t("worker_status_queue_done")
                    self.engine.status_type = "info"
                continue

            consecutive_empty = 0

            # Process candidates
            for asset in candidates:
                if not self.engine.is_running or self.engine.paused_by_user or getattr(self.engine, "reset_requested", False):
                    break

                # Track per-day photo index
                day_str = (asset.get('exifInfo', {}).get('dateTimeOriginal') or asset.get('fileCreatedAt') or '')[:10]
                if not day_str:
                    day_str = getattr(self.engine, "date_from_str", "") or "unknown"
                day_photo_counts[day_str] = day_photo_counts.get(day_str, 0) + 1
                photo_day_idx = day_photo_counts[day_str]

                if slice_on:
                    s_from = getattr(self.engine, "photo_slice_from", 1)
                    s_to = getattr(self.engine, "photo_slice_to", 5)
                    if photo_day_idx < s_from or photo_day_idx > s_to:
                        df = getattr(self.engine, "date_from_str", "")
                        dt = getattr(self.engine, "date_to_str", "")
                        if df and dt and df == dt and photo_day_idx > s_to:
                            self.engine.force_reprocess = False
                            self.engine.is_processing_active = False
                            self.engine.status_text = t("worker_status_period_done", hint=date_hint)
                            self.engine.status_type = "info"
                            break
                        continue

                    # If inside slice, but asset already has description and force_reprocess is not active, skip VLM
                    existing_desc = (asset.get('exifInfo', {}).get('description') or asset.get('description') or '').strip()
                    if not force_all and existing_desc:
                        continue

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
                disp_fn = filename if len(filename) <= 50 else (filename[:26] + "…" + filename[-20:])
                self.engine.status_text = t("worker_status_downloading", fn=disp_fn)
                self.engine.status_type = "active"

                t0 = time.time()
                try:
                    # Download preview
                    b64_img = immich.download_preview_b64(asset_id, immich_cfg.get("thumbnail_size", "preview"))
                    
                    # Create thumbnail for UI preview (keep local until caption is ready)
                    current_thumb_pil = None
                    try:
                        raw_bytes = base64.b64decode(b64_img)
                        pil_img = Image.open(io.BytesIO(raw_bytes))
                        pil_img.thumbnail((140, 140))
                        current_thumb_pil = pil_img
                    except Exception:
                        pass

                    self.engine.status_text = t("worker_status_recognizing", fn=disp_fn)

                    # Call VLM with configured languages and tags count
                    cap_lang = self.config.get("caption_language", "ru")
                    tags_lang = self.config.get("tags_language", "en")
                    tags_cnt = int(self.config.get("tags_count", 15))
                    caption_data = captioner.generate_caption(b64_img, desc_lang=cap_lang, tags_lang=tags_lang, tags_count=tags_cnt)
                    
                    if isinstance(caption_data, dict):
                        title = caption_data.get("title", "").strip()
                        desc_text = caption_data.get("description", "").strip()
                        tags = caption_data.get("tags", [])
                        if tags_cnt and tags_cnt > 0:
                            tags = tags[:tags_cnt]
                            caption_data["tags"] = tags
                        ocr = caption_data.get("ocr", "").strip()
                    else:
                        title = ""
                        desc_text = str(caption_data).strip()
                        tags = []
                        ocr = ""

                    if desc_text or title or tags:
                        self.engine.status_text = t("worker_status_saving", fn=disp_fn)
                        desc_mode = self.config.get("immich_description_mode", "tags_only")
                        immich_desc = format_immich_description(caption_data, mode=desc_mode, desc_lang=cap_lang)

                        # 1. Update Immich description
                        immich.update_description(asset_id, immich_desc)

                        # 2. Apply tags in Immich
                        applied_tags = 0
                        if tags:
                            applied_tags = immich.apply_tags_to_asset(asset_id, tags)
                            if applied_tags == 0:
                                time.sleep(1.0)
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
                                    "ocr": ocr,
                                    "write_iptc": self.config.get("write_iptc", True)
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
                        self.engine.last_thumbnail_pil = current_thumb_pil
                        self.engine.last_photo_name = filename
                        self.engine.last_photo_time = elapsed
                        self.engine.last_title = title
                        self.engine.last_desc_text = desc_text
                        self.engine.last_tags = tags
                        
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
                                self.engine.status_text = t("worker_status_test_done_pause")
                                self.engine.status_type = "paused"
                                continue

                        self.engine.status_text = t("worker_status_photo_done", fn=disp_fn, elapsed=f"{elapsed:.1f}", count=applied_tags)
                        self.engine.status_type = "active"
                    else:
                        self.state_mgr.mark_failed(asset_id, "Пустое описание")
                        self.engine.status_text = t("worker_status_empty_desc", fn=disp_fn)
                except Exception as e:
                    self.state_mgr.mark_failed(asset_id, str(e))
                    self.engine.status_text = t("worker_status_error", fn=disp_fn, err=str(e))
                    self.engine.status_type = "error"

                # Rest between photos
                time.sleep(throttle_cfg.get("idle_delay_between_photos_seconds", 1))

            if getattr(self.engine, "is_processing_active", False) and not self.engine.paused_by_user:
                if has_more:
                    current_page += 1
                else:
                    current_page = 1
                    day_photo_counts.clear()
                    self.engine.force_reprocess = False
                    self.engine.is_processing_active = False
                    if date_filter_on:
                        self.engine.status_text = t("worker_status_period_done", hint=date_hint)
                    else:
                        self.engine.status_text = t("worker_status_queue_done")
                    self.engine.status_type = "info"

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
        self.geometry("960x860")
        self.minsize(940, 840)

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

        # Action Button (Start / Pause / Working)
        self.btn_pause = ctk.CTkButton(
            header,
            text=t("btn_start"),
            font=ctk.CTkFont(size=14, weight="bold"),
            width=140,
            height=38,
            fg_color="#10b981",
            hover_color="#059669",
            command=self.toggle_pause
        )
        self.btn_pause.pack(side="right", padx=16, pady=12)
        self._btn_pause_state = "idle"

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

        # UI Language Dropdown [ 🌐 English ▾ ]
        self.lang_var = ctk.StringVar(value=get_language_name(get_language()))
        self.lang_opt = ctk.CTkOptionMenu(
            header,
            values=list(get_supported_languages().values()),
            variable=self.lang_var,
            command=self.change_language,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=115,
            height=28,
            fg_color="#334155",
            button_color="#475569",
            button_hover_color="#1e293b",
            dropdown_fg_color="#1e293b"
        )
        self.lang_opt.pack(side="right", padx=(6, 10), pady=12)

        # Stock Tagger Launcher Button [ 📁 Стоки ]
        self.btn_stock_tagger = ctk.CTkButton(
            header,
            text=t("btn_stock_tagger"),
            command=self.open_stock_tagger,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=85,
            height=28,
            fg_color="#334155",
            hover_color="#475569"
        )
        self.btn_stock_tagger.pack(side="right", padx=(6, 16), pady=12)
        ToolTip(self.btn_stock_tagger, t("tip_stock_tagger"))

        # --- STATUS BAR (Отдельная строка состояния) ---
        status_bar = ctk.CTkFrame(self, corner_radius=10, fg_color="#181e29")
        status_bar.pack(fill="x", padx=16, pady=(0, 6))

        self.status_badge = ctk.CTkLabel(
            status_bar,
            text=f"● {t('status_init')}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#34d399",
            anchor="w"
        )
        self.status_badge.pack(side="left", fill="x", expand=True, padx=14, pady=6)

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
        ToolTip(self.caption_lang_opt, t("tip_caption_lang"))

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
        ToolTip(self.tags_lang_opt, t("tip_tags_lang"))

        # Keywords Count
        self.tags_count_lbl = ctk.CTkLabel(
            settings_bar, 
            text=t("lang_tags_count_label"), 
            font=ctk.CTkFont(size=11, weight="bold"), 
            text_color="#94a3b8"
        )
        self.tags_count_lbl.pack(side="left", padx=(6, 4), pady=6)

        curr_tags_count = str(self.config.get("tags_count", 15))
        self.tags_count_var = ctk.StringVar(value=curr_tags_count)
        self.tags_count_opt = ctk.CTkOptionMenu(
            settings_bar,
            values=["10", "15", "20", "25", "30", "35", "40", "45", "50"],
            variable=self.tags_count_var,
            command=self.on_tags_count_change,
            font=ctk.CTkFont(size=11),
            width=65,
            height=28,
            fg_color="#334155",
            button_color="#475569",
            button_hover_color="#1e293b",
            dropdown_fg_color="#1e293b"
        )
        self.tags_count_opt.pack(side="left", padx=(0, 10), pady=6)
        ToolTip(self.tags_count_opt, t("tip_tags_count"))

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
        ToolTip(self.desc_mode_opt, t("tip_desc_mode"))

        self.mode_lbl = ctk.CTkLabel(
            settings_bar, 
            text=t("desc_mode_label"), 
            font=ctk.CTkFont(size=11), 
            text_color="#94a3b8"
        )
        self.mode_lbl.pack(side="right", padx=(4, 2), pady=6)

        # Checkbox: Write to original files via ExifTool
        exiftool_enabled = self.config.get("metadata_queue", {}).get("enabled", False)
        iptc_enabled = self.config.get("write_iptc", True)

        self.chk_iptc = ctk.CTkCheckBox(
            settings_bar,
            text=t("setting_iptc"),
            command=self.toggle_iptc,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1",
            fg_color="#0284c7",
            hover_color="#0369a1",
            checkmark_color="#ffffff",
            width=18,
            height=18,
            state="normal" if exiftool_enabled else "disabled"
        )
        if iptc_enabled:
            self.chk_iptc.select()
        else:
            self.chk_iptc.deselect()
        self.chk_iptc.pack(side="right", padx=(4, 16), pady=6)
        ToolTip(self.chk_iptc, t("tip_iptc"))

        self.chk_exiftool = ctk.CTkCheckBox(
            settings_bar,
            text=t("setting_exiftool"),
            command=self.toggle_exiftool,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1",
            fg_color="#0284c7",
            hover_color="#0369a1",
            checkmark_color="#ffffff",
            width=18,
            height=18
        )
        if exiftool_enabled:
            self.chk_exiftool.select()
        else:
            self.chk_exiftool.deselect()
        self.chk_exiftool.pack(side="right", padx=(10, 4), pady=6)
        ToolTip(self.chk_exiftool, t("tip_exiftool"))

        # --- TEST / SINGLE PHOTO & PERIOD FILTER BAR ---
        test_bar = ctk.CTkFrame(self, corner_radius=10, fg_color="#181e29")
        test_bar.pack(fill="x", padx=16, pady=(0, 6))

        # Row 1: Test photo controls
        row_test = ctk.CTkFrame(test_bar, fg_color="transparent")
        row_test.pack(fill="x", padx=10, pady=(6, 3))

        self.test_lbl = ctk.CTkLabel(row_test, text=t("test_label"), font=ctk.CTkFont(size=12, weight="bold"), text_color="#38bdf8")
        self.test_lbl.pack(side="left", padx=(4, 6), pady=2)

        self.test_input = ctk.CTkEntry(
            row_test, 
            placeholder_text=t("test_placeholder"),
            font=ctk.CTkFont(size=11),
            height=28
        )
        self.test_input.pack(side="left", fill="x", expand=True, padx=4, pady=2)
        attach_entry_context_menu(self.test_input, self)
        ToolTip(self.test_input, t("tip_test_input"))

        self.btn_paste = ctk.CTkButton(
            row_test,
            text=t("btn_paste"),
            font=ctk.CTkFont(size=11),
            width=78,
            height=28,
            fg_color="#334155",
            hover_color="#475569",
            command=self.paste_to_test_input
        )
        self.btn_paste.pack(side="left", padx=(1, 4), pady=2)
        ToolTip(self.btn_paste, t("tip_btn_paste"))

        self.btn_run_test = ctk.CTkButton(
            row_test,
            text=t("btn_recognize"),
            font=ctk.CTkFont(size=11, weight="bold"),
            width=105,
            height=28,
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self.run_single_test_photo
        )
        self.btn_run_test.pack(side="left", padx=4, pady=2)
        ToolTip(self.btn_run_test, t("tip_btn_recognize"))

        self.btn_test_5 = ctk.CTkButton(
            row_test,
            text=t("btn_test_5"),
            font=ctk.CTkFont(size=11, weight="bold"),
            width=110,
            height=28,
            fg_color="#475569",
            hover_color="#334155",
            command=self.run_test_batch_5
        )
        self.btn_test_5.pack(side="left", padx=4, pady=2)
        ToolTip(self.btn_test_5, t("tip_btn_test_5"))

        # Row 2: Date period filter
        row_date = ctk.CTkFrame(test_bar, fg_color="transparent")
        row_date.pack(fill="x", padx=10, pady=(2, 6))

        self.chk_date_filter = ctk.CTkCheckBox(
            row_date,
            text=t("chk_date_filter"),
            command=self.toggle_date_filter,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#94a3b8",
            fg_color="#0284c7",
            hover_color="#0369a1",
            checkmark_color="#ffffff",
            width=16,
            height=16
        )
        self.chk_date_filter.pack(side="left", padx=(2, 4), pady=2)
        ToolTip(self.chk_date_filter, t("tip_date_filter"))

        self.lbl_date_from = ctk.CTkLabel(row_date, text=t("date_from_label"), font=ctk.CTkFont(size=10, weight="bold"), text_color="#64748b")
        self.lbl_date_from.pack(side="left", padx=(2, 1), pady=2)

        self.entry_date_from = ctk.CTkEntry(
            row_date,
            placeholder_text="01.01.2024",
            font=ctk.CTkFont(size=11),
            width=78,
            height=26,
            state="disabled"
        )
        self.entry_date_from.pack(side="left", padx=1, pady=2)
        attach_entry_context_menu(self.entry_date_from, self)
        ToolTip(self.entry_date_from, t("tip_date_from"))

        self.lbl_date_to = ctk.CTkLabel(row_date, text=t("date_to_label"), font=ctk.CTkFont(size=10, weight="bold"), text_color="#64748b")
        self.lbl_date_to.pack(side="left", padx=(3, 1), pady=2)

        self.entry_date_to = ctk.CTkEntry(
            row_date,
            placeholder_text="31.12.2024",
            font=ctk.CTkFont(size=11),
            width=78,
            height=26,
            state="disabled"
        )
        self.entry_date_to.pack(side="left", padx=1, pady=2)
        attach_entry_context_menu(self.entry_date_to, self)
        ToolTip(self.entry_date_to, t("tip_date_to"))

        # Photo per-day slice controls (optional, default disabled)
        self.chk_photo_slice = ctk.CTkCheckBox(
            row_date,
            text=t("chk_photo_slice"),
            command=self.toggle_photo_slice,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#94a3b8",
            fg_color="#0284c7",
            hover_color="#0369a1",
            checkmark_color="#ffffff",
            width=16,
            height=16,
            state="disabled"
        )
        self.chk_photo_slice.pack(side="left", padx=(6, 3), pady=2)
        ToolTip(self.chk_photo_slice, t("tip_photo_slice"))

        self.lbl_slice_from = ctk.CTkLabel(row_date, text=t("slice_from_label"), font=ctk.CTkFont(size=10, weight="bold"), text_color="#64748b")
        self.lbl_slice_from.pack(side="left", padx=(2, 1), pady=2)

        self.entry_slice_from = ctk.CTkEntry(
            row_date,
            placeholder_text="1",
            font=ctk.CTkFont(size=11),
            width=34,
            height=26,
            state="disabled"
        )
        self.entry_slice_from.insert(0, "1")
        self.entry_slice_from.pack(side="left", padx=1, pady=2)
        attach_entry_context_menu(self.entry_slice_from, self)
        ToolTip(self.entry_slice_from, t("tip_slice_from"))

        self.lbl_slice_to = ctk.CTkLabel(row_date, text=t("slice_to_label"), font=ctk.CTkFont(size=10, weight="bold"), text_color="#64748b")
        self.lbl_slice_to.pack(side="left", padx=(3, 1), pady=2)

        self.entry_slice_to = ctk.CTkEntry(
            row_date,
            placeholder_text="5",
            font=ctk.CTkFont(size=11),
            width=34,
            height=26,
            state="disabled"
        )
        self.entry_slice_to.insert(0, "5")
        self.entry_slice_to.pack(side="left", padx=1, pady=2)
        attach_entry_context_menu(self.entry_slice_to, self)
        ToolTip(self.entry_slice_to, t("tip_slice_to"))

        self.btn_apply_date = ctk.CTkButton(
            row_date,
            text=t("btn_apply"),
            font=ctk.CTkFont(size=10, weight="bold"),
            width=70,
            height=26,
            fg_color="#334155",
            hover_color="#475569",
            state="disabled",
            command=self.apply_date_filter
        )
        self.btn_apply_date.pack(side="left", padx=(5, 6), pady=2)
        ToolTip(self.btn_apply_date, t("tip_btn_apply_date"))

        self.lbl_date_status = ctk.CTkLabel(
            row_date,
            text=t("date_status_no_limit"),
            font=ctk.CTkFont(size=10),
            text_color="#64748b",
            anchor="w"
        )
        self.lbl_date_status.pack(side="left", fill="x", expand=True, padx=2, pady=2)

        # Auto-apply bindings for date and slice fields
        for _ent in (self.entry_date_from, self.entry_date_to, self.entry_slice_from, self.entry_slice_to):
            _ent.bind("<Return>", lambda e: self.apply_date_filter(show_error=True))
            _ent.bind("<FocusOut>", lambda e: self.apply_date_filter(show_error=False))

        # Restore saved date filter from config if present
        df_cfg = self.config.get("date_filter", {})
        if df_cfg.get("from"):
            self.entry_date_from.delete(0, "end")
            self.entry_date_from.insert(0, str(df_cfg["from"]))
        if df_cfg.get("to"):
            self.entry_date_to.delete(0, "end")
            self.entry_date_to.insert(0, str(df_cfg["to"]))
        if df_cfg.get("slice_from"):
            self.entry_slice_from.delete(0, "end")
            self.entry_slice_from.insert(0, str(df_cfg["slice_from"]))
        if df_cfg.get("slice_to"):
            self.entry_slice_to.delete(0, "end")
            self.entry_slice_to.insert(0, str(df_cfg["slice_to"]))
        if df_cfg.get("slice_enabled"):
            self.chk_photo_slice.select()
        if df_cfg.get("enabled"):
            self.chk_date_filter.select()
            self.toggle_date_filter()

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
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8",
            anchor="w"
        )
        self.photo_info_lbl.pack(anchor="w", pady=(0, 2))

        # Initial placeholder label (shown before any photo is processed)
        self.photo_desc_lbl = ctk.CTkLabel(
            desc_box,
            text=t("last_photo_waiting_desc"),
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            justify="left",
            wraplength=680,
            anchor="w"
        )
        self.photo_desc_lbl.pack(anchor="w", pady=(2, 0))

        # Structured fields frame (Title, Description, Tags)
        self.photo_fields_frame = ctk.CTkFrame(desc_box, fg_color="transparent")

        # Row 1: Title
        self.row_title = ctk.CTkFrame(self.photo_fields_frame, fg_color="transparent")
        self.row_title.pack(fill="x", anchor="w", pady=(1, 1))
        self.lbl_title_prefix = ctk.CTkLabel(
            self.row_title,
            text=t("field_title"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10b981",
            width=88,
            anchor="w"
        )
        self.lbl_title_prefix.pack(side="left", anchor="nw")
        self.lbl_title_val = ctk.CTkLabel(
            self.row_title,
            text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#f8fafc",
            justify="left",
            wraplength=660,
            anchor="w"
        )
        self.lbl_title_val.pack(side="left", fill="x", expand=True, anchor="nw")

        # Row 2: Description
        self.row_desc = ctk.CTkFrame(self.photo_fields_frame, fg_color="transparent")
        self.row_desc.pack(fill="x", anchor="w", pady=(1, 1))
        self.lbl_desc_prefix = ctk.CTkLabel(
            self.row_desc,
            text=t("field_desc"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10b981",
            width=88,
            anchor="w"
        )
        self.lbl_desc_prefix.pack(side="left", anchor="nw")
        self.lbl_desc_val = ctk.CTkLabel(
            self.row_desc,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#cbd5e1",
            justify="left",
            wraplength=660,
            anchor="w"
        )
        self.lbl_desc_val.pack(side="left", fill="x", expand=True, anchor="nw")

        # Row 3: Tags
        self.row_tags = ctk.CTkFrame(self.photo_fields_frame, fg_color="transparent")
        self.row_tags.pack(fill="x", anchor="w", pady=(1, 1))
        self.lbl_tags_prefix = ctk.CTkLabel(
            self.row_tags,
            text=t("field_tags"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10b981",
            width=88,
            anchor="w"
        )
        self.lbl_tags_prefix.pack(side="left", anchor="nw")
        self.lbl_tags_val = ctk.CTkLabel(
            self.row_tags,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            justify="left",
            wraplength=660,
            anchor="w"
        )
        self.lbl_tags_val.pack(side="left", fill="x", expand=True, anchor="nw")

        # --- SERVER STATUS BAR (DISTRIBUTED EXIFTOOL) ---
        server_bar = ctk.CTkFrame(self, corner_radius=10, fg_color="#181e29")
        server_bar.pack(fill="x", padx=16, pady=(0, 14))

        # Pack reset button with side="right"
        self.btn_reset_all = ctk.CTkButton(
            server_bar,
            text=t("btn_reset_all"),
            font=ctk.CTkFont(size=11, weight="bold"),
            width=165,
            height=28,
            fg_color="#334155",
            hover_color="#475569",
            command=self.confirm_reset_and_reprocess
        )
        self.btn_reset_all.pack(side="right", padx=(4, 12), pady=6)
        ToolTip(self.btn_reset_all, t("tip_reset_all"))

        self.server_status_lbl = ctk.CTkLabel(
            server_bar,
            text=t("server_status_waiting"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#94a3b8",
            anchor="w"
        )
        self.server_status_lbl.pack(side="left", fill="x", expand=True, padx=14, pady=8)

    def toggle_exiftool(self):
        enabled = bool(self.chk_exiftool.get())
        self.config.setdefault("metadata_queue", {})["enabled"] = enabled
        save_config(self.config)
        status_txt = t("status_exiftool_on") if enabled else t("status_exiftool_off")
        self.status_badge.configure(text=f"● {status_txt}", text_color="#38bdf8" if enabled else "#94a3b8")
        if hasattr(self, "chk_iptc"):
            self.chk_iptc.configure(state="normal" if enabled else "disabled")

    def toggle_iptc(self):
        enabled = bool(self.chk_iptc.get())
        self.config["write_iptc"] = enabled
        save_config(self.config)
        status_txt = t("status_iptc_on") if enabled else t("status_iptc_off")
        self.status_badge.configure(text=f"● {status_txt}", text_color="#38bdf8" if enabled else "#94a3b8")

    def toggle_photo_slice(self):
        slice_on = bool(self.chk_photo_slice.get())
        if slice_on:
            self.entry_slice_from.configure(state="normal")
            self.entry_slice_to.configure(state="normal")
        else:
            self.entry_slice_from.configure(state="disabled")
            self.entry_slice_to.configure(state="disabled")

    def toggle_date_filter(self):
        enabled = bool(self.chk_date_filter.get())
        if enabled:
            self.entry_date_from.configure(state="normal")
            self.entry_date_to.configure(state="normal")
            self.btn_apply_date.configure(state="normal")
            self.chk_photo_slice.configure(state="normal")
            if bool(self.chk_photo_slice.get()):
                self.entry_slice_from.configure(state="normal")
                self.entry_slice_to.configure(state="normal")
            d_from = self.entry_date_from.get().strip()
            d_to = self.entry_date_to.get().strip()
            if d_from or d_to:
                self.apply_date_filter(show_error=False)
            else:
                self.engine.date_filter_enabled = False
                self.engine.date_from_iso = None
                self.engine.date_to_iso = None
                self.lbl_date_status.configure(text=t("date_status_specify"), text_color="#f59e0b")
                self.entry_date_from.focus()
        else:
            self.entry_date_from.configure(state="disabled")
            self.entry_date_to.configure(state="disabled")
            self.btn_apply_date.configure(state="disabled")
            self.chk_photo_slice.configure(state="disabled")
            self.entry_slice_from.configure(state="disabled")
            self.entry_slice_to.configure(state="disabled")
            self.engine.date_filter_enabled = False
            self.engine.photo_slice_enabled = False
            self.engine.date_from_iso = None
            self.engine.date_to_iso = None
            self.lbl_date_status.configure(text=t("date_status_no_limit"), text_color="#64748b")
            self.engine.reset_requested = True
            if not getattr(self.engine, "is_processing_active", False):
                self.engine.status_text = t("status_period_cleared")
                self.engine.status_type = "info"
                self._update_btn_pause_state(force=True)
            self.config.setdefault("date_filter", {})["enabled"] = False
            save_config(self.config)

    def apply_date_filter(self, show_error=True) -> bool:
        if not bool(self.chk_date_filter.get()):
            self.engine.date_filter_enabled = False
            return True
        d_from = self.entry_date_from.get().strip()
        d_to = self.entry_date_to.get().strip()

        if not d_from and not d_to:
            self.engine.date_filter_enabled = False
            self.engine.date_from_iso = None
            self.engine.date_to_iso = None
            self.lbl_date_status.configure(text=t("date_status_specify"), text_color="#f59e0b")
            if show_error:
                self.entry_date_from.focus()
            return False

        iso_from = None
        iso_to = None
        period_desc = ""

        if d_from and not d_to:
            # User entered 1 date: continue indexing DOWNWARDS into the past (towards 1980)
            # Upper bound is end of entered day, NO lower bound (down to archive start)
            iso_from_parsed = ImmichClient.normalize_date_iso(d_from, is_end=True)
            if not iso_from_parsed:
                if show_error:
                    self.lbl_date_status.configure(text=t("date_error_from"), text_color="#ef4444")
                    self.entry_date_from.focus()
                return False
            formatted_from = ImmichClient.format_date_display(iso_from_parsed)
            if formatted_from != d_from:
                self.entry_date_from.delete(0, "end")
                self.entry_date_from.insert(0, formatted_from)
                d_from = formatted_from

            iso_from = None                         # taken_after (no lower limit, down to 1980)
            iso_to = iso_from_parsed                # taken_before (end of entered date)
            period_desc = f"{d_from} → 1980"

        elif d_to and not d_from:
            # User entered only end date: index from newest down to d_to
            iso_to_parsed = ImmichClient.normalize_date_iso(d_to, is_end=False)
            if not iso_to_parsed:
                if show_error:
                    self.lbl_date_status.configure(text=t("date_error_to"), text_color="#ef4444")
                    self.entry_date_to.focus()
                return False
            formatted_to = ImmichClient.format_date_display(iso_to_parsed)
            if formatted_to != d_to:
                self.entry_date_to.delete(0, "end")
                self.entry_date_to.insert(0, formatted_to)
                d_to = formatted_to

            iso_from = iso_to_parsed                # taken_after (lower limit)
            iso_to = None                           # taken_before (from newest)
            period_desc = f"2026 → {d_to}"

        else:
            # Both dates entered: bounded range between older and newer
            iso_1 = ImmichClient.normalize_date_iso(d_from, is_end=False)
            if not iso_1:
                if show_error:
                    self.lbl_date_status.configure(text=t("date_error_from"), text_color="#ef4444")
                    self.entry_date_from.focus()
                return False
            formatted_from = ImmichClient.format_date_display(iso_1)
            if formatted_from != d_from:
                self.entry_date_from.delete(0, "end")
                self.entry_date_from.insert(0, formatted_from)
                d_from = formatted_from

            iso_2 = ImmichClient.normalize_date_iso(d_to, is_end=True)
            if not iso_2:
                if show_error:
                    self.lbl_date_status.configure(text=t("date_error_to"), text_color="#ef4444")
                    self.entry_date_to.focus()
                return False
            formatted_to = ImmichClient.format_date_display(iso_2)
            if formatted_to != d_to:
                self.entry_date_to.delete(0, "end")
                self.entry_date_to.insert(0, formatted_to)
                d_to = formatted_to

            # Sort them so earlier is lower, later is upper
            iso_start = min(iso_1[:10], iso_2[:10])
            iso_end = max(iso_1[:10], iso_2[:10])
            iso_from = f"{iso_start}T00:00:00.000Z" # taken_after
            iso_to = f"{iso_end}T23:59:59.999Z"     # taken_before
            period_desc = f"{d_from} — {d_to}"

        # Photo slice per day validation
        slice_on = bool(self.chk_photo_slice.get())
        s_from = 1
        s_to = 5
        if slice_on:
            raw_from = self.entry_slice_from.get().strip()
            raw_to = self.entry_slice_to.get().strip()
            try:
                s_from = int(raw_from) if raw_from else 1
                s_to = int(raw_to) if raw_to else 5
                if s_from < 1 or s_to < s_from:
                    raise ValueError("Invalid slice range")
            except Exception:
                if show_error:
                    self.lbl_date_status.configure(text=t("slice_error_range"), text_color="#ef4444")
                return False
            self.engine.photo_slice_enabled = True
            self.engine.photo_slice_from = s_from
            self.engine.photo_slice_to = s_to
        else:
            self.engine.photo_slice_enabled = False

        self.engine.date_filter_enabled = True
        self.engine.date_from_str = d_from
        self.engine.date_to_str = d_to
        self.engine.date_from_iso = iso_from
        self.engine.date_to_iso = iso_to
        self.engine.date_period_desc = period_desc

        # Save to config for persistence
        self.config.setdefault("date_filter", {})["from"] = d_from
        self.config["date_filter"]["to"] = d_to
        self.config["date_filter"]["enabled"] = True
        self.config["date_filter"]["slice_enabled"] = slice_on
        self.config["date_filter"]["slice_from"] = s_from
        self.config["date_filter"]["slice_to"] = s_to
        save_config(self.config)

        if slice_on:
            period_desc += f" {t('date_status_slice', slice_from=s_from, slice_to=s_to)}"

        self.lbl_date_status.configure(text=t("date_status_active", period=period_desc), text_color="#34d399")
        self.engine.reset_requested = True
        if not getattr(self.engine, "is_processing_active", False):
            self.engine.status_text = t("status_period_set", period=period_desc)
            self.engine.status_type = "info"
            self._update_btn_pause_state(force=True)
        else:
            self.engine.status_text = t("status_period_set", period=period_desc)
            self.engine.status_type = "active"

        return True

    def confirm_reset_and_reprocess(self):
        if getattr(self.engine, "force_reprocess", False):
            # User clicked again to cancel force reprocess mode
            self.engine.force_reprocess = False
            self.btn_reset_all.configure(
                fg_color="#334155",
                hover_color="#475569",
                text=t("btn_reset_all")
            )
            self.status_badge.configure(text=f"● {t('status_reprocess_cancelled')}", text_color="#34d399")
            return

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
            active_lbl = t("btn_reset_all_active", label=t("btn_reset_all"))
            self.btn_reset_all.configure(
                fg_color="#dc2626",
                hover_color="#ef4444",
                text=active_lbl
            )
            self.engine.paused_by_user = False
            if not getattr(self.engine, "is_processing_active", False):
                self.status_badge.configure(text=f"● {t('status_reprocess_active')}", text_color="#f59e0b")
                self.engine.status_text = t("status_reprocess_active")
                self.engine.status_type = "info"
                self._update_btn_pause_state(force=True)
            else:
                self.status_badge.configure(text=f"● {t('status_reprocess_active')}", text_color="#f59e0b")

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
        if value in ("ON", "EIN", "ACTIVÉ", "LIGADO", "ENCENDIDO", "常時ON", "始终开启", t("gpu_mode_on")):
            self.engine.throttle_mode = "always_on"
            self.seg_throttle.configure(selected_color="#10b981", selected_hover_color="#059669")
            self.config.setdefault("throttling", {})["mode"] = "always_on"
            save_config(self.config)
            self.status_badge.configure(text=f"● {t('status_throttle_always_on')}", text_color="#10b981")
            if self.engine.status_type == "busy":
                self.engine.status_text = t("status_throttle_unlimited")
                self.engine.status_type = "active"
        else:
            self.engine.throttle_mode = "auto_85"
            self.seg_throttle.configure(selected_color="#3b82f6", selected_hover_color="#2563eb")
            self.config.setdefault("throttling", {})["mode"] = "auto_85"
            save_config(self.config)
            self.status_badge.configure(text=f"● {t('status_throttle_auto')}", text_color="#38bdf8")

    def on_caption_lang_change(self, choice: str):
        code = get_code_by_name(choice)
        self.config["caption_language"] = code
        self.engine.config["caption_language"] = code
        if hasattr(self, 'worker') and hasattr(self.worker, 'config'):
            self.worker.config["caption_language"] = code
        save_config(self.config)
        self.status_badge.configure(text=f"● {t('lang_caption_label')} {choice}", text_color="#38bdf8")

    def on_tags_lang_change(self, choice: str):
        code = get_code_by_name(choice)
        self.config["tags_language"] = code
        self.engine.config["tags_language"] = code
        if hasattr(self, 'worker') and hasattr(self.worker, 'config'):
            self.worker.config["tags_language"] = code
        save_config(self.config)
        self.status_badge.configure(text=f"● {t('lang_tags_label')} {choice}", text_color="#38bdf8")

    def on_tags_count_change(self, choice: str):
        try:
            cnt = int(choice)
        except ValueError:
            cnt = 15
        self.config["tags_count"] = cnt
        self.engine.config["tags_count"] = cnt
        if hasattr(self, 'worker') and hasattr(self.worker, 'config'):
            self.worker.config["tags_count"] = cnt
        save_config(self.config)
        self.status_badge.configure(text=f"● {t('lang_tags_count_label')} {cnt}", text_color="#38bdf8")

    def on_desc_mode_change(self, choice: str):
        rev = {v: k for k, v in self.mode_map_inv.items()}
        m = rev.get(choice, "tags_only")
        self.config["immich_description_mode"] = m
        self.engine.config["immich_description_mode"] = m
        if hasattr(self, 'worker') and hasattr(self.worker, 'config'):
            self.worker.config["immich_description_mode"] = m
        save_config(self.config)
        self.status_badge.configure(text=f"● {t('desc_mode_label')} {choice}", text_color="#38bdf8")

    def run_test_batch_5(self):
        if bool(self.chk_date_filter.get()):
            if not self.apply_date_filter(show_error=True):
                self.status_badge.configure(text=f"● {t('date_error_invalid')}", text_color="#f87171")
                return
        self.engine.test_limit_remaining = 5
        if self.engine.paused_by_user or not getattr(self.engine, "is_processing_active", False):
            self.toggle_pause()
        self.status_badge.configure(text=f"● {t('status_test_batch_5')}", text_color="#38bdf8")

    def paste_to_test_input(self):
        try:
            text = self.clipboard_get()
            if text:
                self.test_input.delete(0, 'end')
                self.test_input.insert(0, text.strip())
                self.status_badge.configure(text=f"● {t('status_clipboard_pasted')}", text_color="#38bdf8")
        except Exception:
            self.status_badge.configure(text=f"● {t('status_clipboard_empty')}", text_color="#f87171")

    def run_single_test_photo(self):
        raw = self.test_input.get().strip()
        if not raw:
            self.status_badge.configure(text=f"● {t('status_test_prompt_enter')}", text_color="#f87171")
            return

        # Robust UUID extraction (handles full URLs, query params, raw IDs)
        uuid_match = re.search(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', raw)
        if uuid_match:
            asset_id = uuid_match.group(0)
        elif "/photos/" in raw:
            asset_id = raw.split("/photos/")[-1].split("?")[0].split("/")[0].strip()
        else:
            asset_id = raw

        self.btn_run_test.configure(state="disabled", text=t("btn_recognizing"))
        self.status_badge.configure(text=f"● {t('worker_status_recognizing', fn=asset_id[:12])}", text_color="#38bdf8")


        def task():
            try:
                imm_cfg = self.config.get("immich", {})
                lm_cfg = self.config.get("lm_studio", {})
                immich = ImmichClient(imm_cfg["url"], imm_cfg["api_key"])
                captioner = VlmCaptioner(lm_cfg["url"], lm_cfg["model"], temperature=0.1)

                # 1. Fetch info
                asset = immich.get_asset_info(asset_id)
                
                filename = asset.get('originalFileName', asset_id)
                disp_fn = filename if len(filename) <= 50 else (filename[:26] + "…" + filename[-20:])
                b64_img = immich.download_preview_b64(asset_id, "preview")

                # 2. Recognize
                t0 = time.time()
                cap_lang = self.config.get("caption_language", "ru")
                tags_lang = self.config.get("tags_language", "en")
                tags_cnt = int(self.config.get("tags_count", 15))
                caption_data = captioner.generate_caption(b64_img, desc_lang=cap_lang, tags_lang=tags_lang, tags_count=tags_cnt)
                elapsed = time.time() - t0

                title = caption_data.get("title", "").strip()
                desc_text = caption_data.get("description", "").strip()
                tags = caption_data.get("tags", [])
                if tags_cnt and tags_cnt > 0:
                    tags = tags[:tags_cnt]
                    caption_data["tags"] = tags
                ocr = caption_data.get("ocr", "").strip()

                # 3. Format description
                desc_mode = self.config.get("immich_description_mode", "tags_only")
                immich_desc = format_immich_description(caption_data, mode=desc_mode, desc_lang=cap_lang)

                immich.update_description(asset_id, immich_desc)
                applied_tags = 0
                if tags:
                    applied_tags = immich.apply_tags_to_asset(asset_id, tags)
                    if applied_tags == 0:
                        time.sleep(1.0)
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
                        "write_iptc": self.config.get("write_iptc", True),
                        "timestamp": time.time()
                    }
                    with open(task_file, "w", encoding="utf-8") as tf:
                        json.dump(task_data, tf, ensure_ascii=False, indent=2)

                # 5. Update UI
                self.engine.last_photo_name = filename
                self.engine.last_photo_time = elapsed
                self.engine.last_title = title
                self.engine.last_desc_text = desc_text
                self.engine.last_tags = tags
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

                success_msg = t("worker_status_test_photo_done", fn=disp_fn, elapsed=f"{elapsed:.1f}", count=applied_tags)
                self.engine.status_text = success_msg
                self.engine.status_type = "active"
                self.after(0, lambda: self.status_badge.configure(text=f"● {success_msg}", text_color="#34d399"))
            except Exception as e:
                err_msg = t("worker_status_test_photo_err", err=str(e))
                self.engine.status_text = err_msg
                self.engine.status_type = "error"
                self.after(0, lambda: self.status_badge.configure(text=f"● {err_msg}", text_color="#ef4444"))
            finally:
                self.after(0, lambda: self.btn_run_test.configure(state="normal", text=t("btn_recognize")))

        threading.Thread(target=task, daemon=True).start()

    def change_language(self, choice: str):
        lang = get_code_by_name(choice)
        set_language(lang)
        self.config["ui_language"] = lang
        save_config(self.config)
        self._refresh_language_texts()

    def _refresh_language_texts(self):
        self.title(t("app_title"))
        self.subtitle_lbl.configure(text=t("subtitle", model=self.active_model, gpu=self.gpu_name))
        self._update_btn_pause_state(force=True)
        self.chk_autostart.configure(text=t("autostart"))
        self.lang_opt.set(get_language_name(get_language()))
        self.caption_lang_lbl.configure(text=t("lang_caption_label"))
        self.tags_lang_lbl.configure(text=t("lang_tags_label"))
        if hasattr(self, 'tags_count_lbl'):
            self.tags_count_lbl.configure(text=t("lang_tags_count_label"))
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
        if hasattr(self, 'lbl_title_prefix'):
            self.lbl_title_prefix.configure(text=t("field_title"))
        if hasattr(self, 'lbl_desc_prefix'):
            self.lbl_desc_prefix.configure(text=t("field_desc"))
        if hasattr(self, 'lbl_tags_prefix'):
            self.lbl_tags_prefix.configure(text=t("field_tags"))
        if self.engine.last_photo_name in ("Нет данных", "No data"):
            self.photo_info_lbl.configure(text=t("last_photo_waiting"))
            self.photo_desc_lbl.configure(text=t("last_photo_waiting_desc"))
        else:
            time_lbl = t("photo_time_label")
            self.photo_info_lbl.configure(text=f"{self.engine.last_photo_name} ({time_lbl}: {self.engine.last_photo_time:.1f}s)")
        self.thumb_label.configure(text=t("preview_placeholder"))

        # Date filter & ExifTool controls
        if hasattr(self, 'chk_exiftool'):
            self.chk_exiftool.configure(text=t("setting_exiftool"))
        if hasattr(self, 'chk_iptc'):
            self.chk_iptc.configure(text=t("setting_iptc"))
        if hasattr(self, 'chk_date_filter'):
            self.chk_date_filter.configure(text=t("chk_date_filter"))
        if hasattr(self, 'lbl_date_from'):
            self.lbl_date_from.configure(text=t("date_from_label"))
        if hasattr(self, 'lbl_date_to'):
            self.lbl_date_to.configure(text=t("date_to_label"))
        if hasattr(self, 'chk_photo_slice'):
            self.chk_photo_slice.configure(text=t("chk_photo_slice"))
        if hasattr(self, 'lbl_slice_from'):
            self.lbl_slice_from.configure(text=t("slice_from_label"))
        if hasattr(self, 'lbl_slice_to'):
            self.lbl_slice_to.configure(text=t("slice_to_label"))
        if hasattr(self, 'btn_apply_date'):
            self.btn_apply_date.configure(text=t("btn_apply"))
        if hasattr(self, 'lbl_date_status'):
            if getattr(self.engine, "date_filter_enabled", False):
                df = getattr(self.engine, "date_from_str", "")
                dt = getattr(self.engine, "date_to_str", "")
                if df and not dt:
                    period_str = f"{df} → 1980"
                elif dt and not df:
                    period_str = f"2026 → {dt}"
                else:
                    period_str = f"{df or t('date_period_start')} — {dt or t('date_period_end')}"
                if getattr(self.engine, "photo_slice_enabled", False):
                    sf = getattr(self.engine, "photo_slice_from", 1)
                    st = getattr(self.engine, "photo_slice_to", 5)
                    period_str += f" {t('date_status_slice', slice_from=sf, slice_to=st)}"
                self.lbl_date_status.configure(text=t("date_status_active", period=period_str), text_color="#34d399")
            else:
                self.lbl_date_status.configure(text=t("date_status_no_limit"), text_color="#64748b")

        # Server bar buttons
        if hasattr(self, 'btn_reset_all'):
            if getattr(self.engine, "force_reprocess", False):
                self.btn_reset_all.configure(text=t("btn_reset_all_active", label=t("btn_reset_all")))
            else:
                self.btn_reset_all.configure(text=t("btn_reset_all"))
        if hasattr(self, 'btn_stock_tagger'):
            self.btn_stock_tagger.configure(text=t("btn_stock_tagger"))

    def open_stock_tagger(self):
        script = os.path.join(get_base_dir(), "stock_tagger_app.py")
        if os.path.exists(script):
            subprocess.Popen([sys.executable, script])
        else:
            exe = os.path.join(get_base_dir(), "StockAI_Tagger.exe")
            if os.path.exists(exe):
                subprocess.Popen([exe])

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
            try:
                icon.stop()
            except Exception:
                pass
            try:
                self.destroy()
            except Exception:
                pass
            os._exit(0)

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

    def _update_btn_pause_state(self, force=False):
        status_lower = getattr(self.engine, "status_text", "").lower()
        is_idle_status = (
            not getattr(self.engine, "is_processing_active", False)
            or self.engine.status_type == "info"
            or not getattr(self.engine, "is_running", True)
            or "обработаны" in status_lower
            or "processed" in status_lower
            or "verarbeitet" in status_lower
            or "procesad" in status_lower
            or "traitée" in status_lower
            or "完了" in status_lower
            or "完成" in status_lower
            or "ожидание" in status_lower
            or "waiting" in status_lower
            or "готов" in status_lower
            or "ready" in status_lower
        )
        if getattr(self.engine, "paused_by_user", False):
            new_state = "paused"
        elif is_idle_status:
            new_state = "idle"
        else:
            new_state = "working"

        if new_state != getattr(self, "_btn_pause_state", None) or force:
            self._btn_pause_state = new_state
            if new_state == "paused":
                self.btn_pause.configure(
                    text=t("btn_paused_label"),
                    fg_color="#f59e0b",
                    hover_color="#d97706"
                )
                if hasattr(self, "tray_icon") and self.tray_icon:
                    try:
                        self.tray_icon.icon = get_tray_icon("paused")
                    except Exception:
                        pass
            elif new_state == "idle":
                self.btn_pause.configure(
                    text=t("btn_start"),
                    fg_color="#10b981",
                    hover_color="#059669"
                )
                if hasattr(self, "tray_icon") and self.tray_icon:
                    try:
                        self.tray_icon.icon = get_tray_icon("active")
                    except Exception:
                        pass
            else:  # "working"
                self.btn_pause.configure(
                    text=t("btn_working"),
                    fg_color="#dc2626",
                    hover_color="#ef4444"
                )
                if hasattr(self, "tray_icon") and self.tray_icon:
                    try:
                        self.tray_icon.icon = get_tray_icon("active")
                    except Exception:
                        pass

    def toggle_pause(self):
        # If user is attempting to start or resume processing
        if not getattr(self.engine, "is_processing_active", False) or self.engine.paused_by_user:
            if bool(self.chk_date_filter.get()):
                if not self.apply_date_filter(show_error=True):
                    self.status_badge.configure(text=f"● {t('date_error_invalid')}", text_color="#f87171")
                    return

        if self.engine.paused_by_user:
            self.engine.paused_by_user = False
            self.engine.is_processing_active = True
            self.engine.force_wake = True
        else:
            current_st = getattr(self, "_btn_pause_state", "idle")
            if current_st == "idle" or not getattr(self.engine, "is_processing_active", False):
                self.engine.is_processing_active = True
                self.engine.paused_by_user = False
                self.engine.force_wake = True
                self.engine.status_text = t("status_starting_archive")
                self.engine.status_type = "active"
            else:
                self.engine.paused_by_user = True
        self._update_btn_pause_state(force=True)

    def _update_ui_loop(self):
        # Update Start / Pause / Working Button dynamic state
        self._update_btn_pause_state()

        # Update Status Badge
        status = self.engine.status_text
        st_type = self.engine.status_type
        disp_status = status
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
            time_lbl = t("photo_time_label")
            self.photo_info_lbl.configure(text=f"{self.engine.last_photo_name} ({time_lbl}: {self.engine.last_photo_time:.1f}s)")
            
            # Show structured fields and hide placeholder
            if hasattr(self, "photo_desc_lbl") and self.photo_desc_lbl.winfo_ismapped():
                self.photo_desc_lbl.pack_forget()
            if hasattr(self, "photo_fields_frame") and not self.photo_fields_frame.winfo_ismapped():
                self.photo_fields_frame.pack(fill="x", expand=True, anchor="w", pady=(2, 0))

            t_val = self.engine.last_title or "—"
            if len(t_val) > 120:
                t_val = t_val[:117] + "..."
            self.lbl_title_val.configure(text=t_val)

            d_val = (self.engine.last_desc_text or self.engine.last_description or "—").replace('\n', ' ')
            if len(d_val) > 280:
                d_val = d_val[:277] + "..."
            self.lbl_desc_val.configure(text=d_val)

            tags_list = self.engine.last_tags
            if tags_list:
                tg_val = ", ".join(tags_list)
            else:
                tg_val = "—"
            if len(tg_val) > 260:
                tg_val = tg_val[:257] + "..."
            self.lbl_tags_val.configure(text=tg_val)

            if self.engine.last_thumbnail_pil:
                try:
                    ctk_img = ctk.CTkImage(self.engine.last_thumbnail_pil, size=self.engine.last_thumbnail_pil.size)
                    self.thumb_label.configure(image=ctk_img, text="")
                except Exception:
                    pass

        # Check if force_reprocess completed or was disabled
        is_force = getattr(self.engine, "force_reprocess", False)
        current_btn_txt = self.btn_reset_all.cget("text")
        if not is_force and current_btn_txt != t("btn_reset_all"):
            self.btn_reset_all.configure(
                fg_color="#334155",
                hover_color="#475569",
                text=t("btn_reset_all")
            )

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
                    disp_last = s_last if len(s_last) <= 45 else (s_last[:22] + "…" + s_last[-18:])
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
            c.create_text(w - 14, 12, text=t("gpu_mode_always_on_label"), fill="#10b981", font=("Segoe UI", 9, "bold"), anchor="ne")
        else:
            limit = self.config.get("throttling", {}).get("max_gpu_util_percent", 85)
            y_lim = h - (limit / 100.0 * h)
            c.create_line(0, y_lim, w, y_lim, fill="#854d0e", dash=(4, 3))
            c.create_text(w - 14, y_lim - 7, text=t("gpu_limit_label", limit=limit), fill="#ca8a04", font=("Segoe UI", 9, "bold"), anchor="ne")

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


def kill_duplicate_instances(target_names: list[str]):
    """
    Terminates other running instances of the specified process names,
    keeping only the current process and its parent (PyInstaller bootloader).
    """
    try:
        import psutil
        current_pid = os.getpid()
        parent_pid = None
        try:
            cur_proc = psutil.Process(current_pid)
            parent = cur_proc.parent()
            if parent:
                parent_pid = parent.pid
        except Exception:
            pass

        target_names_lower = {name.lower() for name in target_names}

        for proc in psutil.process_iter(['pid', 'name']):
            try:
                pid = proc.info['pid']
                if pid == current_pid or (parent_pid and pid == parent_pid):
                    continue
                pname = (proc.info['name'] or '').lower()
                if pname in target_names_lower:
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass


if __name__ == '__main__':
    kill_duplicate_instances([
        "ImmichAI_Captioner_Standalone.exe",
        "ImmichCaptioner.exe",
        "ImmichAI_Captioner_Worker.exe",
        "ImmichCaptionWorker.exe"
    ])
    app = MainApp()
    try:
        app.mainloop()
    finally:
        os._exit(0)
