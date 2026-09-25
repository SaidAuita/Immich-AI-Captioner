import urllib.request
import urllib.error
import http.client
import socket
import json
import base64
import time

class ImmichClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self._tag_cache = {}  # name -> id

    def _urlopen(self, req: urllib.request.Request, timeout: float = 45.0, retries: int = 3, delay: float = 1.5):
        """Executes a request with automatic retries on network timeouts or temporary connection glitches."""
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                return urllib.request.urlopen(req, timeout=timeout)
            except (socket.timeout, TimeoutError, urllib.error.URLError, http.client.RemoteDisconnected) as e:
                last_err = e
                if attempt < retries:
                    time.sleep(delay * attempt)
                else:
                    raise last_err

    def test_connection(self) -> dict:
        """Verifies connection and returns user info."""
        url = f"{self.base_url}/api/users/me"
        req = urllib.request.Request(url, headers=self.headers)
        with self._urlopen(req, timeout=30.0, retries=3) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def get_asset_info(self, asset_id: str) -> dict:
        """Fetches detailed information for a single asset."""
        url = f"{self.base_url}/api/assets/{asset_id}"
        req = urllib.request.Request(url, headers=self.headers)
        with self._urlopen(req, timeout=45.0, retries=3) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def get_server_statistics(self) -> dict:
        """Fetches server statistics including total photos and videos."""
        url = f"{self.base_url}/api/server/statistics"
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with self._urlopen(req, timeout=30.0, retries=3) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception:
            return {}

    def get_unprocessed_assets(self, page: int = 1, size: int = 100, force_all: bool = False) -> list[dict]:
        """
        Fetches a page of assets that do NOT have a description yet (or all assets if force_all=True).
        """
        url = f"{self.base_url}/api/search/metadata"
        payload = {
            "size": size,
            "page": page,
            "isVisible": True,
            "withExif": True,
            "order": "desc"
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=self.headers)
        
        with self._urlopen(req, timeout=45.0, retries=3) as resp:
            res = json.loads(resp.read().decode('utf-8'))
            items = res.get('assets', {}).get('items', [])

        unprocessed = []
        for it in items:
            if it.get('type') != 'IMAGE':
                continue
            if force_all:
                unprocessed.append(it)
            else:
                desc = (it.get('exifInfo', {}).get('description') or it.get('description') or '').strip()
                if not desc:
                    unprocessed.append(it)
        return unprocessed

    def download_preview_bytes(self, asset_id: str, thumbnail_size: str = "preview") -> bytes:
        """Downloads thumbnail/preview of the asset and returns raw bytes."""
        url = f"{self.base_url}/api/assets/{asset_id}/thumbnail?size={thumbnail_size}"
        req = urllib.request.Request(url, headers={"x-api-key": self.api_key})
        with self._urlopen(req, timeout=60.0, retries=3) as resp:
            return resp.read()

    def download_preview_b64(self, asset_id: str, thumbnail_size: str = "preview") -> str:
        """Downloads thumbnail/preview of the asset and returns it as base64 string."""
        img_bytes = self.download_preview_bytes(asset_id, thumbnail_size)
        return base64.b64encode(img_bytes).decode('utf-8')

    def update_description(self, asset_id: str, description: str) -> bool:
        """Updates the description of an asset in Immich."""
        url = f"{self.base_url}/api/assets/{asset_id}"
        payload = {"description": description}
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=self.headers, method="PUT")
        try:
            with self._urlopen(req, timeout=30.0, retries=3) as resp:
                return resp.status in (200, 204)
        except Exception:
            return False

    def load_tags(self) -> dict:
        """Loads all existing tags from Immich into cache."""
        url = f"{self.base_url}/api/tags"
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with self._urlopen(req, timeout=30.0, retries=3) as resp:
                tags = json.loads(resp.read().decode('utf-8'))
                self._tag_cache = {t['name'].lower(): t['id'] for t in tags if 'name' in t and 'id' in t}
                return self._tag_cache
        except Exception:
            return self._tag_cache

    def get_or_create_tag(self, name: str) -> str | None:
        """Returns tag ID by name, creating it if it does not exist."""
        clean_name = name.strip()
        if not clean_name:
            return None

        key = clean_name.lower()
        if key in self._tag_cache:
            return self._tag_cache[key]

        # Try to create tag
        url = f"{self.base_url}/api/tags"
        payload = json.dumps({"name": clean_name}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers=self.headers, method="POST")
        try:
            with self._urlopen(req, timeout=25.0, retries=2) as resp:
                tag = json.loads(resp.read().decode('utf-8'))
                tid = tag.get('id')
                if tid:
                    self._tag_cache[key] = tid
                    return tid
        except urllib.error.HTTPError:
            # If tag already exists (race condition or duplicate name)
            self.load_tags()
            return self._tag_cache.get(key)
        except Exception:
            pass
        return None

    def tag_asset(self, tag_id: str, asset_id: str) -> bool:
        """Assigns a tag to an asset."""
        if not tag_id or not asset_id:
            return False
        url = f"{self.base_url}/api/tags/{tag_id}/assets"
        payload = json.dumps({"ids": [asset_id]}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers=self.headers, method="PUT")
        try:
            with self._urlopen(req, timeout=25.0, retries=2) as resp:
                return resp.status in (200, 204)
        except Exception:
            return False

    def apply_tags_to_asset(self, asset_id: str, tag_names: list[str]) -> int:
        """Ensures all tags exist and assigns them to the asset. Returns count of applied tags."""
        if not self._tag_cache:
            self.load_tags()

        applied = 0
        for name in tag_names:
            tid = self.get_or_create_tag(name)
            if tid:
                if self.tag_asset(tid, asset_id):
                    applied += 1
        return applied
