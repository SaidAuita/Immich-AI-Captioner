# Distributed (Cluster) Mode Setup Guide

Distributed Mode is designed for environments where the Immich instance runs on a low-power home server or NAS (Unraid, Synology, TrueNAS, Raspberry Pi), while AI inferencing is offloaded to a separate powerful PC equipped with an NVIDIA RTX graphics card.

---

## 1. Architecture Overview

1. **Server (Immich + ExifTool Daemon)**:
   - `coordinator.py`: Periodically queries Immich for photos lacking tags and saves thumbnail tasks into a shared folder (`CaptionQueue/In/`).
   - `apply_metadata.py`: Reads completed recognition results from `CaptionQueue/Out/` and embeds IPTC/XMP tags directly into original files using ExifTool.
2. **Workstation (GPU Client)**:
   - `ImmichCaptionWorker.exe`: Connects to `\\NAS\CaptionQueue`, pulls pending items, runs inference in local LM Studio, and writes structured outputs to `Out/`.

---

## 2. Server Setup (Linux / Docker)

1. Create a shared network directory accessible via SMB/NFS:
   ```bash
   mkdir -p /mnt/photos/CaptionQueue/In
   mkdir -p /mnt/photos/CaptionQueue/Out
   ```
2. Configure `coordinator_config.json`:
   ```json
   {
     "immich": {
       "url": "http://localhost:2283",
       "api_key": "YOUR_IMMICH_API_KEY",
       "batch_size": 25
     },
     "queue": {
       "base_dir": "/mnt/photos/CaptionQueue"
     }
   }
   ```
3. Install and enable the systemd service for metadata embedding:
   ```bash
   sudo bash install_service.sh
   ```

---

## 3. Workstation Setup (Windows PC with GPU)

1. Copy `worker_config.example.json` to `worker_config.json`:
   ```json
   {
     "queue": {
       "base_dir": "\\\\NAS\\CaptionQueue",
       "in_dir_name": "In",
       "out_dir_name": "Out"
     },
     "lm_studio": {
       "url": "http://localhost:1234/v1",
       "model": "qwen2.5-vl-7b-instruct"
     },
     "worker": {
       "id": "rtx-4090-worker"
     }
   }
   ```
2. Start `ImmichCaptionWorker.exe`.
3. The worker will automatically claim jobs, send previews to LM Studio, and push results back to the server.
