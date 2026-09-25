import urllib.request
import json
import re

SYSTEM_PROMPT = """Ты — экспертный ассистент для каталогизации фотоархива и подготовки ключевых слов для поиска.
Проанализируй фотографию и верни СТРОГО валидный JSON-объект без форматирования Markdown и без рассуждений.

Схема JSON:
{
  "title": "Краткая суть (3-5 слов)",
  "description": "1-2 лаконичных предложения о происходящем в кадре, строго по фактам, без рассуждений и домыслов.",
  "tags": ["тег1", "тег2", "тег3", "тег4", "тег5", "тег6", "тег7"],
  "ocr": "читаемый текст на фото если есть (вывески, номера, надписи), иначе пустая строка"
}

Правила для тегов (ключевых слов):
- Только конкретные существительные и определения: объекты в кадре, место/локация, природа, люди, время года, техника, животные, материалы.
- Без общих мусорных слов вроде 'фотография', 'изображение', 'кадр', 'снимок'.
- Все теги в нижнем регистре на русском языке.
- От 5 до 15 точных поисковых тегов."""

def format_immich_description(caption_data: dict, mode: str = "tags_only") -> str:
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
            parts.append(f"Теги: {', '.join(tags)}")
        if ocr:
            parts.append(f"[Текст: {ocr}]")
        return "\n".join(parts) if parts else desc

    else:  # full
        parts = []
        if desc:
            parts.append(desc)
        elif title:
            parts.append(title)
        if tags:
            parts.append(f"Теги: {', '.join(tags)}")
        if ocr:
            parts.append(f"[Текст: {ocr}]")
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

    def generate_caption(self, image_b64: str) -> dict:
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
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Опиши фото по заданной JSON-схеме и выдели ключевые теги для поиска."
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
