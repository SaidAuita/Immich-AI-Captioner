# Standalone (Direct) Mode Setup Guide

In Standalone Mode, **Immich AI Captioner** runs directly on your Windows PC or laptop equipped with an NVIDIA GPU. It communicates with your Immich server over the network via its REST API and processes image recognitions via your local LM Studio / Ollama instance.

---

## 1. Prerequisites

1. **Immich Server**: An active instance (e.g. `http://192.168.1.100:2283` or `https://immich.your-domain.com`).
2. **Immich API Key**:
   - Log into your Immich web interface.
   - Click your profile icon (top right) -> **Account Settings** -> **API Keys**.
   - Click **New API Key**, name it `Captioner`, and copy the token.
3. **Local Vision LLM**:
   - Install [LM Studio](https://lmstudio.ai/).
   - Search for and download a Vision model such as `qwen2.5-vl-7b-instruct` or `qwen3-vl-8b-instruct`.
   - In LM Studio, go to the **Local Server** tab (`<->`), select the model, and click **Start Server** on port `1234`.

---

## 2. Configuration (`config.json`)

Create a `config.json` file next to `ImmichCaptioner.exe`:

```json
{
  "immich": {
    "url": "http://192.168.1.100:2283",
    "api_key": "YOUR_IMMICH_API_KEY_HERE",
    "batch_size": 50,
    "thumbnail_size": "preview"
  },
  "lm_studio": {
    "url": "http://localhost:1234/v1",
    "model": "qwen2.5-vl-7b-instruct",
    "temperature": 0.1,
    "max_tokens": 350
  },
  "immich_description_mode": "tags_only",
  "throttling": {
    "mode": "auto_85",
    "max_gpu_util_percent": 85,
    "check_interval_busy_seconds": 15,
    "idle_delay_between_photos_seconds": 1,
    "require_user_idle_seconds": 0,
    "heavy_processes": [
      "cyberpunk2077.exe",
      "blender.exe",
      "unrealengine.exe"
    ]
  },
  "ui_language": "en"
}
```

---

## 3. Running & Operation

1. Start `ImmichCaptioner.exe`.
2. Check the header status: it should display `Connected to Immich (YourUsername)`.
3. If you want to test recognition first:
   - Copy a photo URL from your Immich web gallery (e.g. `https://.../photos/<uuid>`).
   - Paste it into the `🧪 Test Photo` bar and click `⚡ Recognize`.
   - The recognized title, description, and keywords will appear in the dashboard preview.
4. Click **▶ Start** to begin processing your entire library.
5. You can safely minimize the window to the system tray.
