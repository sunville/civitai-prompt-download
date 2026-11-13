"""模型 / 版本名称解析（带 SQLite 缓存，避免重复请求）。"""
from __future__ import annotations

import threading
from typing import Any, Callable

from .api import CivitaiClient
from .errors import AppError
from .store import Store


class ModelNameResolver:
    def __init__(self, client: CivitaiClient, store: Store, enabled: bool = True,
                 log: Callable[[str, str], None] | None = None):
        self.client = client
        self.store = store
        self.enabled = enabled
        self.log = log or (lambda level, msg: None)
        self._lock = threading.RLock()
        self._failed: set[int] = set()

    def get(self, version_id: int) -> dict[str, Any] | None:
        if not version_id:
            return None
        cached = self.store.get_model_cache(version_id)
        if cached:
            return cached
        if not self.enabled or version_id in self._failed:
            return None
        try:
            data = self.client.model_version(int(version_id))
        except AppError as exc:
            with self._lock:
                self._failed.add(int(version_id))
            self.log("WARN", f"解析模型版本 {version_id} 名称失败：{exc.friendly()}")
            return None
        model = data.get("model") or {}
        info = {
            "version_id": int(version_id),
            "model_id": model.get("id"),
            "name": model.get("name") or data.get("name") or "",
            "type": model.get("type") or "",
            "base_model": data.get("baseModel") or "",
        }
        self.store.put_model_cache(info["version_id"], info["model_id"], info["name"], info["type"], info["base_model"])
        return info
