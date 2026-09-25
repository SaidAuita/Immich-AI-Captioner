import json
import os
import sys
import locale

def get_base_dir() -> str:
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

LOCALES_DIR = os.path.join(get_base_dir(), "locales")

SUPPORTED_LANGUAGES = {
    "en": "English",
    "ru": "Русский",
    "de": "Deutsch",
    "es": "Español",
    "fr": "Français",
    "ja": "日本語",
    "pt": "Português",
    "zh": "简体中文"
}

NAME_TO_CODE = {v: k for k, v in SUPPORTED_LANGUAGES.items()}

class I18n:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.current_lang = "en"
        self.translations = {}
        self._load_translations()
        
        # Auto-detect system language
        try:
            sys_lang, _ = locale.getdefaultlocale()
            if sys_lang:
                sl = sys_lang.lower()
                for code in SUPPORTED_LANGUAGES:
                    if sl.startswith(code):
                        self.current_lang = code
                        break
        except Exception:
            self.current_lang = "en"

    def _load_translations(self):
        for lang in SUPPORTED_LANGUAGES:
            f_path = os.path.join(LOCALES_DIR, f"{lang}.json")
            if os.path.exists(f_path):
                try:
                    with open(f_path, "r", encoding="utf-8") as f:
                        self.translations[lang] = json.load(f)
                except Exception:
                    self.translations[lang] = {}
            else:
                self.translations[lang] = {}

    def set_language(self, lang: str):
        if lang in SUPPORTED_LANGUAGES:
            self.current_lang = lang

    def get_language(self) -> str:
        return self.current_lang

    def get_language_name(self, code: str = None) -> str:
        c = code or self.current_lang
        return SUPPORTED_LANGUAGES.get(c, "English")

    def t(self, key: str, **kwargs) -> str:
        # Try current language
        msg = self.translations.get(self.current_lang, {}).get(key)
        if msg is None:
            # Fallback to English
            msg = self.translations.get("en", {}).get(key, key)
        if kwargs:
            try:
                return msg.format(**kwargs)
            except Exception:
                return msg
        return msg

_i18n = I18n()

def t(key: str, **kwargs) -> str:
    return _i18n.t(key, **kwargs)

def set_language(lang: str):
    _i18n.set_language(lang)

def get_language() -> str:
    return _i18n.get_language()

def get_supported_languages() -> dict:
    return SUPPORTED_LANGUAGES

def get_language_name(code: str = None) -> str:
    return _i18n.get_language_name(code)

def get_code_by_name(name: str) -> str:
    return NAME_TO_CODE.get(name, "en")
