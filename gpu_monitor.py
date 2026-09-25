import subprocess
import ctypes
import os

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]

def get_user_idle_seconds() -> float:
    """Returns number of seconds since last mouse/keyboard activity."""
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return max(0.0, millis / 1000.0)
    except Exception:
        pass
    return 0.0

_CACHED_GPU_NAME = None

def get_gpu_name() -> str:
    """
    Returns detected GPU model name, e.g. 'RTX 3090', 'RTX 4090', etc.
    """
    global _CACHED_GPU_NAME
    if _CACHED_GPU_NAME is not None:
        return _CACHED_GPU_NAME
    try:
        smi_path = "nvidia-smi"
        if not os.path.exists(smi_path) and os.path.exists(r"C:\Windows\System32\nvidia-smi.exe"):
            smi_path = r"C:\Windows\System32\nvidia-smi.exe"
            
        out = subprocess.check_output(
            [smi_path, '--query-gpu=name', '--format=csv,noheader'],
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            timeout=3
        ).decode().strip()
        # Clean common prefixes for compact display
        cleaned = out.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").strip()
        _CACHED_GPU_NAME = cleaned if cleaned else out
    except Exception:
        _CACHED_GPU_NAME = "GPU"
    return _CACHED_GPU_NAME

def get_gpu_stats():
    """
    Returns (gpu_util_percent, mem_used_mb, mem_total_mb).
    Returns (0, 0, 0) if nvidia-smi fails.
    """
    try:
        smi_path = "nvidia-smi"
        # Standard system32 fallback if nvidia-smi not in PATH
        if not os.path.exists(smi_path) and os.path.exists(r"C:\Windows\System32\nvidia-smi.exe"):
            smi_path = r"C:\Windows\System32\nvidia-smi.exe"
            
        out = subprocess.check_output(
            [smi_path, '--query-gpu=utilization.gpu,memory.used,memory.total', '--format=csv,noheader,nounits'],
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            timeout=3
        ).decode().strip()
        parts = [int(x.strip()) for x in out.split(',')]
        return parts[0], parts[1], parts[2]
    except Exception:
        return 0, 0, 0

def is_system_busy(throttling_config) -> tuple[bool, str]:
    """
    Checks if the system is currently under heavy load or actively used.
    Returns (is_busy: bool, reason: str).
    """
    # 1. Check GPU utilization
    max_gpu = throttling_config.get("max_gpu_util_percent", 85)
    gpu_util, mem_used, mem_total = get_gpu_stats()
    if gpu_util > max_gpu:
        return True, f"GPU нагружен: {gpu_util}% (лимит: {max_gpu}%)"

    # 2. Check user idle requirement (if configured > 0)
    req_idle = throttling_config.get("require_user_idle_seconds", 0)
    if req_idle > 0:
        idle_sec = get_user_idle_seconds()
        if idle_sec < req_idle:
            return True, f"Пользователь активен (простой {idle_sec:.0f}с из требуемых {req_idle}с)"

    # 3. Check for specific heavy processes
    heavy_procs = throttling_config.get("heavy_processes", [])
    if heavy_procs:
        try:
            # Quick tasklist check
            out = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                timeout=3
            ).decode(errors='ignore').lower()
            for p in heavy_procs:
                if p.lower() in out:
                    return True, f"Обнаружен ресурсоёмкий процесс: {p}"
        except Exception:
            pass

    return False, ""

if __name__ == '__main__':
    util, used, total = get_gpu_stats()
    idle = get_user_idle_seconds()
    print(f"GPU Util: {util}%, VRAM: {used}/{total} MB")
    print(f"User idle: {idle:.1f}s")
    busy, reason = is_system_busy({"max_gpu_util_percent": 30, "require_user_idle_seconds": 10})
    print(f"System busy: {busy} (Reason: '{reason}')")
