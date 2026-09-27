import json
import base64
import time
import requests
from urllib3.util import Retry
from requests.adapters import HTTPAdapter

class ImmichClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(self.headers)
        
        # High-performance connection pooling and resilient retries
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        self._tag_cache = {}  # name -> id

    def test_connection(self) -> dict:
        """Verifies connection and returns user info."""
        url = f"{self.base_url}/api/users/me"
        resp = self.session.get(url, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def get_asset_info(self, asset_id: str) -> dict:
        """Fetches detailed information for a single asset."""
        url = f"{self.base_url}/api/assets/{asset_id}"
        resp = self.session.get(url, timeout=45.0)
        resp.raise_for_status()
        return resp.json()

    def get_server_statistics(self) -> dict:
        """Fetches server statistics including total photos and videos."""
        url = f"{self.base_url}/api/server/statistics"
        try:
            resp = self.session.get(url, timeout=30.0)
            return resp.json() if resp.status_code == 200 else {}
        except Exception:
            return {}

    @staticmethod
    def normalize_date_iso(date_str: str, is_end: bool = False) -> str:
        """Parses dates like 01.01.2024, 2024-01-01, 12/30/2024 into standard ISO string."""
        if not date_str or not str(date_str).strip():
            return None
        s = str(date_str).strip().replace('/', '.').replace('-', '.').replace(' ', '.').replace(',', '.')
        parts = [p.strip() for p in s.split('.') if p.strip()]
        if len(parts) != 3:
            return None
        try:
            if len(parts[0]) == 4:
                year, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            elif len(parts[2]) == 4:
                year = int(parts[2])
                p1, p2 = int(parts[0]), int(parts[1])
                if p1 > 12:
                    d, m = p1, p2
                elif p2 > 12:
                    m, d = p1, p2
                else:
                    d, m = p1, p2
            else:
                return None
            if not (1 <= m <= 12 and 1 <= d <= 31 and 1900 <= year <= 2100):
                return None
            time_str = "23:59:59.999Z" if is_end else "00:00:00.000Z"
            return f"{year:04d}-{m:02d}-{d:02d}T{time_str}"
        except Exception:
            return None

    @staticmethod
    def format_date_display(iso_str: str) -> str:
        """Converts ISO timestamp string (YYYY-MM-DD...) back to canonical DD.MM.YYYY."""
        if not iso_str or len(iso_str) < 10:
            return ""
        try:
            year = iso_str[0:4]
            month = iso_str[5:7]
            day = iso_str[8:10]
            return f"{day}.{month}.{year}"
        except Exception:
            return ""

    def get_unprocessed_assets(
        self, 
        page: int = 1, 
        size: int = 100, 
        force_all: bool = False,
        taken_after: str = None,
        taken_before: str = None,
        order: str = "desc"
    ) -> list[dict]:
        """
        Fetches a page of assets that do NOT have a description yet (or all assets if force_all=True).
        Supports optional date range filtering (taken_after, taken_before) and order (asc/desc).
        """
        url = f"{self.base_url}/api/search/metadata"
        payload = {
            "size": size,
            "page": page,
            "isVisible": True,
            "withExif": True,
            "order": order
        }
        if taken_after:
            payload["takenAfter"] = taken_after
        if taken_before:
            payload["takenBefore"] = taken_before

        resp = self.session.post(url, json=payload, timeout=45.0)
        resp.raise_for_status()
        res = resp.json()
        assets_data = res.get('assets', {})
        items = assets_data.get('items', [])
        next_page = assets_data.get('nextPage')
        self.last_items_count = len(items)
        self.last_has_more = bool(items and (next_page is not None or len(items) >= size))

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
        resp = self.session.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.content

    def download_preview_b64(self, asset_id: str, thumbnail_size: str = "preview") -> str:
        """Downloads thumbnail/preview of the asset and returns it as base64 string."""
        img_bytes = self.download_preview_bytes(asset_id, thumbnail_size)
        return base64.b64encode(img_bytes).decode('utf-8')

    def update_description(self, asset_id: str, description: str) -> bool:
        """Updates the description of an asset in Immich."""
        url = f"{self.base_url}/api/assets/{asset_id}"
        payload = {"description": description}
        try:
            resp = self.session.put(url, json=payload, timeout=30.0)
            return resp.status_code in (200, 204)
        except Exception:
            return False

    def load_tags(self) -> dict:
        """Loads all existing tags from Immich into cache."""
        url = f"{self.base_url}/api/tags"
        try:
            resp = self.session.get(url, timeout=30.0)
            if resp.status_code == 200:
                tags = resp.json()
                self._tag_cache = {t['name'].lower(): t['id'] for t in tags if 'name' in t and 'id' in t}
                return self._tag_cache
        except Exception:
            pass
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
        payload = {"name": clean_name}
        try:
            resp = self.session.post(url, json=payload, timeout=25.0)
            if resp.status_code in (200, 201):
                tag = resp.json()
                tid = tag.get('id')
                if tid:
                    self._tag_cache[key] = tid
                    return tid
            elif resp.status_code in (400, 409):
                # Tag already exists or duplicate
                self.load_tags()
                return self._tag_cache.get(key)
        except Exception:
            self.load_tags()
            return self._tag_cache.get(key)
        return None

    def tag_asset(self, tag_id: str, asset_id: str) -> bool:
        """Assigns a tag to an asset."""
        if not tag_id or not asset_id:
            return False
        url = f"{self.base_url}/api/tags/{tag_id}/assets"
        payload = {"ids": [asset_id]}
        try:
            resp = self.session.put(url, json=payload, timeout=25.0)
            if resp.status_code in (200, 204):
                try:
                    res_data = resp.json()
                    if isinstance(res_data, list) and len(res_data) > 0:
                        res_item = res_data[0]
                        return res_item.get("success", False) or res_item.get("error") == "duplicate"
                except Exception:
                    pass
                return True
            return False
        except Exception:
            return False

    def apply_tags_to_asset(self, asset_id: str, tag_names: list[str]) -> int:
        """Ensures all tags exist and assigns them to the asset reliably. Returns count of applied tags."""
        if not tag_names or not asset_id:
            return 0

        if not self._tag_cache:
            self.load_tags()

        # Step 1: Collect tag IDs (fast cache lookup, creates new if needed)
        tag_ids = []
        tags_to_create = []
        for name in tag_names:
            clean_name = name.strip()
            if not clean_name:
                continue
            key = clean_name.lower()
            if key in self._tag_cache:
                tag_ids.append(self._tag_cache[key])
            else:
                tags_to_create.append(clean_name)

        if tags_to_create:
            # Reload tag cache once before attempting creations
            self.load_tags()
            for clean_name in tags_to_create:
                key = clean_name.lower()
                if key in self._tag_cache:
                    tag_ids.append(self._tag_cache[key])
                else:
                    tid = self.get_or_create_tag(clean_name)
                    if tid:
                        tag_ids.append(tid)

        if not tag_ids:
            return 0

        # Deduplicate tag IDs while preserving order
        unique_tag_ids = list(dict.fromkeys(tag_ids))

        # Step 2: Assign tags sequentially over Keep-Alive socket to guarantee database consistency without race conditions
        applied = 0
        failed_ids = []
        for tid in unique_tag_ids:
            if self.tag_asset(tid, asset_id):
                applied += 1
            else:
                failed_ids.append(tid)

        # Retry any failed tags once with brief pause
        if failed_ids:
            time.sleep(0.2)
            for tid in failed_ids:
                if self.tag_asset(tid, asset_id):
                    applied += 1

        return applied
