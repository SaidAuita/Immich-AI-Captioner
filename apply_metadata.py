#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_metadata.py
Фоновый сервис на сервере cladovka.
- Вшивает стандартизированные IPTC/XMP метаданные в файлы Immich через exiftool.
- Автоматически перезагружается при обновлении файла скрипта через DropSync.
- Ведет файл статистики stats.json для отображения в клиенте на рабочем ПК.
- Поддерживает команды удаленного управления (commands/restart.cmd).
"""

import os
import sys
import time
import json
import shutil
import signal
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
QUEUE_DIR = BASE_DIR / "queue"
DONE_DIR = BASE_DIR / "done"
ERRORS_DIR = BASE_DIR / "errors"
COMMANDS_DIR = BASE_DIR / "commands"
STATS_FILE = BASE_DIR / "stats.json"

IMMICH_HOST_ROOT = Path("/mnt/photos/immich/upload")

DIRECT_EMBED_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff'}
SIDECAR_EXTS = {
    '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.pef',
    '.mp4', '.mov', '.avi', '.mkv', '.m4v', '.3gp'
}

RUNNING = True
SCRIPT_FILE = Path(__file__).resolve()
SCRIPT_START_MTIME = SCRIPT_FILE.stat().st_mtime if SCRIPT_FILE.exists() else 0

STATS = {
    "status": "starting",
    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "total_embedded": 0,
    "total_errors": 0,
    "in_queue": 0,
    "last_file": "",
    "last_title": ""
}

def signal_handler(sig, frame):
    global RUNNING
    print("\n[!] Получен сигнал остановки. Завершаем работу...", flush=True)
    RUNNING = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def log(msg: str):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}"
    print(line, flush=True)
    try:
        with open(BASE_DIR / "worker.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def save_stats(queue_count: int = 0):
    STATS["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    STATS["in_queue"] = queue_count
    try:
        temp_file = BASE_DIR / "stats.json.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(STATS, f, ensure_ascii=False, indent=2)
        temp_file.replace(STATS_FILE)
    except Exception:
        pass

def check_self_update():
    """Если DropSync синхронизировал новую версию apply_metadata.py, перезапускаемся без завершения процесса."""
    try:
        if SCRIPT_FILE.exists():
            current_mtime = SCRIPT_FILE.stat().st_mtime
            if current_mtime > SCRIPT_START_MTIME:
                log("[🔄] Обнаружена новая версия скрипта на диске! Перезапуск...")
                save_stats()
                os.execv(sys.executable, [sys.executable, str(SCRIPT_FILE)])
    except Exception as e:
        log(f"[-] Ошибка автообновления: {e}")

def check_commands():
    """Проверка команд от клиента (например commands/restart.cmd)."""
    restart_cmd = COMMANDS_DIR / "restart.cmd"
    if restart_cmd.exists():
        try:
            restart_cmd.unlink()
        except Exception:
            pass
        log("[🔄] Получена команда перезапуска от клиента! Перезапуск...")
        save_stats()
        os.execv(sys.executable, [sys.executable, str(SCRIPT_FILE)])

def resolve_file_path(container_path: str) -> Path | None:
    if not container_path:
        return None
    norm = container_path.replace('\\', '/')
    if norm.startswith('/data/'):
        rel = norm[len('/data/'):]
    elif norm.startswith('/data'):
        rel = norm[len('/data'):].lstrip('/')
    else:
        rel = norm.lstrip('/')

    candidates = [
        Path("/mnt/photos/immich") / rel,
        Path("/mnt/photos/immich/upload") / rel,
        Path("/mnt/photos") / rel,
        Path(container_path)
    ]
    for c in candidates:
        if c.exists():
            return c

    # Search by filename if not directly at candidate
    fname = Path(container_path).name
    for r in [Path("/mnt/photos/immich"), Path("/mnt/photos")]:
        if r.exists():
            try:
                matches = list(r.glob(f"**/{fname}"))
                if matches:
                    return matches[0]
            except Exception:
                pass

    return Path("/mnt/photos/immich") / rel



import tempfile

def apply_metadata_direct(file_path: Path, title: str, description: str, tags: list[str]) -> bool:
    lines = [
        "-overwrite_original",
        "-preserve",
        "-charset", "iptc=utf8",
        "-codedcharacterset=utf8",
    ]

    if title:
        lines.extend([
            f"-XMP-dc:Title={title}",
            f"-XMP-photoshop:Headline={title}",
            f"-IPTC:Headline={title}",
            f"-IPTC:ObjectName={title}",
            f"-EXIF:XPTitle={title}",
        ])

    if description:
        lines.extend([
            f"-XMP-dc:Description={description}",
            f"-IPTC:Caption-Abstract={description}",
            f"-EXIF:ImageDescription={description}",
        ])

    if tags:
        lines.append("-XMP-dc:Subject=")
        lines.append("-IPTC:Keywords=")
        lines.append("-XMP-lr:hierarchicalSubject=")
        for tag in tags:
            tag_clean = tag.strip()
            if tag_clean:
                lines.append(f"-XMP-dc:Subject={tag_clean}")
                lines.append(f"-IPTC:Keywords={tag_clean}")
                lines.append(f"-XMP-lr:hierarchicalSubject={tag_clean}")

        xp_keywords = "; ".join(tags)
        lines.append(f"-EXIF:XPKeywords={xp_keywords}")

    lines.append(str(file_path))

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".args") as af:
        af.write("\n".join(lines) + "\n")
        args_path = Path(af.name)

    try:
        cmd = ["exiftool", "-charset", "utf8", "-@", str(args_path)]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            err_msg = res.stderr.strip() or res.stdout.strip()
            log(f"[-] Ошибка exiftool для {file_path.name}: {err_msg}")
            return False
        return True
    finally:
        try:
            args_path.unlink()
        except Exception:
            pass

def apply_metadata_sidecar(file_path: Path, title: str, description: str, tags: list[str]) -> bool:
    sidecar_path = file_path.with_suffix(".xmp")
    
    lines = [
        "-overwrite_original",
        "-preserve",
    ]

    if title:
        lines.append(f"-XMP-dc:Title={title}")
    if description:
        lines.append(f"-XMP-dc:Description={description}")
    if tags:
        lines.append("-XMP-dc:Subject=")
        lines.append("-XMP-lr:hierarchicalSubject=")
        for tag in tags:
            tag_clean = tag.strip()
            if tag_clean:
                lines.append(f"-XMP-dc:Subject={tag_clean}")
                lines.append(f"-XMP-lr:hierarchicalSubject={tag_clean}")

    if not sidecar_path.exists():
        lines.extend(["-o", str(sidecar_path), str(file_path)])
    else:
        lines.append(str(sidecar_path))

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".args") as af:
        af.write("\n".join(lines) + "\n")
        args_path = Path(af.name)

    try:
        cmd = ["exiftool", "-charset", "utf8", "-@", str(args_path)]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            err_msg = res.stderr.strip() or res.stdout.strip()
            log(f"[-] Ошибка записи .xmp для {file_path.name}: {err_msg}")
            return False
        return True
    finally:
        try:
            args_path.unlink()
        except Exception:
            pass

def process_task_file(json_file: Path) -> bool:
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"[-] Не удалось прочитать {json_file.name}: {e}")
        return False

    asset_id = data.get("asset_id", json_file.stem)
    container_path = data.get("container_path", "")
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    tags = data.get("tags", [])

    real_path = resolve_file_path(container_path)
    if not real_path:
        log(f"[-] Пропуск {asset_id}: не указан container_path.")
        return False

    if not real_path.exists():
        log(f"[-] Файл не найден на диске: {real_path}")
        STATS["total_errors"] += 1
        try:
            shutil.move(str(json_file), str(ERRORS_DIR / json_file.name))
            with open(ERRORS_DIR / f"{json_file.stem}.error.txt", "w", encoding="utf-8") as ef:
                ef.write(f"Файл не найден на диске: {real_path}\ncontainer_path: {container_path}\n")
        except Exception:
            pass
        return False

    ext = real_path.suffix.lower()

    if ext in DIRECT_EMBED_EXTS:
        success = apply_metadata_direct(real_path, title, description, tags)
    elif ext in SIDECAR_EXTS or real_path.with_suffix(".xmp").exists():
        success = apply_metadata_sidecar(real_path, title, description, tags)
    else:
        success = apply_metadata_direct(real_path, title, description, tags)

    if success:
        log(f"[+] Метаданные записаны: {real_path.name} | \"{title}\" | Тегов: {len(tags)}")
        STATS["total_embedded"] += 1
        STATS["last_file"] = real_path.name
        STATS["last_title"] = title
        dest = DONE_DIR / json_file.name
        try:
            shutil.move(str(json_file), str(dest))
        except Exception:
            pass
        return True
    else:
        STATS["total_errors"] += 1
        try:
            shutil.move(str(json_file), str(ERRORS_DIR / json_file.name))
        except Exception:
            pass
        return False


def main():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (QUEUE_DIR / ".keep").touch(exist_ok=True)
    except Exception:
        pass
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    ERRORS_DIR.mkdir(parents=True, exist_ok=True)
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)

    STATS["status"] = "running"
    save_stats()

    log("==================================================")
    log(" Immich Metadata ExifTool Worker запущен ")
    log(f" Каталог очереди: {QUEUE_DIR}")
    log(f" Хранилище Immich: {IMMICH_HOST_ROOT}")
    log(" Режим: Автономный сервис с автообновлением и статистикой")
    log("==================================================")

    while RUNNING:
        try:
            check_commands()
            check_self_update()

            task_files = sorted(QUEUE_DIR.glob("*.json"))
            save_stats(len(task_files))

            if task_files:
                log(f"[*] Найдено заданий в очереди: {len(task_files)}")
                for tf in task_files:
                    if not RUNNING:
                        break
                    process_task_file(tf)
                    save_stats(len(list(QUEUE_DIR.glob("*.json"))))

            time.sleep(3)
        except Exception as e:
            log(f"[-] Непредвиденная ошибка: {e}")
            time.sleep(5)

    STATS["status"] = "stopped"
    save_stats()
    log("Завершение работы.")

if __name__ == "__main__":
    main()
