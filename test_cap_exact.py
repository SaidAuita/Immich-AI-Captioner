import json
from immich_client import ImmichClient
from captioner import VlmCaptioner

with open("config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)

imm = ImmichClient(cfg["immich"]["url"], cfg["immich"]["api_key"])
b64 = imm.download_preview_b64("6fd7414c-4ded-415d-a3a7-299f29d4ad73", "preview")

cap = VlmCaptioner(cfg["lm_studio"]["url"], cfg["lm_studio"]["model"], temperature=0.1)
res = cap.generate_caption(b64)

with open("caption_out.json", "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)

print("Saved caption_out.json")
