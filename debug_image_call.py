import json
import urllib.request
from immich_client import ImmichClient

with open("config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)

imm = ImmichClient(cfg["immich"]["url"], cfg["immich"]["api_key"])
b64 = imm.download_preview_b64("6fd7414c-4ded-415d-a3a7-299f29d4ad73", "preview")

url = f"{cfg['lm_studio']['url']}/chat/completions"
payload = {
    "model": cfg["lm_studio"]["model"],
    "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "Опиши фото одним словом на русском:"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]}
    ],
    "temperature": 0.1,
    "max_tokens": 100
}

data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as resp:
    raw_bytes = resp.read()

with open("raw_response.json", "wb") as f:
    f.write(raw_bytes)

print("Saved raw_response.json, size:", len(raw_bytes))
