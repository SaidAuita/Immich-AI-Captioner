import json
import os
import sys
import time

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(__file__)

STATE_FILE = os.path.join(get_base_dir(), "state.json")

class StateManager:
    def __init__(self, file_path: str = STATE_FILE):
        self.file_path = file_path
        self.state = {
            "processed_count": 0,
            "processed_ids": [],
            "failed_ids": {},
            "last_processed_at": None
        }
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception:
                pass

    def save(self):
        # Keep last 5000 IDs to avoid unbounded memory growth
        if len(self.state.get("processed_ids", [])) > 10000:
            self.state["processed_ids"] = self.state["processed_ids"][-5000:]
            
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def mark_processed(self, asset_id: str):
        self.state["processed_count"] = self.state.get("processed_count", 0) + 1
        if "processed_ids" not in self.state:
            self.state["processed_ids"] = []
        if asset_id not in self.state["processed_ids"]:
            self.state["processed_ids"].append(asset_id)
        if asset_id in self.state.get("failed_ids", {}):
            del self.state["failed_ids"][asset_id]
        self.state["last_processed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def mark_failed(self, asset_id: str, reason: str):
        if "failed_ids" not in self.state:
            self.state["failed_ids"] = {}
        fails = self.state["failed_ids"].get(asset_id, 0)
        self.state["failed_ids"][asset_id] = fails + 1
        self.save()

    def should_skip(self, asset_id: str) -> bool:
        if asset_id in self.state.get("processed_ids", []):
            return True
        # If an asset failed more than 3 times, skip it
        if self.state.get("failed_ids", {}).get(asset_id, 0) >= 3:
            return True
        return False

    def reset_processed(self):
        """Resets the list of processed IDs and counter to allow re-processing."""
        self.state["processed_ids"] = []
        self.state["processed_count"] = 0
        self.state["failed_ids"] = {}
        self.save()
