# -*- coding: utf-8 -*-
"""
build_stock_tagger.py
Automated release builder for StockAI Tagger v1.1.
1. Compiles stock_tagger_app.py into standalone StockAI_Tagger.exe using PyInstaller.
2. Creates a portable package with embedded ExifTool (if available) and quick-start instructions.
3. Creates a ready-to-upload ZIP for GitHub Releases: StockAI_Tagger_v1.1_Windows_x64.zip.
"""

import os
import sys
import shutil
import zipfile
import subprocess
from pathlib import Path

def main():
    base_dir = Path(__file__).resolve().parent
    dist_dir = base_dir / "dist_stock"
    build_dir = base_dir / "build_stock"
    portable_dir = dist_dir / "StockAI_Tagger_v1.1_Portable"

    dist_dir.mkdir(exist_ok=True)
    build_dir.mkdir(exist_ok=True)

    print("=========================================================")
    print("  StockAI Tagger v1.1 — Building Standalone Windows Executable")
    print("=========================================================")

    skip_compile = "--skip-compile" in sys.argv
    exe_file = dist_dir / "StockAI_Tagger.exe"

    if not skip_compile or not exe_file.is_file():
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--windowed",
            f"--name=StockAI_Tagger",
            f"--icon={base_dir / 'app_icon.ico'}",
            f"--distpath={dist_dir}",
            f"--workpath={build_dir}",
            "--collect-all", "customtkinter",
            "--collect-all", "darkdetect",
            "--hidden-import", "PIL._tkinter_finder",
            "--hidden-import", "tkinter",
            "--hidden-import", "_tkinter",
            "--hidden-import", "stock_engine",
            "--hidden-import", "stock_translations",
            "--hidden-import", "captioner",
            "--hidden-import", "gpu_monitor",
            "--hidden-import", "apply_metadata",
            f"--add-data={base_dir / 'app_icon.ico'};.",
            str(base_dir / "stock_tagger_app.py")
        ]

        print("Running PyInstaller...")
        res = subprocess.run(cmd, cwd=str(base_dir))
        if res.returncode != 0:
            print(f"\n[ERROR] PyInstaller failed with exit code {res.returncode}")
            sys.exit(res.returncode)
    else:
        print("Skipping PyInstaller compile (--skip-compile requested). Using existing EXE.")

    exe_file = dist_dir / "StockAI_Tagger.exe"
    if not exe_file.is_file():
        print(f"\n[ERROR] Expected output executable not found: {exe_file}")
        sys.exit(1)

    exe_size_mb = exe_file.stat().st_size / (1024 * 1024)
    print(f"\n[SUCCESS] Compiled {exe_file.name} ({exe_size_mb:.2f} MB)")

    # Prepare Portable Release Package
    if portable_dir.exists():
        shutil.rmtree(portable_dir)
    portable_dir.mkdir(parents=True)

    # 1. Copy executable
    shutil.copy2(exe_file, portable_dir / "StockAI_Tagger.exe")

    # 2. Copy exiftool and exiftool_files runtime if present in system or C:\ExifTool
    exif_candidates = [
        Path(r"C:\ExifTool\exiftool.exe"),
        base_dir / "exiftool.exe"
    ]
    for c in exif_candidates:
        if c.is_file():
            shutil.copy2(c, portable_dir / "exiftool.exe")
            print(f"Bundled portable ExifTool: {c}")
            files_dir = c.parent / "exiftool_files"
            if files_dir.is_dir():
                target_files_dir = portable_dir / "exiftool_files"
                if target_files_dir.exists():
                    shutil.rmtree(target_files_dir)
                shutil.copytree(files_dir, target_files_dir)
                print(f"Bundled portable ExifTool runtime: {files_dir}")
            break

    # 3. Create Quick Start Guide
    readme_content = """StockAI Tagger v1.1 — Microstock AI Assistant
=================================================
Batch photo preparation and metadata tagging for microstocks
(Adobe Stock, Shutterstock, Freepik, Getty Images).

QUICK START:
1. Launch LM Studio (https://lmstudio.ai/) on your computer.
2. Load any Vision Language Model (e.g. Qwen2-VL-7B-Instruct, Gemma-3-VL, Llama-3.2-Vision).
3. Start the local server in LM Studio (default port 1234).
4. Run StockAI_Tagger.exe.
5. Select a photo folder and click 'Scan Folder'.
6. Choose 'Start Batch' to tag all photos or select specific photos.

FEATURES:
- Multilingual interface (English, Russian, German, Spanish, French, Japanese, Portuguese, Chinese)
- Direct metadata embedding into JPG/PNG/WebP/TIFF (IPTC, XMP, EXIF)
- Sidecar .XMP generation support
- Export stock_metadata.csv for mass uploads
- ACDSee-style grid view & photo inspector
- Multi-select (Shift+Click, Ctrl+Click)
- Fast metadata clearing (for selected or all files)

System Requirements: Windows 10/11 64-bit.
"""
    with open(portable_dir / "README.txt", "w", encoding="utf-8") as f:
        f.write(readme_content)

    # 4. Create ZIP archive
    zip_path = dist_dir / "StockAI_Tagger_v1.1_Windows_x64.zip"
    print(f"Packing release ZIP: {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(portable_dir):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(dist_dir)
                zf.write(full_path, rel_path)

    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    print("=========================================================")
    print(f"RELEASE READY:")
    print(f"  EXE: {exe_file} ({exe_size_mb:.2f} MB)")
    print(f"  ZIP: {zip_path} ({zip_size_mb:.2f} MB)")
    print("=========================================================")

if __name__ == "__main__":
    main()
