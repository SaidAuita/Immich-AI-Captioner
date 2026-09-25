import urllib.request
import json
import re

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

def get_system_prompt(desc_lang: str = "ru", tags_lang: str = "en") -> str:
    """
    Generates a high-precision system prompt instructing the VLM to produce:
    - title and description strictly in `desc_lang`
    - tags/keywords strictly in `tags_lang` in lowercase
    """
    desc_name = LANGUAGE_NAMES.get(desc_lang, "Russian")
    tags_name = LANGUAGE_NAMES.get(tags_lang, "English")

    return f"""You are an expert AI photo cataloger and archivist.
Analyze the provided photo and return STRICTLY a valid JSON object without markdown formatting, codeblocks, or thoughts.

JSON schema:
{{
  "title": "Short title (3-6 words) strictly in {desc_name}",
  "description": "1-2 concise factual sentences describing what is happening in the photo, strictly in {desc_name}.",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7"],
  "ocr": "Any clearly readable text/signs/numbers found on the photo, or empty string"
}}

Rules for tags (keywords):
- All tags MUST be strictly in {tags_name} in lowercase.
- Only specific nouns and descriptors: objects, scene/location, nature, people, season, vehicles, animals, materials.
- No meta or useless words like 'photo', 'image', 'picture', 'shot', 'view', 'wallpaper'.
- Provide 5 to 15 accurate search keywords."""

# Backwards-compatible default prompt
SYSTEM_PROMPT = get_system_prompt("ru", "ru")

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
        self.temperature = temperature
        self.max_tokens = max_tokens

    def test_connection(self) -> bool:
        """Verifies LM Studio is responding and the model is loaded."""
        try:
            req = urllib.request.Request(f"{self.base_url}/models")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                models = [m['id'] for m in data.get('data', [])]
                return any(self.model in m for m in models) or len(models) > 0
        except Exception:
            return False

    def generate_caption(self, image_b64: str, desc_lang: str = "ru", tags_lang: str = "en") -> dict:
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
        system_prompt = get_system_prompt(desc_lang=desc_lang, tags_lang=tags_lang)
        desc_name = LANGUAGE_NAMES.get(desc_lang, "Russian")
        tags_name = LANGUAGE_NAMES.get(tags_lang, "English")

        payload = {
            "model": self.model,
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
                            "text": f"Describe the photo using the specified JSON schema. Output title and description in {desc_name}. Output all search tags strictly in {tags_name}."
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
            "max_tokens": self.max_tokens
        }

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode('utf-8'))
            raw_text = res['choices'][0]['message']['content'].strip()

        # Clean thinking tags if present (<think>...</think>)
        cleaned = re.sub(r'<think>.*?</think>', '', raw_text, flags=re.DOTALL).strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'```$', '', cleaned, flags=re.MULTILINE).strip()

        # Parse JSON
        try:
            parsed = json.loads(cleaned)
            title = str(parsed.get("title", "")).strip()
            desc = str(parsed.get("description", "")).strip()
            raw_tags = parsed.get("tags", [])
            ocr = str(parsed.get("ocr", "")).strip()

            tags = []
            if isinstance(raw_tags, list):
                for t in raw_tags:
                    t_str = str(t).strip().lower()
                    if t_str and t_str not in tags:
                        tags.append(t_str)
            elif isinstance(raw_tags, str):
                tags = [t.strip().lower() for t in raw_tags.split(",") if t.strip()]

            return {
                "title": title,
                "description": desc,
                "tags": tags,
                "ocr": ocr
            }
        except Exception:
            return {
                "title": "",
                "description": cleaned,
                "tags": [],
                "ocr": ""
            }
