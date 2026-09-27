import urllib.request
import urllib.parse
import json
import re
import os
import sys
import time

LANGUAGE_NAMES = {
    "en": "English",
    "ru": "Russian",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "ja": "Japanese",
    "pt": "Portuguese",
    "zh": "Chinese"
}

def get_prompt_template_path() -> str:
    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "prompt_template.json"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_template.json"))
    candidates.append(os.path.join(os.getcwd(), "prompt_template.json"))
    for p in candidates:
        if os.path.isfile(p):
            return p
    return candidates[0]

def load_prompt_template() -> dict:
    p = get_prompt_template_path()
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def get_system_prompt(desc_lang: str = "ru", tags_lang: str = "en", tags_count: int = 15) -> str:
    """
    Generates a high-precision system prompt instructing the VLM to produce:
    - title and description strictly in `desc_lang`
    - tags/keywords strictly in `tags_lang` in lowercase
    - exact number of keywords according to `tags_count`
    """
    desc_name = LANGUAGE_NAMES.get(desc_lang, "Russian")
    tags_name = LANGUAGE_NAMES.get(tags_lang, "English")

    tpl = load_prompt_template()
    if tpl and "system_prompt_template" in tpl:
        try:
            return tpl["system_prompt_template"].format(
                desc_lang_name=desc_name,
                tags_lang_name=tags_name,
                tags_count=tags_count
            )
        except Exception:
            pass

    return f"""You are an expert AI photo cataloger and archivist.
Analyze the provided photo and return STRICTLY a valid JSON object without markdown formatting, codeblocks, or thoughts.

CRITICAL LANGUAGE REQUIREMENTS:
- The title and description MUST be written strictly in {desc_name}.
- NEVER use Chinese characters (汉字/hanzi), Japanese, or any language other than {desc_name}.
- All tags MUST be strictly in {tags_name} in lowercase.

JSON schema:
{{
  "title": "Short title (3-6 words) strictly in {desc_name} (NO Chinese characters)",
  "description": "1-2 concise factual sentences describing what is happening in the photo, strictly in {desc_name} (NO Chinese characters).",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "..."],
  "ocr": "Any clearly readable text/signs/numbers found on the photo, or empty string"
}}

    Rules for tags (keywords):
- All tags MUST be strictly in {tags_name} in lowercase.
- Only specific nouns and descriptors: objects, scene/location, nature, people, season, vehicles, animals, materials, concepts.
- No meta or useless words like 'photo', 'image', 'picture', 'shot', 'view', 'wallpaper'.
- Provide exactly {tags_count} accurate, diverse search keywords strictly in {tags_name}.
- STOP immediately after outputting {tags_count} tags. Close the tags array with ']' and complete the JSON object."""

# Backwards-compatible default prompt
SYSTEM_PROMPT = get_system_prompt("ru", "ru", 15)

def format_immich_description(caption_data: dict, mode: str = "tags_only", desc_lang: str = "ru") -> str:
    """
    Формирует строку для записи в поле description Immich в зависимости от выбранного режима.
    - 'tags_only': только список ключевых слов через запятую (максимальная точность поиска).
    - 'title_and_tags': Заголовок + список тегов.
    - 'full': полное лаконичное описание + теги.
    """
    tags = caption_data.get("tags", [])
    title = caption_data.get("title", "").strip()
    desc = caption_data.get("description", "").strip()
    ocr = caption_data.get("ocr", "").strip()

    tags_label = "Теги:" if desc_lang == "ru" else "Tags:"
    ocr_prefix = "[Текст: " if desc_lang == "ru" else "[Text: "

    if mode == "tags_only":
        items = list(tags)
        if ocr and ocr.lower() not in [t.lower() for t in items]:
            items.append(ocr)
        return ", ".join(items) if items else (title or desc)

    elif mode == "title_and_tags":
        parts = []
        if title:
            parts.append(title)
        if tags:
            parts.append(f"{tags_label} {', '.join(tags)}")
        if ocr:
            parts.append(f"{ocr_prefix}{ocr}]")
        return "\n".join(parts) if parts else desc

    else:  # full
        parts = []
        if desc:
            parts.append(desc)
        elif title:
            parts.append(title)
        if tags:
            parts.append(f"{tags_label} {', '.join(tags)}")
        if ocr:
            parts.append(f"{ocr_prefix}{ocr}]")
        return "\n".join(parts)

class VlmCaptioner:
    def __init__(self, base_url: str, model: str, temperature: float = 0.1, max_tokens: int = 350):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.active_model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._last_model_check = 0.0
        try:
            self.resolve_active_model()
        except Exception:
            pass

    def resolve_active_model(self, force: bool = False) -> str:
        """
        Detects which model is currently loaded in LM Studio VRAM via /api/v0/models.
        If a model is already loaded in memory, ALWAYS use it to avoid unwanted model reloads/ejections.
        Caches result for 45s unless force=True.
        """
        now = time.time()
        if not force and getattr(self, "active_model", None) and (now - getattr(self, "_last_model_check", 0.0) < 45.0):
            return self.active_model

        # 1. Native LM Studio API check for loaded model in memory
        try:
            parsed = urllib.parse.urlparse(self.base_url)
            root_url = f"{parsed.scheme}://{parsed.netloc}"
            req_v0 = urllib.request.Request(f"{root_url}/api/v0/models", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req_v0, timeout=2.0) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                models = data.get("data", [])
                loaded = [m for m in models if m.get("state") == "loaded"]
                if loaded:
                    self._last_model_check = now
                    # Prefer loaded VLM/vision model
                    vlm_loaded = [m for m in loaded if m.get("type") == "vlm" or any(k in m.get("id", "").lower() for k in ["vl", "vision", "gemma"])]
                    if vlm_loaded:
                        self.active_model = vlm_loaded[0].get("id")
                        return self.active_model
                    self.active_model = loaded[0].get("id")
                    return self.active_model

                # If no model is loaded right now in memory, check available models
                ids = [m.get("id") for m in models if m.get("id")]
                for mid in ids:
                    if self.model.lower() == mid.lower() or self.model.lower() in mid.lower() or mid.lower() in self.model.lower():
                        self.active_model = mid
                        self._last_model_check = now
                        return self.active_model
                for mid in ids:
                    if any(k in mid.lower() for k in ["gemma", "qwen", "vl", "vision", "e4b", "e2b"]):
                        self.active_model = mid
                        self._last_model_check = now
                        return self.active_model
                if ids:
                    self.active_model = ids[0]
                    self._last_model_check = now
                    return self.active_model
        except Exception:
            pass

        # 2. Standard OpenAI-compatible /v1/models fallback
        try:
            req = urllib.request.Request(f"{self.base_url}/models", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                models = [m['id'] for m in data.get('data', [])]
                if models:
                    self._last_model_check = now
                    for m in models:
                        if self.model.lower() == m.lower() or self.model.lower() in m.lower() or m.lower() in self.model.lower():
                            self.active_model = m
                            return self.active_model
                    for m in models:
                        if any(k in m.lower() for k in ["gemma", "qwen", "vl", "vision", "e4b", "e2b"]):
                            self.active_model = m
                            return self.active_model
                    self.active_model = models[0]
                    return self.active_model
        except Exception:
            pass

        self._last_model_check = now
        return self.active_model

    def test_connection(self) -> bool:
        """Verifies LM Studio is responding without redundant model re-scans."""
        try:
            req = urllib.request.Request(f"{self.base_url}/models")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def generate_caption(self, image_b64: str, desc_lang: str = "ru", tags_lang: str = "en", tags_count: int = 15, system_prompt: str = None, user_prompt: str = None) -> dict:
        """
        Sends image to LM Studio and returns structured dict:
        {
            "title": str,
            "description": str,
            "tags": list[str],
            "ocr": str
        }
        """
        url = f"{self.base_url}/chat/completions"
        if not system_prompt:
            system_prompt = get_system_prompt(desc_lang=desc_lang, tags_lang=tags_lang, tags_count=tags_count)
        desc_name = LANGUAGE_NAMES.get(desc_lang, "Russian")
        tags_name = LANGUAGE_NAMES.get(tags_lang, "English")

        if not user_prompt:
            tpl = load_prompt_template()
            user_prompt_text = f"Describe the photo using the specified JSON schema. Output title and description strictly in {desc_name} without any Chinese characters. Output exactly {tags_count} search tags strictly in {tags_name}."
            if tpl and "user_prompt_template" in tpl:
                try:
                    user_prompt_text = tpl["user_prompt_template"].format(
                        desc_lang_name=desc_name,
                        tags_lang_name=tags_name,
                        tags_count=tags_count
                    )
                except Exception:
                    pass
        else:
            user_prompt_text = user_prompt

        # Resolve active model before inference to avoid triggering model unload
        self.resolve_active_model()
        active_m = getattr(self, "active_model", self.model)

        if tags_count >= 40:
            effective_max_tokens = min(max(self.max_tokens, 550), 650)
        elif tags_count >= 25:
            effective_max_tokens = min(max(self.max_tokens, 450), 550)
        else:
            effective_max_tokens = min(max(self.max_tokens, 350), 450)

        # Reasoning / Thinking models (like Gemma-4) consume 200-400 tokens in thought process
        # before outputting JSON, so they require a higher max_tokens budget (at least 750).
        if any(k in str(active_m).lower() for k in ["gemma", "think", "reason", "r1"]):
            effective_max_tokens = max(effective_max_tokens, 750)

        payload = {
            "model": active_m,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_prompt_text
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}"
                            }
                        }
                    ]
                }
            ],
            "temperature": self.temperature,
            "max_tokens": effective_max_tokens
        }

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode('utf-8'))
            msg = res['choices'][0]['message']
            raw_text = (msg.get('content') or '').strip()
            # If content is empty but reasoning_content has text (fallback)
            if not raw_text and msg.get('reasoning_content'):
                raw_text = msg.get('reasoning_content').strip()

        # Clean thinking tags if present (<think>...</think>)
        cleaned = re.sub(r'<think>.*?</think>', '', raw_text, flags=re.DOTALL).strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'```$', '', cleaned, flags=re.MULTILINE).strip()

        # Parse JSON
        title = ""
        desc = ""
        tags = []
        ocr = ""

        try:
            parsed = json.loads(cleaned)
            title = str(parsed.get("title", "")).strip()
            desc = str(parsed.get("description", "")).strip()
            raw_tags = parsed.get("tags", [])
            ocr = str(parsed.get("ocr", "")).strip()

            if isinstance(raw_tags, list):
                for t in raw_tags:
                    t_str = str(t).strip().lower()
                    if t_str and t_str not in tags:
                        tags.append(t_str)
            elif isinstance(raw_tags, str):
                tags = [t.strip().lower() for t in raw_tags.split(",") if t.strip()]
        except Exception:
            # Fallback regex extraction in case of truncated or malformed JSON
            m_title = re.search(r'"title"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', cleaned)
            if m_title:
                title = m_title.group(1).strip()
            m_desc = re.search(r'"description"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', cleaned)
            if m_desc:
                desc = m_desc.group(1).strip()
            m_ocr = re.search(r'"ocr"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', cleaned)
            if m_ocr:
                ocr = m_ocr.group(1).strip()
            tags_idx = cleaned.find('"tags"')
            if tags_idx != -1:
                found = re.findall(r'"([^"\\]+)"', cleaned[tags_idx:])
                for t in found:
                    t_clean = t.strip().lower()
                    if t_clean != "tags" and len(t_clean) > 1 and t_clean not in tags:
                        tags.append(t_clean)

        # 1. Enforce strict tags count limit
        if tags_count and tags_count > 0:
            tags = tags[:tags_count]

        # 2. Filter out Chinese characters from tags if not Chinese
        if tags_lang != "zh":
            tags = [t for t in tags if not re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', t)]

        # 3. Auto-healing translation: detect Chinese (CJK) characters when Chinese was NOT requested
        if desc_lang != "zh" and re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', f"{title} {desc}"):
            try:
                active_tr_m = getattr(self, "active_model", self.model)
                tr_tokens = 500 if any(k in str(active_tr_m).lower() for k in ["gemma", "think", "reason", "r1"]) else 250
                fix_payload = {
                    "model": active_tr_m,
                    "messages": [
                        {"role": "system", "content": f"You are a professional translator. Translate into {desc_name}. Output strictly JSON with keys 'title' and 'description' without any Chinese characters or markdown."},
                        {"role": "user", "content": f"Translate the following photo title and description strictly into {desc_name}:\nTitle: {title}\nDescription: {desc}"}
                    ],
                    "temperature": 0.1,
                    "max_tokens": tr_tokens
                }
                fix_req = urllib.request.Request(
                    f"{self.base_url}/chat/completions",
                    data=json.dumps(fix_payload).encode('utf-8'),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(fix_req, timeout=15) as fix_resp:
                    fix_res = json.loads(fix_resp.read().decode('utf-8'))
                    fix_msg = fix_res['choices'][0]['message']
                    fix_raw = (fix_msg.get('content') or fix_msg.get('reasoning_content') or '').strip()
                    fix_clean = re.sub(r'<think>.*?</think>', '', fix_raw, flags=re.DOTALL).strip()
                    fix_clean = re.sub(r'^```(?:json)?\s*', '', fix_clean, flags=re.MULTILINE)
                    fix_clean = re.sub(r'```$', '', fix_clean, flags=re.MULTILINE).strip()
                    try:
                        fix_parsed = json.loads(fix_clean)
                    except Exception:
                        fix_parsed = {}
                        m_t = re.search(r'"title"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', fix_clean)
                        if m_t:
                            fix_parsed["title"] = m_t.group(1)
                        m_d = re.search(r'"description"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', fix_clean)
                        if m_d:
                            fix_parsed["description"] = m_d.group(1)

                    if fix_parsed.get("title"):
                        title = str(fix_parsed["title"]).strip()
                    if fix_parsed.get("description"):
                        desc = str(fix_parsed["description"]).strip()
            except Exception:
                pass

        if not desc and not title:
            desc = cleaned

        return {
            "title": title,
            "description": desc,
            "tags": tags,
            "ocr": ocr
        }
