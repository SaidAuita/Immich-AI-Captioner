import os
import sys
import subprocess

def run_build():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(base_dir, "dist_release")
    work_dir = os.path.join(base_dir, "build_release")
    os.makedirs(dist_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    print("=== [1/2] Building ImmichAI_Captioner_Standalone.exe ===")
    cmd_standalone = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--icon=app_icon.ico",
        "--distpath", dist_dir,
        "--workpath", work_dir,
        "--name", "ImmichAI_Captioner_Standalone",
        "--collect-all", "customtkinter",
        "--collect-all", "darkdetect",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "tkinter",
        "--hidden-import", "_tkinter",
        "--add-data", "locales;locales",
        "ui_app.py"
    ]
    res1 = subprocess.run(cmd_standalone, cwd=base_dir)
    if res1.returncode != 0:
        print("ERROR: Standalone build failed!")
        sys.exit(res1.returncode)

    print("\n=== [2/2] Building ImmichAI_Captioner_Worker.exe ===")
    cmd_worker = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--icon=app_icon.ico",
        "--distpath", dist_dir,
        "--workpath", work_dir,
        "--name", "ImmichAI_Captioner_Worker",
        "--collect-all", "customtkinter",
        "--collect-all", "darkdetect",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "tkinter",
        "--hidden-import", "_tkinter",
        "--add-data", "locales;locales",
        "ui_worker.py"
    ]
    res2 = subprocess.run(cmd_worker, cwd=base_dir)
    if res2.returncode != 0:
        print("ERROR: Worker build failed!")
        sys.exit(res2.returncode)

    print("\n=== BUILD COMPLETE ===")
    for f in os.listdir(dist_dir):
        fp = os.path.join(dist_dir, f)
        print(f"  {f} ({os.path.getsize(fp):,} bytes)")

if __name__ == "__main__":
    run_build()
