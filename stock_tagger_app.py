# -*- coding: utf-8 -*-
"""
stock_tagger_app.py
StockAI Tagger v1.1 — Microstock AI Metadata Assistant
Batch photo preparation and metadata tagging for microstocks (Adobe Stock, Shutterstock, Freepik).
- English by default + Language switcher (English / Русский)
- Fully responsive layout: wide inspector for comfortable keyword viewing on widescreen displays
- Robust batch execution with error recovery and GPU throttling
- Cached LM Studio model detection without loop polling
- Direct metadata embedding (IPTC, XMP, EXIF) via ExifTool and CSV export
"""

import os
import sys
import time
import json
import threading
import traceback
import queue
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageTk, ImageOps

import stock_engine
from captioner import VlmCaptioner, LANGUAGE_NAMES
from gpu_monitor import is_system_busy

from stock_translations import TRANSLATIONS, SUPPORTED_LANGUAGES, NAME_TO_CODE

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_config.json")

DEFAULT_CONFIG = {
    "ui_language": "en",
    "lm_studio_url": "http://localhost:1234/v1",
    "folder_path": "",
    "recursive": True,
    "skip_tagged": False,
    "desc_language": "en",
    "tags_language": "en",
    "tags_count": 40,
    "write_file": True,
    "write_iptc": True,
    "write_xmp": False,
    "export_csv": True
}

class StockTaggerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config = self.load_config()
        self.current_lang = self.config.get("ui_language", "en")

        self.title(self.t("app_title"))
        self.geometry("1280x820")
        self.minsize(980, 620)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Иконка окна
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
        if os.path.isfile(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        # Состояние приложения
        self.files = []
        self.selected_file_idx = -1
        self.selected_indices = set()
        self.selection_anchor = 0
        self.grid_bottom_spacer = None
        self._syncing_selection = False
        self.is_clearing_meta = False
        self.captioner = None
        self.active_model_name = "Detecting..." if self.current_lang == "en" else "Определение..."
        self.model_online = False

        self.is_processing = False
        self.is_paused = False
        self.stop_requested = False
        self.processing_thread = None

        self.total_processed_session = 0
        self.session_times = []

        self.inspector_thumb_cache = {}
        self.is_scanning_tags = False
        self.stop_tags_scan = False
        self.tags_scan_thread = None

        # Thread-safe UI update queue
        self.ui_queue = queue.Queue()
        self._poll_ui_queue()

        self.init_captioner()
        self.setup_ui()
        self.start_model_monitor()

        # Загрузка сохраненного пути к папке
        if self.config.get("folder_path") and os.path.isdir(self.config.get("folder_path")):
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, self.config.get("folder_path"))

    def run_on_ui(self, func):
        """Thread-safe submission of a callable to the main Tkinter thread."""
        self.ui_queue.put(func)

    def _poll_ui_queue(self):
        """Periodically runs on the main thread to process all queued UI updates."""
        try:
            while not self.ui_queue.empty():
                callback = self.ui_queue.get_nowait()
                try:
                    callback()
                except Exception:
                    traceback.print_exc()
        finally:
            self.after(40, self._poll_ui_queue)

    def t(self, key: str, **kwargs) -> str:
        lang_dict = TRANSLATIONS.get(self.current_lang, TRANSLATIONS["en"])
        val = lang_dict.get(key, TRANSLATIONS["en"].get(key, key))
        if kwargs:
            try:
                return val.format(**kwargs)
            except Exception:
                return val
        return val

    def load_config(self) -> dict:
        cfg = DEFAULT_CONFIG.copy()
        if os.path.isfile(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)
                    cfg.update(user_cfg)
            except Exception:
                pass
        return cfg

    def save_config(self):
        try:
            self.config["ui_language"] = self.current_lang
            self.config["folder_path"] = self.folder_entry.get().strip()
            self.config["recursive"] = self.chk_recursive.get()
            self.config["skip_tagged"] = self.chk_skip_tagged.get()
            self.config["desc_language"] = NAME_TO_CODE.get(self.combo_desc_lang.get(), "en")
            self.config["tags_language"] = NAME_TO_CODE.get(self.combo_tags_lang.get(), "en")
            self.config["tags_count"] = int(self.combo_tags_count.get())
            self.config["write_file"] = self.chk_write_file.get()
            self.config["write_iptc"] = self.chk_write_iptc.get()
            self.config["write_xmp"] = self.chk_write_xmp.get()
            self.config["export_csv"] = self.chk_export_csv.get()

            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def init_captioner(self):
        url = self.config.get("lm_studio_url", "http://localhost:1234/v1")
        self.captioner = VlmCaptioner(url, "")
        try:
            self.active_model_name = self.captioner.resolve_active_model()
            self.model_online = self.captioner.test_connection()
        except Exception:
            self.active_model_name = "LM Studio offline"
            self.model_online = False

    def start_model_monitor(self):
        def monitor_loop():
            while True:
                time.sleep(15)
                # DO NOT poll while batch processing is actively sending images to LM Studio!
                if self.is_processing:
                    continue
                try:
                    active = self.captioner.resolve_active_model(force=False)
                    online = self.captioner.test_connection()
                    self.active_model_name = active
                    self.model_online = online
                    self.run_on_ui(self.update_model_badge)
                except Exception:
                    self.model_online = False
                    self.run_on_ui(self.update_model_badge)

        t = threading.Thread(target=monitor_loop, daemon=True)
        t.start()

    def update_model_badge(self):
        if self.model_online:
            txt = self.t("model_online", model=self.active_model_name)
            color = "#065f46"
            border_color = "#10b981"
        else:
            txt = self.t("model_offline")
            color = "#7f1d1d"
            border_color = "#ef4444"

        self.lbl_model_badge.configure(text=txt, fg_color=color, border_color=border_color)

    def setup_ui(self):
        # 1. Header Frame (Top)
        header_frame = ctk.CTkFrame(self, fg_color="#1e293b", corner_radius=8)
        header_frame.pack(side="top", fill="x", padx=12, pady=(8, 4))

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=12, pady=8)

        self.lbl_header_title = ctk.CTkLabel(
            title_box,
            text=self.t("header_title"),
            font=ctk.CTkFont(size=19, weight="bold"),
            text_color="#f8fafc"
        )
        self.lbl_header_title.pack(anchor="w")

        self.lbl_header_sub = ctk.CTkLabel(
            title_box,
            text=self.t("header_sub"),
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        )
        self.lbl_header_sub.pack(anchor="w")

        # Right side of header: UI Language Dropdown + Model status badge
        header_right = ctk.CTkFrame(header_frame, fg_color="transparent")
        header_right.pack(side="right", padx=12, pady=8)

        # UI Language dropdown (All 8 supported languages)
        self.combo_ui_lang = ctk.CTkOptionMenu(
            header_right,
            values=list(SUPPORTED_LANGUAGES.values()),
            command=self.on_ui_lang_change,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=115,
            height=28,
            fg_color="#334155",
            button_color="#475569",
            button_hover_color="#1e293b",
            dropdown_fg_color="#1e293b"
        )
        self.combo_ui_lang.set(SUPPORTED_LANGUAGES.get(self.current_lang, "English"))
        self.combo_ui_lang.pack(side="right", padx=(8, 0))

        self.lbl_model_badge = ctk.CTkLabel(
            header_right,
            text=self.t("model_checking"),
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#334155",
            corner_radius=14,
            border_width=1,
            border_color="#475569",
            padx=12,
            pady=4,
            text_color="#f8fafc",
            cursor="hand2"
        )
        self.lbl_model_badge.pack(side="right", padx=(0, 6))
        self.lbl_model_badge.bind("<Button-1>", lambda e: self.check_model_click())
        self.update_model_badge()

        # 2. Footer Panel (DOCKED AT BOTTOM FIRST - GUARANTEES VISIBILITY ON ALL SCREENS)
        footer_frame = ctk.CTkFrame(self, fg_color="#1e293b", corner_radius=8)
        footer_frame.pack(side="bottom", fill="x", padx=12, pady=(4, 8))

        # Progress bar
        row_prog = ctk.CTkFrame(footer_frame, fg_color="transparent")
        row_prog.pack(fill="x", padx=12, pady=(8, 3))

        self.prog_bar = ctk.CTkProgressBar(row_prog, fg_color="#0f172a", progress_color="#10b981", height=9)
        self.prog_bar.set(0)
        self.prog_bar.pack(fill="x")

        row_status = ctk.CTkFrame(footer_frame, fg_color="transparent")
        row_status.pack(fill="x", padx=12, pady=(2, 4))

        self.lbl_status_main = ctk.CTkLabel(
            row_status,
            text=self.t("status_ready"),
            font=ctk.CTkFont(size=12),
            text_color="#f8fafc"
        )
        self.lbl_status_main.pack(side="left")

        self.lbl_eta = ctk.CTkLabel(
            row_status,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        )
        self.lbl_eta.pack(side="right")

        # Footer Action Buttons
        row_buttons = ctk.CTkFrame(footer_frame, fg_color="transparent")
        row_buttons.pack(fill="x", padx=12, pady=(0, 8))

        self.btn_start = ctk.CTkButton(
            row_buttons,
            text=self.t("btn_start"),
            command=self.toggle_batch_processing,
            fg_color="#10b981",
            hover_color="#059669",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=135,
            height=32
        )
        self.btn_start.pack(side="left", padx=(0, 6))

        self.btn_tag_selected = ctk.CTkButton(
            row_buttons,
            text=self.t("btn_tag_selected"),
            command=self.tag_selected_photo,
            fg_color="#0284c7",
            hover_color="#0369a1",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=155,
            height=32
        )
        self.btn_tag_selected.pack(side="left", padx=(0, 6))

        self.btn_clear_meta = ctk.CTkButton(
            row_buttons,
            text=self.t("btn_clear_meta"),
            command=self.on_clear_metadata_clicked,
            fg_color="#334155",
            hover_color="#475569",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=145,
            height=32
        )
        self.btn_clear_meta.pack(side="left", padx=(0, 14))

        self.btn_pause = ctk.CTkButton(
            row_buttons,
            text=self.t("btn_pause"),
            command=self.toggle_pause,
            fg_color="#334155",
            hover_color="#475569",
            state="disabled",
            width=85,
            height=32
        )
        self.btn_pause.pack(side="left", padx=(0, 8))

        self.btn_stop = ctk.CTkButton(
            row_buttons,
            text=self.t("btn_stop"),
            command=self.stop_processing,
            fg_color="#ef4444",
            hover_color="#dc2626",
            state="disabled",
            width=80,
            height=32
        )
        self.btn_stop.pack(side="left", padx=(0, 16))

        self.btn_export = ctk.CTkButton(
            row_buttons,
            text=self.t("btn_export_csv"),
            command=self.manual_export_csv,
            fg_color="#334155",
            hover_color="#475569",
            width=120,
            height=32
        )
        self.btn_export.pack(side="left", padx=(0, 8))

        self.btn_open_folder = ctk.CTkButton(
            row_buttons,
            text=self.t("btn_open_folder"),
            command=self.open_current_folder,
            fg_color="#334155",
            hover_color="#475569",
            width=120,
            height=32
        )
        self.btn_open_folder.pack(side="left")

        # 3. Settings Card (Top, below header)
        settings_frame = ctk.CTkFrame(self, fg_color="#1e293b", corner_radius=8)
        settings_frame.pack(side="top", fill="x", padx=12, pady=4)

        # Row 1: Folder + Browse + Scan
        row_folder = ctk.CTkFrame(settings_frame, fg_color="transparent")
        row_folder.pack(fill="x", padx=12, pady=(8, 4))

        self.lbl_folder_title = ctk.CTkLabel(row_folder, text=self.t("lbl_folder"), font=ctk.CTkFont(size=12, weight="bold"), text_color="#e2e8f0")
        self.lbl_folder_title.pack(side="left", padx=(0, 8))

        self.folder_entry = ctk.CTkEntry(
            row_folder,
            placeholder_text=self.t("folder_placeholder"),
            font=ctk.CTkFont(size=12),
            fg_color="#0f172a",
            border_color="#334155"
        )
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_browse = ctk.CTkButton(
            row_folder,
            text=self.t("btn_browse"),
            width=90,
            command=self.browse_folder,
            fg_color="#334155",
            hover_color="#475569"
        )
        self.btn_browse.pack(side="left", padx=(0, 8))

        self.btn_scan = ctk.CTkButton(
            row_folder,
            text=self.t("btn_scan"),
            width=115,
            command=self.start_scan,
            fg_color="#2563eb",
            hover_color="#1d4ed8"
        )
        self.btn_scan.pack(side="left")

        # Row 2: Scan & Tag Options (With full multilingual Title/Desc & Keywords languages)
        row_opts = ctk.CTkFrame(settings_frame, fg_color="transparent")
        row_opts.pack(fill="x", padx=12, pady=(2, 4))

        self.chk_recursive = ctk.CTkCheckBox(row_opts, text=self.t("chk_recursive"), font=ctk.CTkFont(size=11))
        if self.config.get("recursive", True):
            self.chk_recursive.select()
        self.chk_recursive.pack(side="left", padx=(0, 14))

        self.chk_skip_tagged = ctk.CTkCheckBox(row_opts, text=self.t("chk_skip_tagged"), font=ctk.CTkFont(size=11))
        if self.config.get("skip_tagged", True):
            self.chk_skip_tagged.select()
        self.chk_skip_tagged.pack(side="left", padx=(0, 18))

        # Title/Desc Language
        self.lbl_lang_desc = ctk.CTkLabel(row_opts, text=self.t("lbl_lang_desc"), font=ctk.CTkFont(size=11), text_color="#cbd5e1")
        self.lbl_lang_desc.pack(side="left", padx=(0, 4))

        cur_desc_code = self.config.get("desc_language", "en")
        cur_desc_name = SUPPORTED_LANGUAGES.get(cur_desc_code, "English")
        self.combo_desc_lang = ctk.CTkComboBox(
            row_opts,
            values=list(SUPPORTED_LANGUAGES.values()),
            width=105,
            state="readonly",
            command=lambda v: self.save_config()
        )
        self.combo_desc_lang.set(cur_desc_name)
        self.combo_desc_lang.pack(side="left", padx=(0, 14))

        # Keywords Language
        self.lbl_lang_tags = ctk.CTkLabel(row_opts, text=self.t("lbl_lang_tags"), font=ctk.CTkFont(size=11), text_color="#cbd5e1")
        self.lbl_lang_tags.pack(side="left", padx=(0, 4))

        cur_tags_code = self.config.get("tags_language", "en")
        cur_tags_name = SUPPORTED_LANGUAGES.get(cur_tags_code, "English")
        self.combo_tags_lang = ctk.CTkComboBox(
            row_opts,
            values=list(SUPPORTED_LANGUAGES.values()),
            width=105,
            state="readonly",
            command=lambda v: self.save_config()
        )
        self.combo_tags_lang.set(cur_tags_name)
        self.combo_tags_lang.pack(side="left", padx=(0, 14))

        # Keywords count
        self.lbl_tags_count = ctk.CTkLabel(row_opts, text=self.t("lbl_tags_count"), font=ctk.CTkFont(size=11), text_color="#cbd5e1")
        self.lbl_tags_count.pack(side="left", padx=(0, 4))

        self.combo_tags_count = ctk.CTkComboBox(
            row_opts,
            values=["15", "20", "25", "30", "35", "40", "45", "50"],
            width=65,
            state="readonly",
            command=lambda v: self.save_config()
        )
        self.combo_tags_count.set(str(self.config.get("tags_count", 40)))
        self.combo_tags_count.pack(side="left")

        # Row 3: Output embedding checkboxes
        row_embed = ctk.CTkFrame(settings_frame, fg_color="transparent")
        row_embed.pack(fill="x", padx=12, pady=(2, 6))

        self.chk_write_file = ctk.CTkCheckBox(row_embed, text=self.t("chk_write_file"), font=ctk.CTkFont(size=11), command=self.on_toggle_write_file)
        if self.config.get("write_file", True):
            self.chk_write_file.select()
        self.chk_write_file.pack(side="left", padx=(0, 16))

        self.chk_write_iptc = ctk.CTkCheckBox(row_embed, text=self.t("chk_write_iptc"), font=ctk.CTkFont(size=11))
        if self.config.get("write_iptc", True):
            self.chk_write_iptc.select()
        self.chk_write_iptc.pack(side="left", padx=(0, 16))

        self.chk_write_xmp = ctk.CTkCheckBox(row_embed, text=self.t("chk_write_xmp"), font=ctk.CTkFont(size=11))
        if self.config.get("write_xmp", False):
            self.chk_write_xmp.select()
        self.chk_write_xmp.pack(side="left", padx=(0, 16))

        self.chk_export_csv = ctk.CTkCheckBox(row_embed, text=self.t("chk_export_csv"), font=ctk.CTkFont(size=11))
        if self.config.get("export_csv", True):
            self.chk_export_csv.select()
        self.chk_export_csv.pack(side="left")

        # 4. Main Work Area (Split View: Responsive Grid 55% / 45%) - FILLS CENTER
        main_paned = ctk.CTkFrame(self, fg_color="transparent")
        main_paned.pack(side="top", fill="both", expand=True, padx=12, pady=4)
        main_paned.grid_columnconfigure(0, weight=6)  # Left Table
        main_paned.grid_columnconfigure(1, weight=5)  # Right Inspector (Wide & Spacious)
        main_paned.grid_rowconfigure(0, weight=1)

        # 4.1 Left side: File List Table
        left_frame = ctk.CTkFrame(main_paned, fg_color="#1e293b", corner_radius=8)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        table_header = ctk.CTkFrame(left_frame, fg_color="transparent")
        table_header.pack(fill="x", padx=10, pady=(8, 4))

        self.lbl_stats = ctk.CTkLabel(
            table_header,
            text=self.t("stats_summary", total=0, pending=0, done=0, skipped=0, err=0),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#e2e8f0"
        )
        self.lbl_stats.pack(side="left")

        # View Mode Toggle Buttons (List ☰ / Grid ⊞)
        self.view_mode = "list"
        self.btn_view_grid = ctk.CTkButton(
            table_header,
            text="⊞ " + self.t("view_grid"),
            command=lambda: self.set_view_mode("grid"),
            width=68,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569"
        )
        self.btn_view_grid.pack(side="right", padx=(4, 0))

        self.btn_view_list = ctk.CTkButton(
            table_header,
            text="☰ " + self.t("view_list"),
            command=lambda: self.set_view_mode("list"),
            width=68,
            height=24,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#0ea5e9",
            hover_color="#0284c7"
        )
        self.btn_view_list.pack(side="right", padx=(6, 0))

        # Scan tags button (batch metadata indexing)
        self.btn_scan_tags = ctk.CTkButton(
            table_header,
            text="🏷 " + self.t("btn_scan_tags"),
            command=self.toggle_tags_scan,
            width=130,
            height=24,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#334155",
            hover_color="#475569"
        )
        self.btn_scan_tags.pack(side="right", padx=(0, 6))

        # Container for List View
        self.tree_container = tk.Frame(left_frame, bg="#0f172a")
        self.tree_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Stock.Treeview",
            background="#0f172a",
            foreground="#f8fafc",
            fieldbackground="#0f172a",
            rowheight=25,
            font=("Segoe UI", 10),
            borderwidth=0
        )
        style.configure(
            "Stock.Treeview.Heading",
            background="#334155",
            foreground="#f8fafc",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padding=4
        )
        style.map(
            "Stock.Treeview",
            background=[("selected", "#0ea5e9")],
            foreground=[("selected", "#ffffff")]
        )

        columns = ("num", "name", "status", "title", "tags", "time")
        self.tree = ttk.Treeview(
            self.tree_container,
            columns=columns,
            show="headings",
            style="Stock.Treeview",
            selectmode="extended"
        )

        self.update_tree_column_headers()

        scrollbar = ttk.Scrollbar(self.tree_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.tag_configure("pending", foreground="#94a3b8")
        self.tree.tag_configure("processing", foreground="#38bdf8")
        self.tree.tag_configure("done", foreground="#10b981")
        self.tree.tag_configure("skipped", foreground="#64748b")
        self.tree.tag_configure("error", foreground="#ef4444")

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # Container for Grid View (created here, packed on demand)
        self.grid_container = ctk.CTkScrollableFrame(left_frame, fg_color="#0f172a", corner_radius=6)
        self.grid_container.bind("<Configure>", self.on_grid_container_resize)

        self.grid_cards = {}
        self.grid_thumb_cache = {}
        self.last_grid_cols = 0

        # 4.2 Right side: Wide Inspector / Editor Card
        right_frame = ctk.CTkFrame(main_paned, fg_color="#1e293b", corner_radius=8)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        inspector_header = ctk.CTkFrame(right_frame, fg_color="transparent")
        inspector_header.pack(fill="x", padx=12, pady=(8, 4))

        self.lbl_insp_header = ctk.CTkLabel(
            inspector_header,
            text=self.t("inspector_title"),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#e2e8f0"
        )
        self.lbl_insp_header.pack(side="left")

        self.lbl_photo_status = ctk.CTkLabel(
            inspector_header,
            text="—",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#334155",
            corner_radius=10,
            padx=8,
            pady=2,
            text_color="#94a3b8"
        )
        self.lbl_photo_status.pack(side="right")

        # Compact & crisp Image preview (up to 340x140)
        self.thumb_container = ctk.CTkFrame(right_frame, fg_color="#0f172a", corner_radius=6, height=140)
        self.thumb_container.pack(fill="x", padx=12, pady=(2, 4))
        self.thumb_container.pack_propagate(False)

        self.thumb_label = ctk.CTkLabel(
            self.thumb_container,
            text=self.t("no_preview"),
            text_color="#64748b"
        )
        self.thumb_label.pack(expand=True)

        self.lbl_file_details = ctk.CTkLabel(right_frame, text="", font=ctk.CTkFont(size=11), text_color="#94a3b8")
        self.lbl_file_details.pack(anchor="w", padx=12, pady=(0, 2))

        # Title Field
        self.lbl_field_title = ctk.CTkLabel(
            right_frame,
            text=self.t("lbl_title"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1"
        )
        self.lbl_field_title.pack(anchor="w", padx=12, pady=(1, 1))

        self.entry_title = ctk.CTkEntry(
            right_frame,
            font=ctk.CTkFont(size=11),
            fg_color="#0f172a",
            border_color="#334155",
            height=28
        )
        self.entry_title.pack(fill="x", padx=12, pady=(0, 3))

        # Description Field
        self.lbl_field_desc = ctk.CTkLabel(
            right_frame,
            text=self.t("lbl_desc"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1"
        )
        self.lbl_field_desc.pack(anchor="w", padx=12, pady=(1, 1))

        self.text_desc = ctk.CTkTextbox(
            right_frame,
            height=50,
            font=ctk.CTkFont(size=11),
            fg_color="#0f172a",
            border_color="#334155",
            border_width=1,
            wrap="word"
        )
        self.text_desc.pack(fill="x", padx=12, pady=(0, 3))

        # Keywords Field (Expanded and spacious for 40-50 tags)
        kw_box = ctk.CTkFrame(right_frame, fg_color="transparent")
        kw_box.pack(fill="x", padx=12, pady=(1, 1))

        self.lbl_field_kw = ctk.CTkLabel(
            kw_box,
            text=self.t("lbl_keywords"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1"
        )
        self.lbl_field_kw.pack(side="left")

        self.lbl_kw_count = ctk.CTkLabel(
            kw_box,
            text=self.t("tags_count_label", count=0),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38bdf8"
        )
        self.lbl_kw_count.pack(side="right")

        self.text_tags = ctk.CTkTextbox(
            right_frame,
            height=105,
            font=ctk.CTkFont(size=11),
            fg_color="#0f172a",
            border_color="#334155",
            border_width=1,
            wrap="word"
        )
        self.text_tags.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        # Action Buttons in Card
        btn_box = ctk.CTkFrame(right_frame, fg_color="transparent")
        btn_box.pack(fill="x", padx=12, pady=(0, 8))

        self.btn_save_photo = ctk.CTkButton(
            btn_box,
            text=self.t("btn_save_photo"),
            command=self.save_current_photo_metadata,
            fg_color="#059669",
            hover_color="#047857",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=28
        )
        self.btn_save_photo.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_re_tag = ctk.CTkButton(
            btn_box,
            text=self.t("btn_re_tag"),
            command=self.re_tag_current_photo,
            fg_color="#334155",
            hover_color="#475569",
            width=120,
            font=ctk.CTkFont(size=11),
            height=28
        )
        self.btn_re_tag.pack(side="right")

    def update_tree_column_headers(self):
        self.tree.heading("num", text=self.t("col_num"))
        self.tree.heading("name", text=self.t("col_file"))
        self.tree.heading("status", text=self.t("col_status"))
        self.tree.heading("title", text=self.t("col_title"))
        self.tree.heading("tags", text=self.t("col_tags"))
        self.tree.heading("time", text=self.t("col_time"))

        self.tree.column("num", width=36, anchor="center")
        self.tree.column("name", width=190, anchor="w")
        self.tree.column("status", width=95, anchor="center")
        self.tree.column("title", width=220, anchor="w")
        self.tree.column("tags", width=55, anchor="center")
        self.tree.column("time", width=55, anchor="center")

    def on_ui_lang_change(self, choice: str):
        new_lang = NAME_TO_CODE.get(choice, "en")
        if new_lang == self.current_lang:
            return
        self.current_lang = new_lang
        self.save_config()

        # Update all UI texts
        self.title(self.t("app_title"))
        self.lbl_header_title.configure(text=self.t("header_title"))
        self.lbl_header_sub.configure(text=self.t("header_sub"))
        self.update_model_badge()

        self.lbl_folder_title.configure(text=self.t("lbl_folder"))
        self.folder_entry.configure(placeholder_text=self.t("folder_placeholder"))
        self.btn_browse.configure(text=self.t("btn_browse"))
        self.btn_scan.configure(text=self.t("btn_scan"))

        self.chk_recursive.configure(text=self.t("chk_recursive"))
        self.chk_skip_tagged.configure(text=self.t("chk_skip_tagged"))
        self.lbl_lang_desc.configure(text=self.t("lbl_lang_desc"))
        self.lbl_lang_tags.configure(text=self.t("lbl_lang_tags"))
        self.lbl_tags_count.configure(text=self.t("lbl_tags_count"))

        self.chk_write_file.configure(text=self.t("chk_write_file"))
        self.chk_write_iptc.configure(text=self.t("chk_write_iptc"))
        self.chk_write_xmp.configure(text=self.t("chk_write_xmp"))
        self.chk_export_csv.configure(text=self.t("chk_export_csv"))

        self.lbl_insp_header.configure(text=self.t("inspector_title"))
        self.lbl_field_title.configure(text=self.t("lbl_title"))
        self.lbl_field_desc.configure(text=self.t("lbl_desc"))
        self.lbl_field_kw.configure(text=self.t("lbl_keywords"))
        self.btn_save_photo.configure(text=self.t("btn_save_photo"))
        self.btn_re_tag.configure(text=self.t("btn_re_tag"))

        if not self.is_processing:
            self.btn_start.configure(text=self.t("btn_start"))
            self.lbl_status_main.configure(text=self.t("status_ready"))
        else:
            self.btn_start.configure(text=self.t("btn_processing"))

        self.btn_pause.configure(text=self.t("btn_pause") if not self.is_paused else self.t("btn_resume"))
        self.btn_stop.configure(text=self.t("btn_stop"))
        self.btn_export.configure(text=self.t("btn_export_csv"))
        self.btn_open_folder.configure(text=self.t("btn_open_folder"))
        self.btn_tag_selected.configure(text=self.t("btn_tag_selected"))
        self.btn_clear_meta.configure(text=self.t("btn_clear_meta"))
        self.btn_view_list.configure(text="☰ " + self.t("view_list"))
        self.btn_view_grid.configure(text="⊞ " + self.t("view_grid"))
        self.sync_selection_ui()
        if not getattr(self, "is_scanning_tags", False):
            self.btn_scan_tags.configure(text="🏷 " + self.t("btn_scan_tags"))
        else:
            self.btn_scan_tags.configure(text="⏹ " + self.t("btn_stop_scan_tags"))

        self.update_tree_column_headers()
        self.update_stats_label()

        # Update table rows status text
        for idx, f in enumerate(self.files):
            st_text = self.get_status_display(f["status"])
            t_cnt = len(f.get("tags", []))
            tags_disp = str(t_cnt) if t_cnt > 0 else ""
            t_disp = f"{f['time_sec']:.1f}s" if f["time_sec"] > 0 else ""
            self.tree.item(str(idx), values=(idx + 1, f["rel_path"], st_text, f.get("title", ""), tags_disp, t_disp))

        if self.selected_file_idx >= 0 and self.selected_file_idx < len(self.files):
            self.display_photo_inspector(self.selected_file_idx)

    def on_toggle_write_file(self):
        val = self.chk_write_file.get()
        self.chk_write_iptc.configure(state="normal" if val else "disabled")

    def check_model_click(self):
        self.lbl_model_badge.configure(text=self.t("model_checking"))
        self.init_captioner()
        self.update_model_badge()

    def browse_folder(self):
        cur = self.folder_entry.get().strip() or os.getcwd()
        chosen = filedialog.askdirectory(initialdir=cur, title="Select photo folder")
        if chosen:
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, chosen)
            self.save_config()
            self.start_scan()

    def start_scan(self):
        folder = self.folder_entry.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Warning", self.t("warn_folder_not_found"))
            return

        self.save_config()
        self.btn_scan.configure(state="disabled", text=self.t("btn_scanning"))
        self.lbl_status_main.configure(text=f"Scanning {os.path.basename(folder)}...")

        def scan_task():
            recurse = self.chk_recursive.get()
            skip_tag = self.chk_skip_tagged.get()

            def progress_cb(cur, tot):
                self.run_on_ui(lambda: self.lbl_status_main.configure(text=self.t("checking_meta", cur=cur, tot=tot)))

            files = stock_engine.scan_stock_folder(folder, recursive=recurse, skip_tagged=skip_tag, on_progress=progress_cb)
            self.run_on_ui(lambda: self.finish_scan(files))

        t = threading.Thread(target=scan_task, daemon=True)
        t.start()

    def finish_scan(self, files: list[dict]):
        self.files = files
        self.btn_scan.configure(state="normal", text=self.t("btn_scan"))
        self.grid_thumb_cache.clear()
        self.inspector_thumb_cache.clear()
        self._thumb_gen = getattr(self, "_thumb_gen", 0) + 1
        self.is_scanning_tags = False
        self.stop_tags_scan = False
        self.btn_scan_tags.configure(
            text="🏷 " + self.t("btn_scan_tags"),
            fg_color="#334155",
            hover_color="#475569",
            state="normal"
        )

        for item in self.tree.get_children():
            self.tree.delete(item)

        total = len(self.files)
        pending = sum(1 for f in self.files if f["status"] == "pending")
        skipped = sum(1 for f in self.files if f["status"] == "skipped")

        for idx, f in enumerate(self.files):
            st_text = self.get_status_display(f["status"])
            t_cnt = len(f.get("tags", []))
            tags_disp = str(t_cnt) if t_cnt > 0 else ""
            t_disp = f"{f['time_sec']:.1f}s" if f["time_sec"] > 0 else ""

            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(idx + 1, f["rel_path"], st_text, f.get("title", ""), tags_disp, t_disp),
                tags=(f["status"],)
            )

        self.update_stats_label()
        self.lbl_status_main.configure(text=f"Total: {total} | Ready: {pending} | Skipped: {skipped}")
        self.prog_bar.set(0)

        if self.view_mode == "grid":
            self.render_grid_view()

        if self.files:
            self.selected_indices = {0}
            self.selection_anchor = 0
            self.select_photo(0)
        else:
            self.selected_indices = set()
            self.selection_anchor = 0

    def get_status_display(self, st: str) -> str:
        mapping = {
            "pending": self.t("st_pending"),
            "processing": self.t("st_processing"),
            "done": self.t("st_done"),
            "skipped": self.t("st_skipped"),
            "error": self.t("st_error")
        }
        return mapping.get(st, st)

    def update_stats_label(self):
        total = len(self.files)
        pending = sum(1 for f in self.files if f["status"] == "pending")
        done = sum(1 for f in self.files if f["status"] == "done")
        skipped = sum(1 for f in self.files if f["status"] == "skipped")
        err = sum(1 for f in self.files if f["status"] == "error")

        self.lbl_stats.configure(
            text=self.t("stats_summary", total=total, pending=pending, done=done, skipped=skipped, err=err)
        )

    def on_tree_select(self, event):
        if getattr(self, "_syncing_selection", False):
            return
        sel = self.tree.selection()
        if not sel:
            return
        indices = [int(i) for i in sel]
        self.selected_indices = set(indices)
        if self.selected_file_idx not in self.selected_indices:
            self.selected_file_idx = indices[-1]
        self.selection_anchor = self.selected_file_idx
        self.sync_selection_ui(from_tree=True)
        self.display_photo_inspector(self.selected_file_idx)

    def display_photo_inspector(self, idx: int):
        if idx < 0 or idx >= len(self.files):
            return
        self.selected_file_idx = idx
        item = self.files[idx]
        file_path = item["path"]

        # 0. Header with selection count badge
        sel_cnt = len(self.selected_indices)
        if sel_cnt > 1:
            self.lbl_insp_header.configure(text=f"{self.t('inspector_title')} ({self.t('insp_selected_count', count=sel_cnt)})")
        else:
            self.lbl_insp_header.configure(text=self.t("inspector_title"))

        # 1. Update text fields and details immediately (0 ms lag!)
        self.lbl_photo_status.configure(
            text=self.get_status_display(item["status"]),
            text_color="#10b981" if item["status"] == "done" else ("#64748b" if item["status"] == "skipped" else "#94a3b8")
        )

        sz_mb = item["size_bytes"] / (1024 * 1024)
        self.lbl_file_details.configure(text=f"{item['name']} • {sz_mb:.2f} MB")

        # Title
        self.entry_title.delete(0, "end")
        self.entry_title.insert(0, item.get("title", ""))

        # Description
        self.text_desc.delete("1.0", "end")
        self.text_desc.insert("1.0", item.get("description", ""))

        # Keywords
        tags = item.get("tags", [])
        tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
        self.text_tags.delete("1.0", "end")
        self.text_tags.insert("1.0", tags_str)
        self.lbl_kw_count.configure(text=self.t("tags_count_label", count=len(tags)))

        # 2. Thumbnail with LRU caching and fast draft decode
        if file_path in self.inspector_thumb_cache:
            ctk_img = self.inspector_thumb_cache[file_path]
            self.thumb_label.configure(image=ctk_img, text="")
            self.thumb_label.image = ctk_img
        else:
            try:
                with Image.open(file_path) as img:
                    try:
                        img.draft('RGB', (720, 350))
                    except Exception:
                        pass
                    img = ImageOps.exif_transpose(img)
                    img.thumbnail((360, 175), Image.Resampling.LANCZOS)
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                    self.inspector_thumb_cache[file_path] = ctk_img
                    self.thumb_label.configure(image=ctk_img, text="")
                    self.thumb_label.image = ctk_img
            except Exception:
                self.thumb_label.configure(image=None, text=self.t("no_preview"))

        # 3. Asynchronous on-demand metadata fallback (NEVER blocks the UI thread!)
        if not item.get("tags") and not item.get("title") and not item.get("_meta_checked"):
            item["_meta_checked"] = True
            def fetch_meta_worker(target_idx=idx, p=file_path):
                try:
                    meta = stock_engine.read_image_metadata(p)
                    def update_meta_ui():
                        if target_idx < len(self.files) and self.files[target_idx]["path"] == p:
                            it = self.files[target_idx]
                            it["title"] = meta.get("title", "")
                            it["description"] = meta.get("description", "")
                            it["tags"] = meta.get("tags", [])
                            if meta.get("is_tagged") and it["status"] == "pending":
                                it["status"] = "done"
                                self.update_single_file_ui(target_idx)
                                self.update_stats_label()

                            # If still selected by user, update inspector fields
                            if self.selected_file_idx == target_idx:
                                self.entry_title.delete(0, "end")
                                self.entry_title.insert(0, it["title"])
                                self.text_desc.delete("1.0", "end")
                                self.text_desc.insert("1.0", it["description"])
                                self.text_tags.delete("1.0", "end")
                                self.text_tags.insert("1.0", ", ".join(it["tags"]))
                                self.lbl_kw_count.configure(text=self.t("tags_count_label", count=len(it["tags"])))
                                self.lbl_photo_status.configure(
                                    text=self.get_status_display(it["status"]),
                                    text_color="#10b981" if it["status"] == "done" else "#94a3b8"
                                )
                    self.run_on_ui(update_meta_ui)
                except Exception:
                    pass

            threading.Thread(target=fetch_meta_worker, daemon=True).start()

    def save_current_photo_metadata(self):
        if self.selected_file_idx < 0 or self.selected_file_idx >= len(self.files):
            return
        item = self.files[self.selected_file_idx]

        new_title = self.entry_title.get().strip()
        new_desc = self.text_desc.get("1.0", "end").strip()
        raw_tags_str = self.text_tags.get("1.0", "end").strip()

        tags_list = [t.strip() for t in raw_tags_str.split(",") if t.strip()]

        item["title"] = new_title
        item["description"] = new_desc
        item["tags"] = tags_list

        write_file = self.chk_write_file.get()
        write_iptc = self.chk_write_iptc.get()
        write_xmp = self.chk_write_xmp.get()

        ok = stock_engine.apply_stock_metadata(
            item["path"],
            title=new_title,
            description=new_desc,
            tags=tags_list,
            write_file=write_file,
            write_xmp=write_xmp,
            write_iptc=write_iptc
        )

        if ok:
            item["status"] = "done"
            t_disp = f"{item['time_sec']:.1f}s" if item['time_sec'] > 0 else ""
            self.tree.item(
                str(self.selected_file_idx),
                values=(self.selected_file_idx + 1, item["rel_path"], self.t("st_done"), new_title, len(tags_list), t_disp),
                tags=("done",)
            )
            self.update_stats_label()
            self.update_single_file_ui(self.selected_file_idx)
            self.lbl_status_main.configure(text=self.t("msg_saved_ok", name=item["name"]))
        else:
            messagebox.showerror("Error", self.t("msg_save_err", name=item["name"]))

    def re_tag_current_photo(self):
        if self.selected_file_idx < 0 or self.selected_file_idx >= len(self.files):
            return
        item = self.files[self.selected_file_idx]

        if not self.model_online:
            messagebox.showwarning("Warning", self.t("warn_no_lm_studio"))
            return

        self.btn_re_tag.configure(state="disabled", text=self.t("btn_re_tagging"))
        self.lbl_status_main.configure(text=self.t("analyzing_vlm", name=item["name"]))

        def task():
            desc_lang = NAME_TO_CODE.get(self.combo_desc_lang.get(), "en")
            tags_lang = NAME_TO_CODE.get(self.combo_tags_lang.get(), "en")
            tags_cnt = int(self.combo_tags_count.get())
            try:
                res = stock_engine.tag_stock_image(
                    item["path"],
                    self.captioner,
                    desc_lang=desc_lang,
                    tags_lang=tags_lang,
                    tags_count=tags_cnt
                )
                item["title"] = res["title"]
                item["description"] = res["description"]
                item["tags"] = res["tags"]
                item["time_sec"] = res["time_sec"]

                write_file = self.chk_write_file.get()
                write_iptc = self.chk_write_iptc.get()
                write_xmp = self.chk_write_xmp.get()

                stock_engine.apply_stock_metadata(
                    item["path"],
                    title=res["title"],
                    description=res["description"],
                    tags=res["tags"],
                    write_file=write_file,
                    write_xmp=write_xmp,
                    write_iptc=write_iptc
                )
                item["status"] = "done"
                self.run_on_ui(lambda: self.on_re_tag_finish(True, res["time_sec"]))
            except Exception as e:
                item["error"] = str(e)
                self.run_on_ui(lambda: self.on_re_tag_finish(False, 0.0))

        threading.Thread(target=task, daemon=True).start()

    def on_re_tag_finish(self, success: bool, elapsed: float):
        self.btn_re_tag.configure(state="normal", text=self.t("btn_re_tag"))
        if success:
            item = self.files[self.selected_file_idx]
            self.display_photo_inspector(self.selected_file_idx)
            self.tree.item(
                str(self.selected_file_idx),
                values=(self.selected_file_idx + 1, item["rel_path"], self.t("st_done"), item["title"], len(item["tags"]), f"{elapsed:.1f}s"),
                tags=("done",)
            )
            self.update_stats_label()
            self.update_single_file_ui(self.selected_file_idx)
            self.lbl_status_main.configure(text=self.t("tagged_single_ok", name=item["name"], elapsed=elapsed, tags=len(item["tags"])))
        else:
            messagebox.showerror("Error", self.t("tagged_single_err"))

    # ------------------ Grid View & Single Selection Methods ------------------

    def set_view_mode(self, mode: str):
        if mode == self.view_mode:
            return
        self.view_mode = mode
        if mode == "list":
            self.btn_view_list.configure(fg_color="#0ea5e9", font=ctk.CTkFont(size=11, weight="bold"))
            self.btn_view_grid.configure(fg_color="#334155", font=ctk.CTkFont(size=11))
            self.grid_container.pack_forget()
            self.tree_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))
            if self.selected_indices and self.files:
                self._syncing_selection = True
                try:
                    iids = [str(i) for i in sorted(self.selected_indices) if self.tree.exists(str(i))]
                    self.tree.selection_set(iids)
                    if self.selected_file_idx >= 0 and self.tree.exists(str(self.selected_file_idx)):
                        self.tree.see(str(self.selected_file_idx))
                except Exception:
                    pass
                finally:
                    self._syncing_selection = False
        elif mode == "grid":
            self.btn_view_grid.configure(fg_color="#0ea5e9", font=ctk.CTkFont(size=11, weight="bold"))
            self.btn_view_list.configure(fg_color="#334155", font=ctk.CTkFont(size=11))
            self.tree_container.pack_forget()
            self.grid_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))
            self.render_grid_view()

    def get_grid_thumbnail(self, path: str, idx: int):
        if idx in self.grid_thumb_cache:
            return self.grid_thumb_cache[idx]
        try:
            with Image.open(path) as img:
                try:
                    img.draft('RGB', (280, 190))
                except Exception:
                    pass
                img = ImageOps.exif_transpose(img)
                img.thumbnail((140, 95), Image.Resampling.LANCZOS)
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                self.grid_thumb_cache[idx] = ctk_img
                return ctk_img
        except Exception:
            return None

    def render_grid_view(self):
        for c in self.grid_container.winfo_children():
            c.destroy()
        self.grid_cards.clear()

        if not self.files:
            return

        w = self.grid_container.winfo_width()
        scaling = getattr(self.grid_container, "_get_widget_scaling", lambda: 1.0)()
        card_pitch = max(100, int(156 * scaling))
        pad = int(45 * scaling)
        cols = max(1, min(8, int((w - pad) // card_pitch)))
        self.last_grid_cols = cols

        for idx, item in enumerate(self.files):
            ctk_img = self.grid_thumb_cache.get(idx)
            is_sel = (idx in self.selected_indices)
            is_active = (idx == self.selected_file_idx)

            card = ctk.CTkFrame(
                self.grid_container,
                fg_color="#1e293b",
                corner_radius=6,
                border_width=2 if is_sel else 1,
                border_color=("#38bdf8" if is_active else "#0ea5e9") if is_sel else "#334155",
                cursor="hand2",
                width=148,
                height=130
            )
            card.pack_propagate(False)

            # Thumbnail container (dark frame well, centered uncropped preview)
            t_box = ctk.CTkFrame(card, fg_color="#090d16", corner_radius=4, width=140, height=95)
            t_box.pack(padx=4, pady=(4, 2))
            t_box.pack_propagate(False)

            t_lbl = ctk.CTkLabel(t_box, image=ctk_img, text="" if ctk_img else "")
            t_lbl.place(relx=0.5, rely=0.5, anchor="center")

            # Status badge (Green checkmark for processed; no clocks on untagged photos)
            badge_lbl = ctk.CTkLabel(
                t_box,
                text="✓",
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color="#10b981",
                text_color="#ffffff",
                width=20,
                height=20,
                corner_radius=4
            )
            st = item.get("status", "pending")
            if st == "done":
                badge_lbl.place(x=4, y=4)
            elif st == "processing":
                badge_lbl.configure(text="●", fg_color="#0284c7")
                badge_lbl.place(x=4, y=4)
            elif st == "error":
                badge_lbl.configure(text="✕", fg_color="#ef4444")
                badge_lbl.place(x=4, y=4)

            # Name label (strictly filename, clean and centered)
            name_lbl = ctk.CTkLabel(
                card,
                text=item["name"],
                font=ctk.CTkFont(size=11),
                text_color="#ffffff" if is_sel else "#cbd5e1"
            )
            name_lbl.pack(fill="x", padx=4, pady=(2, 4))

            def make_cb(i):
                return lambda e: self.handle_card_click(e, i)

            cb = make_cb(idx)
            for widget in (card, t_box, t_lbl, badge_lbl, name_lbl):
                widget.bind("<Button-1>", cb)

            card.grid(row=idx // cols, column=idx % cols, padx=4, pady=4, sticky="nsew")

            self.grid_cards[idx] = {
                "card": card,
                "t_box": t_box,
                "t_lbl": t_lbl,
                "badge": badge_lbl,
                "name": name_lbl
            }

        # Add generous bottom spacer so the last row and filenames are 100% visible
        last_row = ((len(self.files) - 1) // cols) if self.files else 0
        self.grid_bottom_spacer = ctk.CTkFrame(self.grid_container, height=60, fg_color="transparent")
        self.grid_bottom_spacer.grid(row=last_row + 1, column=0, columnspan=cols, pady=(0, 20))

        def ensure_scroll():
            try:
                self.grid_container.update_idletasks()
                bbox = self.grid_container._parent_canvas.bbox("all")
                if bbox:
                    self.grid_container._parent_canvas.configure(scrollregion=(0, 0, bbox[2], bbox[3] + 40))
            except Exception:
                pass
        self.after(60, ensure_scroll)

        # Background thumbnail loading for uncached cards
        self._thumb_gen = getattr(self, "_thumb_gen", 0) + 1
        cur_gen = self._thumb_gen
        missing_thumbs = [i for i in range(len(self.files)) if i not in self.grid_thumb_cache]
        if missing_thumbs:
            def load_thumbs_worker(gen_id):
                for i in missing_thumbs:
                    if getattr(self, "_thumb_gen", 0) != gen_id:
                        return
                    if i >= len(self.files):
                        continue
                    it = self.files[i]
                    thumb = self.get_grid_thumbnail(it["path"], i)
                    if thumb and getattr(self, "_thumb_gen", 0) == gen_id:
                        def apply_t(idx_to_update=i, t_img=thumb):
                            if idx_to_update in self.grid_cards:
                                self.grid_cards[idx_to_update]["t_lbl"].configure(image=t_img)
                        self.run_on_ui(apply_t)

            threading.Thread(target=load_thumbs_worker, args=(cur_gen,), daemon=True).start()

    def update_grid_card_status(self, idx: int):
        if idx not in self.grid_cards:
            return
        item = self.files[idx]
        card_info = self.grid_cards[idx]
        badge = card_info["badge"]
        card = card_info["card"]
        name_lbl = card_info["name"]

        is_sel = (idx in self.selected_indices)
        is_active = (idx == self.selected_file_idx)
        card.configure(
            border_color=("#38bdf8" if is_active else "#0ea5e9") if is_sel else "#334155",
            border_width=2 if is_sel else 1
        )
        name_lbl.configure(text_color="#ffffff" if is_sel else "#cbd5e1")

        st = item.get("status", "pending")
        if st == "done":
            badge.configure(text="✓", fg_color="#10b981", text_color="#ffffff")
            badge.place(x=4, y=4)
        elif st == "processing":
            badge.configure(text="●", fg_color="#0284c7", text_color="#ffffff")
            badge.place(x=4, y=4)
        elif st == "error":
            badge.configure(text="✕", fg_color="#ef4444", text_color="#ffffff")
            badge.place(x=4, y=4)
        else:
            badge.place_forget()

    def on_grid_container_resize(self, event):
        if not self.grid_cards:
            return
        w = event.width
        scaling = getattr(self.grid_container, "_get_widget_scaling", lambda: 1.0)()
        card_pitch = max(100, int(156 * scaling))
        pad = int(45 * scaling)
        new_cols = max(1, min(8, int((w - pad) // card_pitch)))
        if new_cols != self.last_grid_cols:
            self.last_grid_cols = new_cols
            for idx in sorted(self.grid_cards.keys()):
                card = self.grid_cards[idx]["card"]
                card.grid(row=idx // new_cols, column=idx % new_cols, padx=4, pady=4, sticky="nsew")

            if getattr(self, "grid_bottom_spacer", None):
                last_row = ((len(self.files) - 1) // new_cols) if self.files else 0
                self.grid_bottom_spacer.grid(row=last_row + 1, column=0, columnspan=new_cols, pady=(0, 20))

            def ensure_scroll():
                try:
                    self.grid_container.update_idletasks()
                    bbox = self.grid_container._parent_canvas.bbox("all")
                    if bbox:
                        self.grid_container._parent_canvas.configure(scrollregion=(0, 0, bbox[2], bbox[3] + 40))
                except Exception:
                    pass
            self.after(50, ensure_scroll)

    def handle_card_click(self, event, idx: int):
        if idx < 0 or idx >= len(self.files):
            return

        is_shift = bool(event.state & 0x0001)
        is_ctrl = bool(event.state & 0x0004)

        if is_shift:
            anchor = getattr(self, "selection_anchor", 0)
            start = min(anchor, idx)
            end = max(anchor, idx)
            self.selected_indices = set(range(start, end + 1))
            self.selected_file_idx = idx
        elif is_ctrl:
            if idx in self.selected_indices:
                self.selected_indices.remove(idx)
                if not self.selected_indices:
                    self.selected_indices.add(idx)
            else:
                self.selected_indices.add(idx)
            self.selected_file_idx = idx
            self.selection_anchor = idx
        else:
            self.selected_indices = {idx}
            self.selected_file_idx = idx
            self.selection_anchor = idx

        self.sync_selection_ui(from_tree=False)
        self.display_photo_inspector(self.selected_file_idx)

    def select_photo(self, idx: int):
        if idx < 0 or idx >= len(self.files):
            return
        self.selected_indices = {idx}
        self.selected_file_idx = idx
        self.selection_anchor = idx
        self.sync_selection_ui(from_tree=False)
        self.display_photo_inspector(idx)

    def sync_selection_ui(self, from_tree: bool = False):
        # 1. Update Grid card borders and styles
        for i, card_info in self.grid_cards.items():
            is_sel = (i in self.selected_indices)
            is_active = (i == self.selected_file_idx)
            card = card_info["card"]
            name_lbl = card_info["name"]

            if is_sel:
                card.configure(
                    border_color="#38bdf8" if is_active else "#0ea5e9",
                    border_width=2
                )
                name_lbl.configure(text_color="#ffffff")
            else:
                card.configure(
                    border_color="#334155",
                    border_width=1
                )
                name_lbl.configure(text_color="#cbd5e1")

        # 2. Sync to Treeview if not triggered from Treeview
        if not from_tree and self.tree.get_children():
            self._syncing_selection = True
            try:
                iids = [str(i) for i in sorted(self.selected_indices) if self.tree.exists(str(i))]
                self.tree.selection_set(iids)
                if self.selected_file_idx >= 0 and self.tree.exists(str(self.selected_file_idx)):
                    self.tree.see(str(self.selected_file_idx))
            except Exception:
                pass
            finally:
                self._syncing_selection = False

        # 3. Dynamic text on action buttons
        sel_cnt = len(self.selected_indices)
        if sel_cnt > 1:
            self.btn_tag_selected.configure(text=self.t("btn_tag_selected_multi", count=sel_cnt))
            self.btn_clear_meta.configure(text=self.t("btn_clear_selected", count=sel_cnt))
        else:
            self.btn_tag_selected.configure(text=self.t("btn_tag_selected"))
            self.btn_clear_meta.configure(text=self.t("btn_clear_meta"))

    def tag_selected_photo(self):
        if not self.selected_indices:
            messagebox.showinfo("Info", self.t("warn_no_photo_selected"))
            return

        if self.is_processing:
            messagebox.showwarning("Warning", "Batch processing is already running.")
            return

        if not self.model_online:
            messagebox.showwarning("Warning", self.t("warn_no_lm_studio"))
            return

        targets = sorted(list(self.selected_indices))
        if len(targets) == 1:
            # Single photo processing
            idx = targets[0]
            item = self.files[idx]
            self.btn_tag_selected.configure(state="disabled", text=self.t("btn_re_tagging"))
            self.btn_start.configure(state="disabled")
            self.btn_re_tag.configure(state="disabled")
            self.btn_clear_meta.configure(state="disabled")
            self.lbl_status_main.configure(text=self.t("analyzing_vlm", name=item["name"]))

            def single_task():
                desc_lang = NAME_TO_CODE.get(self.combo_desc_lang.get(), "en")
                tags_lang = NAME_TO_CODE.get(self.combo_tags_lang.get(), "en")
                tags_cnt = int(self.combo_tags_count.get())
                try:
                    res = stock_engine.tag_stock_image(
                        item["path"],
                        self.captioner,
                        desc_lang=desc_lang,
                        tags_lang=tags_lang,
                        tags_count=tags_cnt
                    )
                    item["title"] = res["title"]
                    item["description"] = res["description"]
                    item["tags"] = res["tags"]
                    item["time_sec"] = res["time_sec"]

                    write_file = self.chk_write_file.get()
                    write_iptc = self.chk_write_iptc.get()
                    write_xmp = self.chk_write_xmp.get()

                    stock_engine.apply_stock_metadata(
                        item["path"],
                        title=res["title"],
                        description=res["description"],
                        tags=res["tags"],
                        write_file=write_file,
                        write_xmp=write_xmp,
                        write_iptc=write_iptc
                    )
                    item["status"] = "done"
                    self.run_on_ui(lambda: self.on_tag_selected_finish(True, res["time_sec"]))
                except Exception as e:
                    item["error"] = str(e)
                    self.run_on_ui(lambda: self.on_tag_selected_finish(False, 0.0))

            threading.Thread(target=single_task, daemon=True).start()

        else:
            # Batch of selected photos
            self.is_processing = True
            self.is_paused = False
            self.stop_requested = False

            self.btn_start.configure(state="disabled")
            self.btn_tag_selected.configure(state="disabled", text=self.t("btn_processing"))
            self.btn_clear_meta.configure(state="disabled")
            self.btn_scan.configure(state="disabled")
            self.btn_pause.configure(state="normal", text=self.t("btn_pause"), fg_color="#334155")
            self.btn_stop.configure(state="normal")

            def multi_task():
                desc_lang = NAME_TO_CODE.get(self.combo_desc_lang.get(), "en")
                tags_lang = NAME_TO_CODE.get(self.combo_tags_lang.get(), "en")
                tags_cnt = int(self.combo_tags_count.get())
                write_file = self.chk_write_file.get()
                write_iptc = self.chk_write_iptc.get()
                write_xmp = self.chk_write_xmp.get()

                tot = len(targets)
                done_cnt = 0

                for step_i, idx in enumerate(targets):
                    if self.stop_requested:
                        break
                    while self.is_paused:
                        if self.stop_requested:
                            break
                        time.sleep(0.25)
                    if self.stop_requested:
                        break

                    item = self.files[idx]
                    item["status"] = "processing"
                    self.run_on_ui(lambda i=idx, it=item: self.update_tree_row(i, self.t("st_processing"), "processing", it.get("title", ""), 0, 0))
                    self.run_on_ui(lambda it=item, s=step_i+1, t=tot: self.lbl_status_main.configure(text=f"[{s}/{t}] {self.t('analyzing_vlm', name=it['name'])}"))

                    try:
                        res = stock_engine.tag_stock_image(
                            item["path"],
                            self.captioner,
                            desc_lang=desc_lang,
                            tags_lang=tags_lang,
                            tags_count=tags_cnt
                        )
                        elapsed = res["time_sec"]
                        item["title"] = res["title"]
                        item["description"] = res["description"]
                        item["tags"] = res["tags"]
                        item["time_sec"] = elapsed

                        stock_engine.apply_stock_metadata(
                            item["path"],
                            title=res["title"],
                            description=res["description"],
                            tags=res["tags"],
                            write_file=write_file,
                            write_xmp=write_xmp,
                            write_iptc=write_iptc
                        )
                        item["status"] = "done"
                        done_cnt += 1
                        self.run_on_ui(lambda i=idx, it=item, el=elapsed, d=done_cnt, tt=tot: self.on_file_processed(i, it, el, d, tt))
                    except Exception as e:
                        item["status"] = "error"
                        item["error"] = str(e)
                        self.run_on_ui(lambda i=idx, it=item: self.update_tree_row(i, self.t("st_error"), "error", it.get("title", ""), 0, 0))

                    time.sleep(0.08)

                def finish_multi():
                    self.finish_batch_processing()
                    self.sync_selection_ui()
                self.run_on_ui(finish_multi)

            threading.Thread(target=multi_task, daemon=True).start()

    def on_tag_selected_finish(self, success: bool, elapsed: float):
        self.btn_tag_selected.configure(state="normal", text=self.t("btn_tag_selected"))
        self.btn_clear_meta.configure(state="normal")
        self.btn_start.configure(state="normal")
        self.btn_re_tag.configure(state="normal", text=self.t("btn_re_tag"))
        if success:
            item = self.files[self.selected_file_idx]
            self.display_photo_inspector(self.selected_file_idx)
            self.update_single_file_ui(self.selected_file_idx)
            self.update_stats_label()
            self.lbl_status_main.configure(text=self.t("tagged_single_ok", name=item["name"], elapsed=elapsed, tags=len(item["tags"])))
        else:
            messagebox.showerror("Error", self.t("tagged_single_err"))

    def on_clear_metadata_clicked(self):
        if not self.files:
            messagebox.showinfo("Info", self.t("status_ready"))
            return

        if self.is_processing:
            messagebox.showwarning("Warning", "Batch processing is currently running.")
            return

        tot_files = len(self.files)
        sel_count = len(self.selected_indices)

        if 1 <= sel_count < tot_files:
            self.show_clear_choice_dialog(sel_count, tot_files)
        else:
            ans = messagebox.askyesno(
                self.t("clear_modal_title"),
                f"{self.t('clear_modal_msg')}\n\n" + self.t("btn_clear_all", count=tot_files) + "?"
            )
            if ans:
                self.execute_clear_metadata(mode="all")

    def show_clear_choice_dialog(self, sel_count: int, tot_files: int):
        dialog = ctk.CTkToplevel(self)
        dialog.title(self.t("clear_modal_title"))
        dialog.geometry("450x230")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        try:
            x = self.winfo_x() + (self.winfo_width() - 450) // 2
            y = self.winfo_y() + (self.winfo_height() - 230) // 2
            dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass

        lbl_icon = ctk.CTkLabel(
            dialog,
            text="🗑 " + self.t("clear_modal_title"),
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#f8fafc"
        )
        lbl_icon.pack(pady=(16, 6))

        lbl_msg = ctk.CTkLabel(
            dialog,
            text=self.t("clear_modal_msg"),
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
            wraplength=400,
            justify="center"
        )
        lbl_msg.pack(pady=(0, 14))

        btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_box.pack(fill="x", padx=24, pady=(0, 14))

        def on_clear_sel():
            dialog.destroy()
            self.execute_clear_metadata(mode="selected")

        def on_clear_all():
            dialog.destroy()
            self.execute_clear_metadata(mode="all")

        def on_cancel():
            dialog.destroy()

        btn_sel = ctk.CTkButton(
            btn_box,
            text=self.t("btn_clear_selected", count=sel_count),
            command=on_clear_sel,
            fg_color="#0284c7",
            hover_color="#0369a1",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32
        )
        btn_sel.pack(fill="x", pady=(0, 6))

        btn_all = ctk.CTkButton(
            btn_box,
            text=self.t("btn_clear_all", count=tot_files),
            command=on_clear_all,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32
        )
        btn_all.pack(fill="x", pady=(0, 6))

        btn_canc = ctk.CTkButton(
            btn_box,
            text=self.t("clear_modal_cancel"),
            command=on_cancel,
            fg_color="#334155",
            hover_color="#475569",
            height=28
        )
        btn_canc.pack(fill="x")

    def execute_clear_metadata(self, mode: str = "selected"):
        if mode == "selected":
            target_indices = sorted(list(self.selected_indices))
        else:
            target_indices = list(range(len(self.files)))

        if not target_indices:
            return

        target_paths = [self.files[i]["path"] for i in target_indices]
        tot = len(target_paths)

        self.btn_clear_meta.configure(state="disabled", text="Clearing...")
        self.btn_tag_selected.configure(state="disabled")
        self.btn_start.configure(state="disabled")
        self.lbl_status_main.configure(text=self.t("status_clearing_meta", cur=0, tot=tot))

        def clear_worker():
            def on_prog(cur, t):
                self.run_on_ui(lambda: self.lbl_status_main.configure(text=self.t("status_clearing_meta", cur=cur, tot=t)))

            ok = stock_engine.clear_stock_metadata(target_paths, chunk_size=50, on_progress=on_prog)

            def finish_clear():
                for idx in target_indices:
                    if idx < len(self.files):
                        it = self.files[idx]
                        it["title"] = ""
                        it["description"] = ""
                        it["tags"] = []
                        it["status"] = "pending"
                        it["time_sec"] = 0.0
                        it["error"] = ""
                        it["_meta_checked"] = True

                        if self.tree.exists(str(idx)):
                            self.tree.item(str(idx), values=(idx + 1, it["rel_path"], self.t("st_pending"), "", "", ""), tags=("pending",))

                        if idx in self.grid_cards:
                            self.update_grid_card_status(idx)

                self.btn_clear_meta.configure(state="normal")
                self.btn_tag_selected.configure(state="normal")
                self.btn_start.configure(state="normal")

                self.sync_selection_ui()
                self.update_stats_label()

                if self.selected_file_idx in target_indices:
                    self.display_photo_inspector(self.selected_file_idx)

                self.lbl_status_main.configure(text=self.t("status_cleared_ok", count=tot))

            self.run_on_ui(finish_clear)

        threading.Thread(target=clear_worker, daemon=True).start()


    def update_single_file_ui(self, idx: int):
        if idx < 0 or idx >= len(self.files):
            return
        item = self.files[idx]
        st_text = self.get_status_display(item["status"])
        t_cnt = len(item.get("tags", []))
        tags_disp = str(t_cnt) if t_cnt > 0 else ""
        t_disp = f"{item['time_sec']:.1f}s" if item["time_sec"] > 0 else ""

        if self.tree.exists(str(idx)):
            self.tree.item(str(idx), values=(idx + 1, item["rel_path"], st_text, item.get("title", ""), tags_disp, t_disp), tags=(item["status"],))

        if idx in self.grid_cards:
            self.update_grid_card_status(idx)


    # ------------------ Fast Batch Tags Scanner ------------------

    def toggle_tags_scan(self):
        if self.is_scanning_tags:
            self.stop_tags_scan = True
            self.btn_scan_tags.configure(state="disabled", text="Stopping...")
            self.lbl_status_main.configure(text="Stopping tags scan...")
            return

        if not self.files:
            messagebox.showinfo("Info", self.t("status_ready"))
            return

        self.is_scanning_tags = True
        self.stop_tags_scan = False

        self.btn_scan_tags.configure(
            text="⏹ " + self.t("btn_stop_scan_tags"),
            fg_color="#ef4444",
            hover_color="#dc2626",
            state="normal"
        )
        self.btn_scan.configure(state="disabled")

        threading.Thread(target=self.tags_scan_worker, daemon=True).start()

    def tags_scan_worker(self):
        try:
            total_files = len(self.files)
            paths_str = [f["path"] for f in self.files]
            chunk_size = 50
            tagged_count = 0

            for i in range(0, total_files, chunk_size):
                if self.stop_tags_scan:
                    break

                chunk_paths = paths_str[i:i + chunk_size]
                chunk_meta = stock_engine.read_files_metadata_batch(chunk_paths, chunk_size=chunk_size)

                def apply_chunk(start_idx=i, meta_dict=chunk_meta):
                    nonlocal tagged_count
                    for offset, p in enumerate(chunk_paths):
                        idx = start_idx + offset
                        if idx >= len(self.files):
                            break
                        item = self.files[idx]
                        m = meta_dict.get(p, {})
                        item["title"] = m.get("title", "")
                        item["description"] = m.get("description", "")
                        item["tags"] = m.get("tags", [])
                        item["_meta_checked"] = True

                        if m.get("is_tagged"):
                            tagged_count += 1
                            if item["status"] == "pending":
                                item["status"] = "done"

                        # Update Treeview row
                        st_text = self.get_status_display(item["status"])
                        t_cnt = len(item["tags"])
                        tags_disp = str(t_cnt) if t_cnt > 0 else ""
                        t_disp = f"{item['time_sec']:.1f}s" if item["time_sec"] > 0 else ""
                        if self.tree.exists(str(idx)):
                            self.tree.item(str(idx), values=(idx + 1, item["rel_path"], st_text, item["title"], tags_disp, t_disp), tags=(item["status"],))

                        # Update Grid card
                        if idx in self.grid_cards:
                            self.update_grid_card_status(idx)

                    cur_done = min(start_idx + len(chunk_paths), total_files)
                    self.lbl_status_main.configure(
                        text=self.t("status_scanning_tags", cur=cur_done, tot=total_files, tagged=tagged_count)
                    )
                    self.update_stats_label()

                    # If currently selected photo was in this chunk, update inspector
                    if self.selected_file_idx >= start_idx and self.selected_file_idx < start_idx + len(chunk_paths):
                        self.display_photo_inspector(self.selected_file_idx)

                self.run_on_ui(apply_chunk)

        except Exception as e:
            print(f"Error in tags_scan_worker: {e}")
        finally:
            def finish_scan_tags():
                self.is_scanning_tags = False
                self.stop_tags_scan = False
                self.btn_scan_tags.configure(
                    text="🏷 " + self.t("btn_scan_tags"),
                    fg_color="#334155",
                    hover_color="#475569",
                    state="normal"
                )
                self.btn_scan.configure(state="normal")
                self.update_stats_label()
                final_tagged = sum(1 for f in self.files if len(f.get("tags", [])) >= 3 or f["status"] == "done")
                self.lbl_status_main.configure(
                    text=self.t("status_scan_tags_finished", tagged=final_tagged, tot=len(self.files))
                )
                if self.selected_file_idx >= 0 and self.selected_file_idx < len(self.files):
                    self.display_photo_inspector(self.selected_file_idx)

            self.run_on_ui(finish_scan_tags)


    # ------------------ Batch Runner ------------------

    def toggle_batch_processing(self):
        if self.is_processing:
            self.toggle_pause()
            return

        if not self.files:
            messagebox.showinfo("Info", self.t("status_ready"))
            return

        pending_items = [f for f in self.files if f["status"] == "pending"]
        if not pending_items:
            ans = messagebox.askyesno(self.t("confirm_reprocess_title"), self.t("confirm_reprocess_msg"))
            if ans:
                for idx, f in enumerate(self.files):
                    f["status"] = "pending"
                    f["time_sec"] = 0.0
                    self.tree.item(str(idx), values=(idx + 1, f["rel_path"], self.t("st_pending"), f.get("title", ""), "", ""), tags=("pending",))
                self.update_stats_label()
            else:
                return

        if not self.model_online:
            messagebox.showwarning("Warning", self.t("warn_no_lm_studio"))
            return

        self.save_config()
        self.is_processing = True
        self.is_paused = False
        self.stop_requested = False

        self.btn_start.configure(text=self.t("btn_processing"), fg_color="#dc2626", hover_color="#b91c1c")
        self.btn_pause.configure(state="normal", text=self.t("btn_pause"), fg_color="#334155")
        self.btn_stop.configure(state="normal")
        self.btn_scan.configure(state="disabled")
        self.btn_tag_selected.configure(state="disabled")
        self.btn_clear_meta.configure(state="disabled")

        self.processing_thread = threading.Thread(target=self.batch_worker_loop, daemon=True)
        self.processing_thread.start()

    def toggle_pause(self):
        if not self.is_processing:
            return
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.btn_pause.configure(text=self.t("btn_resume"), fg_color="#f59e0b", hover_color="#d97706")
            self.lbl_status_main.configure(text=self.t("batch_paused"))
        else:
            self.btn_pause.configure(text=self.t("btn_pause"), fg_color="#334155", hover_color="#475569")
            self.lbl_status_main.configure(text="Resuming...")

    def stop_processing(self):
        if not self.is_processing:
            return
        self.stop_requested = True
        self.lbl_status_main.configure(text=self.t("batch_stopping"))

    def batch_worker_loop(self):
        try:
            desc_lang = NAME_TO_CODE.get(self.combo_desc_lang.get(), "en")
            tags_lang = NAME_TO_CODE.get(self.combo_tags_lang.get(), "en")
            tags_cnt = int(self.combo_tags_count.get())
            write_file = self.chk_write_file.get()
            write_iptc = self.chk_write_iptc.get()
            write_xmp = self.chk_write_xmp.get()

            to_process = [i for i, f in enumerate(self.files) if f["status"] == "pending"]
            total_batch = len(to_process)
            processed_in_batch = 0

            for idx in to_process:
                if self.stop_requested:
                    break

                while self.is_paused:
                    if self.stop_requested:
                        break
                    time.sleep(0.25)

                if self.stop_requested:
                    break

                item = self.files[idx]
                item["status"] = "processing"

                self.run_on_ui(lambda i=idx, it=item: self.update_tree_row(i, self.t("st_processing"), "processing", it.get("title", ""), 0, 0))
                self.run_on_ui(lambda it=item: self.lbl_status_main.configure(text=self.t("analyzing_vlm", name=it["name"])))

                # Check GPU load safely (passed as proper dict)
                try:
                    busy, reason = is_system_busy({"max_gpu_util_percent": 90}, lang=self.current_lang)
                    if busy:
                        self.run_on_ui(lambda r=reason: self.lbl_status_main.configure(text=f"{self.t('cooling_gpu')} ({r})"))
                        time.sleep(3.5)
                except Exception:
                    pass

                try:
                    res = stock_engine.tag_stock_image(item["path"], self.captioner, desc_lang=desc_lang, tags_lang=tags_lang, tags_count=tags_cnt)
                    elapsed = res["time_sec"]

                    item["title"] = res["title"]
                    item["description"] = res["description"]
                    item["tags"] = res["tags"]
                    item["time_sec"] = elapsed

                    # Embed metadata
                    stock_engine.apply_stock_metadata(
                        item["path"],
                        title=res["title"],
                        description=res["description"],
                        tags=res["tags"],
                        write_file=write_file,
                        write_xmp=write_xmp,
                        write_iptc=write_iptc
                    )
                    item["status"] = "done"

                    processed_in_batch += 1
                    self.total_processed_session += 1
                    self.session_times.append(elapsed)
                    if len(self.session_times) > 20:
                        self.session_times.pop(0)

                    self.run_on_ui(lambda i=idx, it=item, el=elapsed: self.on_file_processed(i, it, el, processed_in_batch, total_batch))

                except Exception as e:
                    item["status"] = "error"
                    item["error"] = str(e)
                    err_line = f"Error processing {item['name']}: {traceback.format_exc()}"
                    print(err_line)
                    self.run_on_ui(lambda i=idx, it=item: self.update_tree_row(i, self.t("st_error"), "error", it.get("title", ""), 0, 0))

                time.sleep(0.08)

        except Exception as e:
            print("Fatal error in batch loop:", traceback.format_exc())
        finally:
            self.run_on_ui(self.finish_batch_processing)

    def update_tree_row(self, idx: int, status_text: str, tag: str, title: str, tag_count: int, elapsed: float):
        item = self.files[idx]
        t_disp = f"{elapsed:.1f}s" if elapsed > 0 else ""
        cnt_disp = str(tag_count) if tag_count > 0 else ""
        self.tree.item(str(idx), values=(idx + 1, item["rel_path"], status_text, title, cnt_disp, t_disp), tags=(tag,))
        self.tree.see(str(idx))

        if idx in self.grid_cards:
            self.update_grid_card_status(idx)

    def on_file_processed(self, idx: int, item: dict, elapsed: float, cur_count: int, total_count: int):
        self.update_tree_row(idx, self.t("st_done"), "done", item["title"], len(item["tags"]), elapsed)
        self.update_stats_label()

        if self.selected_file_idx == idx:
            self.display_photo_inspector(idx)

        progress = cur_count / max(1, total_count)
        self.prog_bar.set(progress)

        avg_speed = sum(self.session_times) / max(1, len(self.session_times))
        remaining = total_count - cur_count
        eta_seconds = int(remaining * avg_speed)

        eta_str = f"ETA: ~{eta_seconds // 60}m {eta_seconds % 60}s" if eta_seconds > 0 else "Finishing..."
        self.lbl_status_main.configure(
            text=f"{cur_count}/{total_count} ({progress*100:.1f}%) • {item['name']} ({elapsed:.1f}s)"
        )
        self.lbl_eta.configure(text=f"{avg_speed:.1f} s/photo • {eta_str}")

    def finish_batch_processing(self):
        self.is_processing = False
        self.is_paused = False
        self.stop_requested = False

        self.btn_start.configure(text=self.t("btn_start"), fg_color="#10b981", hover_color="#059669")
        self.btn_pause.configure(state="disabled", text=self.t("btn_pause"), fg_color="#334155")
        self.btn_stop.configure(state="disabled")
        self.btn_scan.configure(state="normal")
        self.btn_tag_selected.configure(state="normal")
        self.btn_clear_meta.configure(state="normal")
        self.sync_selection_ui()

        if self.chk_export_csv.get():
            self.auto_export_csv()

        self.lbl_status_main.configure(text=self.t("batch_completed"))
        self.lbl_eta.configure(text="")
        self.update_stats_label()

    def auto_export_csv(self):
        folder = self.folder_entry.get().strip()
        if not folder or not os.path.isdir(folder):
            return

        done_items = [f for f in self.files if f["status"] == "done"]
        if not done_items:
            return

        csv_path = os.path.join(folder, "stock_metadata.csv")
        try:
            stock_engine.export_stock_csv(done_items, csv_path)
            self.lbl_status_main.configure(text=f"✓ Exported {os.path.basename(csv_path)} ({len(done_items)} photos)")
        except Exception as e:
            print(f"Error auto-exporting CSV: {e}")

    def manual_export_csv(self):
        done_items = [f for f in self.files if f["status"] == "done" or (f.get("tags") and len(f.get("tags")) > 0)]
        if not done_items:
            messagebox.showinfo("Info", self.t("no_photos_csv"))
            return

        cur_dir = self.folder_entry.get().strip() or os.getcwd()
        initial_file = os.path.join(cur_dir, "stock_metadata.csv")

        save_path = filedialog.asksaveasfilename(
            initialfile=initial_file,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export CSV for Microstocks"
        )
        if save_path:
            try:
                stock_engine.export_stock_csv(done_items, save_path)
                messagebox.showinfo("Success", self.t("csv_exported_ok", count=len(done_items), path=save_path))
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export CSV: {e}")

    def open_current_folder(self):
        folder = self.folder_entry.get().strip()
        if folder and os.path.isdir(folder):
            os.startfile(folder)
        else:
            messagebox.showwarning("Warning", self.t("warn_folder_not_found"))


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


def main():
    kill_duplicate_instances(["StockAI_Tagger.exe"])
    app = StockTaggerApp()
    try:
        app.mainloop()
    finally:
        os._exit(0)

if __name__ == "__main__":
    main()
