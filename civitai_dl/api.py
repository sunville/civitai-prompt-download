"""civitai 官方 REST API 客户端：限速、重试、代理、API Key。"""
from __future__ import annotations

import gzip
import json
import random
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from . import __version__
from .config import Settings
from .errors import ApiError, AuthError, CancelledError

USER_AGENT = f"CivitaiPromptDL/{__version__} (Windows; +https://civitai.com)"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY_WAIT = 120.0


class RateLimiter:
    """全局限速：保证两次请求间隔不小于 interval 秒，等待期间可取消。"""

    def __init__(self, interval: float = 1.0):
        self.interval = max(0.05, float(interval))
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self, cancel: threading.Event | None = None) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._last + self.interval - now
            if delay <= 0:
                self._last = now
                return
            target = now + delay
            self._last = target
        while True:
            remaining = target - time.monotonic()
            if remaining <= 0:
                return
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            time.sleep(min(0.2, remaining))


class CivitaiClient:
    def __init__(
        self,
        settings: Settings,
        log: Callable[[str, str], None] | None = None,
        cancel: threading.Event | None = None,
    ):
        self.settings = settings
        self.site = settings.site
        self.base_url = f"https://{self.site}/api/v1"
        self.web_url = f"https://{self.site}"
        self.log = log or (lambda level, msg: None)
        self.cancel = cancel or threading.Event()
        self.limiter = RateLimiter(settings.rate_interval)
        self._opener = self._build_opener()
        self._ssl = ssl.create_default_context()
        self._request_count = 0

    # ---------------- 网络 ----------------
    def _build_opener(self) -> urllib.request.OpenerDirector:
        mode = self.settings.proxy_mode
        if mode == "manual" and self.settings.proxy_url.strip():
            proxy = self.settings.proxy_url.strip()
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        elif mode == "direct":
            handler = urllib.request.ProxyHandler({})
        else:  # system
            handler = urllib.request.ProxyHandler()
        return urllib.request.build_opener(handler)

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        key = self.settings.api_key.strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def request_json(
        self,
        url: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        last_error: Exception | None = None
        for attempt in range(self.settings.retries + 1):
            if self.cancel.is_set():
                raise CancelledError()
            self.limiter.wait(self.cancel)
            try:
                self._request_count += 1
                req = urllib.request.Request(url, data=data, method=method, headers=self._headers(body is not None))
                with self._opener.open(req, timeout=self.settings.timeout) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body_text = self._safe_body(exc)
                if exc.code in (401, 403):
                    raise AuthError(
                        "API Key 无效、过期或没有权限" if exc.code == 401 else "访问被拒绝（可能是 API Key 权限不足或内容受限）",
                        status=exc.code, url=url, body=body_text[:500],
                        hint="请到 civitai 设置页重新生成 API Key，并在「设置」里重新填写",
                    ) from exc
                if exc.code in RETRYABLE_STATUS and attempt < self.settings.retries:
                    wait = self._retry_wait(exc, attempt)
                    self.log("WARN", f"HTTP {exc.code}，{wait:.1f} 秒后重试（{attempt + 1}/{self.settings.retries}）")
                    self._sleep(wait)
                    last_error = exc
                    continue
                friendly = {
                    404: "接口不存在或内容已被删除",
                    400: "请求参数不被接口接受（可能是筛选条件组合不合法）",
                    429: "请求太频繁，被 civitai 限流",
                }.get(exc.code, f"接口返回 HTTP {exc.code}")
                raise ApiError(friendly, status=exc.code, url=url, body=body_text[:800]) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError) as exc:
                reason = getattr(exc, "reason", exc)
                last_error = exc
                if attempt < self.settings.retries:
                    wait = min(MAX_RETRY_WAIT, (2 ** attempt) + random.uniform(0, 0.5))
                    self.log("WARN", f"网络错误：{reason}，{wait:.1f} 秒后重试（{attempt + 1}/{self.settings.retries}）")
                    self._sleep(wait)
                    continue
                raise ApiError(
                    "网络连接失败，无法访问 civitai",
                    status=None, url=url, body=str(reason),
                    hint="检查网络/代理是否可用；可在「设置」里填写代理地址",
                ) from exc
            except ValueError as exc:
                raise ApiError("接口返回的内容无法解析为 JSON", url=url, body=str(exc)[:500]) from exc
        raise ApiError("请求失败", url=url, body=str(last_error))

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancel.is_set():
                raise CancelledError()
            time.sleep(min(0.2, end - time.monotonic()))

    def _retry_wait(self, exc: urllib.error.HTTPError, attempt: int) -> float:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            try:
                return min(MAX_RETRY_WAIT, max(1.0, float(retry_after)))
            except ValueError:
                pass
        return min(MAX_RETRY_WAIT, (2 ** attempt) * 2 + random.uniform(0, 0.5))

    @staticmethod
    def _safe_body(exc: urllib.error.HTTPError) -> str:
        try:
            return exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""

    # ---------------- 业务接口 ----------------
    def images(self, **params: Any) -> dict[str, Any]:
        return self.request_json(f"{self.base_url}/images", params=params)

    def model_version(self, version_id: int) -> dict[str, Any]:
        return self.request_json(f"{self.base_url}/model-versions/{version_id}")

    def model(self, model_id: int) -> dict[str, Any]:
        return self.request_json(f"{self.base_url}/models/{model_id}")

    def search_models(self, query: str = "", tag: str = "", types: str = "", limit: int = 20) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if query:
            params["query"] = query
        if tag:
            params["tag"] = tag
        if types:
            params["types"] = types
        return self.request_json(f"{self.base_url}/models", params=params)

    def search_tags(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        data = self.request_json(f"{self.base_url}/tags", params={"query": query, "limit": limit})
        return data.get("items", [])

    def image_page_url(self, image_id: int) -> str:
        return f"{self.web_url}/images/{image_id}"

    def model_page_url(self, model_id: int) -> str:
        return f"{self.web_url}/models/{model_id}"

    @property
    def request_count(self) -> int:
        return self._request_count
