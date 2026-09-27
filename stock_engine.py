# -*- coding: utf-8 -*-
"""
stock_engine.py
Ядро подготовки и тегирования фотографий для микростоков (Adobe Stock, Shutterstock, Freepik и др.)
- Сканирование локальных папок (рекурсивно или только верхний уровень)
- Быстрая проверка существующих метаданных через ExifTool
- Оптимизация изображений для передачи в VLM
- Очистка и нормализация ключевых слов по стоковым стандартам
- Запись метаданных в файлы (IPTC / XMP / EXIF) и sidecar .xmp
- Экспорт стандартного CSV для массовой загрузки на стоки
"""

import os
import sys
import io
import csv
import json
import base64
import subprocess
import time
from pathlib import Path
from PIL import Image, ImageOps

from captioner import VlmCaptioner, LANGUAGE_NAMES
from apply_metadata import apply_metadata_direct, apply_metadata_sidecar

SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff'}

# Автоматический поиск exiftool.exe рядом с .exe или в распакованном бандле
exe_dir = os.path.dirname(os.path.abspath(sys.executable))
app_dir = os.path.dirname(os.path.abspath(__file__))
for p_dir in [exe_dir, app_dir, getattr(sys, '_MEIPASS', None)]:
    if p_dir and os.path.isdir(p_dir):
        if p_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = p_dir + os.path.pathsep + os.environ.get("PATH", "")

# Список запрещенных / мусорных слов для микростоков (стоки отклоняют фото за спам-слова)
STOCK_BANNED_WORDS = {
    'photo', 'photography', 'image', 'picture', 'shot', 'view', 'pic',
    'wallpaper', 'backgrounds', 'nobody', 'close-up', 'closeup', 'macro',
    'hd', '4k', '8k', 'high resolution', 'stock', 'photographer',
    'camera', 'dslr', 'nikon', 'canon', 'sony', 'iphone', 'editorial'
}

def read_files_metadata_batch(file_paths: list[str], chunk_size: int = 50, on_progress=None) -> dict[str, dict]:
    """
    Быстро считывает метаданные для пачки файлов через ExifTool в пакетном режиме.
    Возвращает словарь {abs_path: {"title": str, "description": str, "tags": list[str], "is_tagged": bool}}.
    """
    results = {}
    if not file_paths:
        return results

    cmd_base = [
        "exiftool",
        "-fast2",
        "-charset", "utf8",
        "-j",
        "-Title",
        "-Headline",
        "-XPTitle",
        "-Description",
        "-ImageDescription",
        "-Caption-Abstract",
        "-Keywords",
        "-Subject",
        "-XPKeywords"
    ]

    total = len(file_paths)
    processed = 0

    for i in range(0, total, chunk_size):
        chunk = file_paths[i:i + chunk_size]
        try:
            res = subprocess.run(
                cmd_base + chunk,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                for item in data:
                    src = os.path.abspath(item.get("SourceFile", ""))
                    title = str(item.get("Title") or item.get("Headline") or item.get("XPTitle") or "").strip()
                    desc = str(item.get("Description") or item.get("ImageDescription") or item.get("Caption-Abstract") or "").strip()
                    raw_tags = item.get("Keywords") or item.get("Subject") or item.get("XPKeywords") or []
                    tags_list = []
                    if isinstance(raw_tags, list):
                        for t in raw_tags:
                            t_str = str(t).strip()
                            if t_str and t_str not in tags_list:
                                tags_list.append(t_str)
                    elif isinstance(raw_tags, str):
                        for part in raw_tags.replace(";", ",").split(","):
                            p_str = part.strip()
                            if p_str and p_str not in tags_list:
                                tags_list.append(p_str)

                    is_tagged = len(tags_list) >= 3 or bool(title and desc)
                    results[src] = {
                        "title": title,
                        "description": desc,
                        "tags": tags_list,
                        "is_tagged": is_tagged
                    }
        except Exception:
            pass

        processed += len(chunk)
        if on_progress:
            on_progress(min(processed, total), total)

    return results

def scan_stock_folder(folder_path: str, recursive: bool = True, skip_tagged: bool = False, on_progress=None) -> list[dict]:
    """
    Сканирует локальную директорию на наличие поддерживаемых изображений.
    Всегда считывает существующие метаданные (Title, Description, Tags) для каждого файла.
    Если включен skip_tagged, помечает уже размеченные файлы статусом 'skipped'.
    """
    root_path = Path(folder_path)
    if not root_path.is_dir():
        return []

    found_files = []
    if recursive:
        for p in root_path.rglob("*"):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                found_files.append(p)
    else:
        for p in root_path.glob("*"):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                found_files.append(p)

    found_files.sort(key=lambda x: str(x).lower())

    if not found_files:
        return []

    # Пакетное считывание метаданных запускается ТОЛЬКО если включен skip_tagged (иначе сканирование мгновенное)
    batch_meta = {}
    if skip_tagged:
        paths_str = [str(p.resolve()) for p in found_files]
        batch_meta = read_files_metadata_batch(paths_str, chunk_size=50, on_progress=on_progress)

    results = []
    for p in found_files:
        abs_p = str(p.resolve())
        try:
            rel = str(p.relative_to(root_path))
        except Exception:
            rel = p.name

        meta = batch_meta.get(abs_p, {})
        is_tagged = meta.get("is_tagged", False)
        status = "skipped" if (skip_tagged and is_tagged) else "pending"

        entry = {
            "path": abs_p,
            "name": p.name,
            "rel_path": rel,
            "size_bytes": p.stat().st_size if p.exists() else 0,
            "status": status,
            "title": meta.get("title", ""),
            "description": meta.get("description", ""),
            "tags": meta.get("tags", []),
            "time_sec": 0.0,
            "error": ""
        }
        results.append(entry)

    return results

def read_image_metadata(file_path: str) -> dict:
    """
    Быстро считывает текущие метаданные файла через ExifTool (-fast2).
    Проверяет файл изображения и при необходимости sidecar .xmp.
    Возвращает словарь с полями title, description, tags, is_tagged.
    """
    result = {
        "title": "",
        "description": "",
        "tags": [],
        "is_tagged": False
    }
    p = Path(file_path)
    cmd = [
        "exiftool",
        "-fast2",
        "-charset", "utf8",
        "-j",
        "-Title",
        "-Headline",
        "-XPTitle",
        "-Description",
        "-ImageDescription",
        "-Caption-Abstract",
        "-Keywords",
        "-Subject",
        "-XPKeywords",
        str(p)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            if data and isinstance(data, list):
                item = data[0]
                result["title"] = str(item.get("Title") or item.get("Headline") or item.get("XPTitle") or "").strip()
                result["description"] = str(item.get("Description") or item.get("ImageDescription") or item.get("Caption-Abstract") or "").strip()
                raw_tags = item.get("Keywords") or item.get("Subject") or item.get("XPKeywords") or []
                tags_list = []
                if isinstance(raw_tags, list):
                    for t in raw_tags:
                        t_str = str(t).strip()
                        if t_str and t_str not in tags_list:
                            tags_list.append(t_str)
                elif isinstance(raw_tags, str):
                    for part in raw_tags.replace(";", ",").split(","):
                        p_str = part.strip()
                        if p_str and p_str not in tags_list:
                            tags_list.append(p_str)

                result["tags"] = tags_list
                if len(tags_list) >= 3 or (result["title"] and result["description"]):
                    result["is_tagged"] = True
    except Exception:
        pass

    # Если в файле теги не найдены, проверяем возможный sidecar .xmp
    if not result["tags"] and not result["title"]:
        xmp_candidates = [p.with_suffix(".xmp"), Path(str(p) + ".xmp")]
        for xmp in xmp_candidates:
            if xmp.is_file():
                try:
                    res_xmp = subprocess.run(cmd[:-1] + [str(xmp)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
                    if res_xmp.returncode == 0 and res_xmp.stdout.strip():
                        xmp_data = json.loads(res_xmp.stdout)
                        if xmp_data and isinstance(xmp_data, list):
                            item = xmp_data[0]
                            t_title = str(item.get("Title") or item.get("Headline") or item.get("XPTitle") or "").strip()
                            t_desc = str(item.get("Description") or item.get("ImageDescription") or item.get("Caption-Abstract") or "").strip()
                            raw_tags = item.get("Keywords") or item.get("Subject") or item.get("XPKeywords") or []
                            t_tags = []
                            if isinstance(raw_tags, list):
                                for t in raw_tags:
                                    t_str = str(t).strip()
                                    if t_str and t_str not in t_tags:
                                        t_tags.append(t_str)
                            elif isinstance(raw_tags, str):
                                for part in raw_tags.replace(";", ",").split(","):
                                    p_str = part.strip()
                                    if p_str and p_str not in t_tags:
                                        t_tags.append(p_str)
                            if t_tags or t_title or t_desc:
                                result["title"] = t_title
                                result["description"] = t_desc
                                result["tags"] = t_tags
                                result["is_tagged"] = len(t_tags) >= 3 or bool(t_title and t_desc)
                                break
                except Exception:
                    pass

    return result

def prepare_image_b64(file_path: str, max_dimension: int = 1280) -> tuple[str, Image.Image]:
    """
    Загружает изображение, нормализует ориентацию EXIF,
    создает оптимизированное превью в памяти и возвращает (base64_str, pil_thumbnail).
    1280px - оптимальное разрешение для VLM моделей (Qwen-VL, Gemma), обеспечивающее
    моментальную передачу по HTTP и высокую скорость инференса без потери деталей.
    """
    with Image.open(file_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Создаем четкую миниатюру для широкого инспектора UI
        thumb = img.copy()
        thumb.thumbnail((420, 280), Image.Resampling.LANCZOS)

        # Масштабируем для VLM
        w, h = img.size
        if max(w, h) > max_dimension:
            scale = max_dimension / max(w, h)
            new_w = int(w * scale)
            new_h = int(h * scale)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        raw_bytes = buf.getvalue()
        b64_str = base64.b64encode(raw_bytes).decode("utf-8")
        return b64_str, thumb

def clean_stock_tags(tags: list[str], max_count: int = 40) -> list[str]:
    """
    Фильтрует и нормализует ключевые слова по стандартам микростоков:
    - Нижний регистр
    - Удаление знаков препинания и кавычек
    - Исключение спам-слов (photo, picture, image и т.д.)
    - Дедупликация с сохранением релевантного порядка
    - Ограничение до max_count
    """
    cleaned = []
    seen = set()

    for raw in tags:
        t = raw.strip().lower()
        # Удаляем окружающие кавычки и точки
        t = t.strip('\'"`.,;:!?[]{}()')
        if not t or len(t) < 2:
            continue
        if t in seen:
            continue
        if t in STOCK_BANNED_WORDS:
            continue
        # Пропускаем явный мусор
        if any(char in t for char in "<>/\\|*+~="):
            continue

        seen.add(t)
        cleaned.append(t)
        if len(cleaned) >= max_count:
            break

    return cleaned

def clean_stock_title(title: str, max_words: int = 10) -> str:
    """Очищает заголовок для стока: делает заглавной первую букву, убирает точку в конце."""
    t = title.strip().strip('\'"`.,;:!?')
    # Убираем типичные паразитные фразы
    for prefix in ["an image of ", "a photo of ", "a picture of ", "close up of ", "isolated view of "]:
        if t.lower().startswith(prefix):
            t = t[len(prefix):].strip()
            break
    if t:
        t = t[0].upper() + t[1:]
    words = t.split()
    if len(words) > max_words:
        t = " ".join(words[:max_words])
    return t

def clean_stock_description(desc: str) -> str:
    """Очищает описание: делает заглавной первую букву, гарантирует точку в конце."""
    d = desc.strip().strip('\'"`')
    if d:
        d = d[0].upper() + d[1:]
        if not d.endswith('.'):
            d += '.'
    return d

def get_stock_system_prompt(desc_lang: str = "en", tags_lang: str = "en", tags_count: int = 40) -> str:
    """
    Генерирует специализированный системный промпт для микростоков (Adobe Stock, Shutterstock, Freepik).
    """
    desc_name = LANGUAGE_NAMES.get(desc_lang, "English")
    tags_name = LANGUAGE_NAMES.get(tags_lang, "English")

    no_asian_rule = ""
    if desc_lang not in ("zh", "ja") and tags_lang not in ("zh", "ja"):
        no_asian_rule = f"- NEVER use Chinese characters (汉字/hanzi), Japanese, or any language other than {desc_name} and {tags_name}."

    return f"""You are a professional microstock metadata expert preparing high-converting commercial assets for Adobe Stock, Shutterstock, and Getty Images.
Analyze the provided photo and return STRICTLY a valid JSON object without markdown formatting, codeblocks, or thoughts.

CRITICAL REQUIREMENTS:
- The title and description MUST be written strictly in {desc_name}.
{no_asian_rule}
- All keywords MUST be strictly in {tags_name} in lowercase.
- Focus on commercial stock photography value: identify the main subject, action, context, environment, lighting, colors, concepts, and mood.

JSON schema:
{{
  "title": "Commercial title (5-10 words) strictly in {desc_name}, descriptive and captivating",
  "description": "Factual description (15-25 words) in {desc_name} detailing subject, setting, lighting, and context.",
  "tags": ["tag1", "tag2", "tag3", "tag4", "..."],
  "ocr": "Any clearly readable text/signs/numbers found on the photo, or empty string"
}}

Rules for tags (keywords):
- Exactly {tags_count} highly relevant commercial stock keywords strictly in {tags_name} in lowercase.
- Sorted from most essential main subject to setting, mood, and abstract concepts.
- NO spam or generic words: avoid 'photo', 'image', 'picture', 'shot', 'view', 'wallpaper', 'background' (unless abstract), 'nobody', 'high resolution'.
- STOP immediately after outputting {tags_count} tags. Close the tags array with ']' and complete the JSON object."""

def tag_stock_image(
    file_path: str,
    captioner: VlmCaptioner,
    desc_lang: str = "en",
    tags_lang: str = "en",
    tags_count: int = 40
) -> dict:
    """
    Обрабатывает изображение через VLM со специализированным стоковым промптом.
    Возвращает dict с полями:
    - title: очищенный коммерческий заголовок
    - description: фактическое описание
    - tags: список из ровно tags_count очищенных ключевых слов
    - thumbnail: PIL.Image миниатюра
    - time_sec: время выполнения
    """
    t0 = time.time()
    b64_str, thumb = prepare_image_b64(file_path)

    sys_prompt = get_stock_system_prompt(desc_lang=desc_lang, tags_lang=tags_lang, tags_count=tags_count)
    desc_name = LANGUAGE_NAMES.get(desc_lang, "English")
    tags_name = LANGUAGE_NAMES.get(tags_lang, "English")
    user_prompt = f"Analyze this stock image. Generate a commercial title and factual description in {desc_name}. Generate exactly {tags_count} search keywords in {tags_name}."

    raw = captioner.generate_caption(
        b64_str,
        desc_lang=desc_lang,
        tags_lang=tags_lang,
        tags_count=tags_count,
        system_prompt=sys_prompt,
        user_prompt=user_prompt
    )
    elapsed = time.time() - t0

    raw_title = raw.get("title", "")
    raw_desc = raw.get("description", "")
    raw_tags = raw.get("tags", [])

    clean_title = clean_stock_title(raw_title)
    clean_desc = clean_stock_description(raw_desc)
    clean_kw = clean_stock_tags(raw_tags, max_count=tags_count)

    return {
        "title": clean_title,
        "description": clean_desc,
        "tags": clean_kw,
        "thumbnail": thumb,
        "time_sec": elapsed
    }

def apply_stock_metadata(
    file_path: str,
    title: str,
    description: str,
    tags: list[str],
    write_file: bool = True,
    write_xmp: bool = False,
    write_iptc: bool = True
) -> bool:
    """
    Вшивает подготовленные метаданные в файл и/или sidecar .xmp.
    """
    p = Path(file_path)
    if not p.exists():
        return False

    success = True
    if write_file:
        ok = apply_metadata_direct(p, title=title, description=description, tags=tags, write_iptc=write_iptc)
        if not ok:
            success = False

    if write_xmp:
        ok_xmp = apply_metadata_sidecar(p, title=title, description=description, tags=tags)
        if not ok_xmp:
            success = False

    return success

def export_stock_csv(items: list[dict], output_csv_path: str) -> str:
    """
    Экспортирует список размеченных файлов в универсальный CSV-файл для микростоков
    (совместим с Adobe Stock, Shutterstock, Freepik, Depositphotos).
    Использует UTF-8 BOM (utf-8-sig) для корректного открытия в Excel и веб-панелях.
    """
    fieldnames = ["Filename", "Title", "Description", "Keywords", "Categories"]
    out_path = Path(output_csv_path)

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()

        for item in items:
            fname = item.get("name") or Path(item.get("path", "")).name
            title = item.get("title", "").strip()
            desc = item.get("description", "").strip()
            tags = item.get("tags", [])
            kw_str = ", ".join(tags) if isinstance(tags, list) else str(tags)

            writer.writerow({
                "Filename": fname,
                "Title": title,
                "Description": desc,
                "Keywords": kw_str,
                "Categories": ""
            })

    return str(out_path.resolve())

def clear_stock_metadata(file_paths: list[str], chunk_size: int = 50, on_progress=None) -> bool:
    """
    Полностью удаляет метаданные микростоков (Title, Description, Keywords) из указанных файлов
    (IPTC, XMP, EXIF) через ExifTool и удаляет/очищает сопутствующие sidecar .xmp файлы.
    Возвращает True в случае успешной обработки всех порций.
    """
    if not file_paths:
        return True

    # Список тегов для полного удаления метаданных
    clear_args = [
        "exiftool",
        "-overwrite_original",
        "-preserve",
        "-charset", "iptc=utf8",
        "-codedcharacterset=utf8",
        "-Title=",
        "-Headline=",
        "-ObjectName=",
        "-XPTitle=",
        "-Description=",
        "-ImageDescription=",
        "-Caption-Abstract=",
        "-Keywords=",
        "-Subject=",
        "-XPKeywords=",
        "-XMP-dc:Title=",
        "-XMP-dc:Description=",
        "-XMP-dc:Subject=",
        "-XMP-photoshop:Headline=",
        "-XMP-lr:hierarchicalSubject=",
        "-IPTC:Headline=",
        "-IPTC:ObjectName=",
        "-IPTC:Caption-Abstract=",
        "-IPTC:Keywords="
    ]

    total = len(file_paths)
    processed = 0
    all_ok = True

    # 1. Удаляем связанные sidecar .xmp файлы
    for p_str in file_paths:
        p = Path(p_str)
        for xmp_cand in [p.with_suffix(".xmp"), Path(str(p) + ".xmp")]:
            if xmp_cand.is_file():
                try:
                    xmp_cand.unlink()
                except Exception:
                    pass

    # 2. Пакетная очистка метаданных самих файлов через ExifTool
    for i in range(0, total, chunk_size):
        chunk = [f for f in file_paths[i:i + chunk_size] if os.path.isfile(f)]
        if not chunk:
            continue
        try:
            res = subprocess.run(
                clear_args + chunk,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            if res.returncode != 0:
                all_ok = False
        except Exception as e:
            print(f"Error clearing metadata in chunk: {e}")
            all_ok = False

        processed += len(chunk)
        if on_progress:
            try:
                on_progress(min(processed, total), total)
            except Exception:
                pass

    return all_ok

