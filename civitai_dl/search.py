"""实验性：civitai 站内图片搜索（非官方公开契约，可能随时失效）。

协议参考社区开源工具（gallery-dl）的实现：POST search-new.civitai.com/multi-search，
索引 images_v6。仅在用户显式选择「站内搜索图片（实验）」入口时使用。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .errors import ApiError, CancelledError

SEARCH_ROOT = "https://search-new.civitai.com"
DEFAULT_TOKEN = (
    "8c46eb2508e21db1e9828a97968d91ab1ca1caa5f70a00e88a2ba1e286603b61"
)
DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "X-Meilisearch-Client": "Meilisearch instant-meilisearch (v0.13.5) ; Meilisearch JavaScript (v0.34.0)",
    "Priority": "u=4",
}

FACETS = [
    "aspectRatio", "baseModel", "createdAtUnix", "tagNames", "techniqueNames",
    "toolNames", "type", "user.username",
]


def _nsfw_filter(level_mask: int) -> str:
    parts = [str(bit) for bit in (1, 2, 4, 8, 16) if level_mask & bit]
    if not parts:
        return "()"
    return "(nsfwLevel=" + " OR nsfwLevel=".join(parts) + ")"


def search_images(
    query: str,
    limit: int = 100,
    token: str = "",
    nsfw_mask: int = 31,
    timeout: int = 40,
) -> list[dict]:
    """返回搜索命中的图片对象列表（含 id 等字段）。"""
    token = (token or "").strip() or DEFAULT_TOKEN
    headers = dict(DEFAULT_HEADERS)
    headers["Authorization"] = token if " " in token else "Bearer " + token
    hits: list[dict] = []
    offset = 0
    page_size = min(51, max(1, limit))
    while len(hits) < limit:
        payload = {
            "queries": [{
                "q": query,
                "indexUid": "images_v6",
                "facets": FACETS,
                "attributesToHighlight": [],
                "highlightPreTag": "__ais-highlight__",
                "highlightPostTag": "__/ais-highlight__",
                "limit": page_size,
                "offset": offset,
                "filter": _nsfw_filter(nsfw_mask),
            }]
        }
        req = urllib.request.Request(
            SEARCH_ROOT + "/multi-search",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001
                pass
            raise ApiError(
                "站内搜索服务不可用（实验功能）",
                status=exc.code, url=SEARCH_ROOT + "/multi-search", body=body,
                hint="该功能依赖非官方搜索服务，可能随时失效；可改用「按模型 / 作者 / 榜单」入口",
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ApiError(
                "无法连接站内搜索服务（实验功能）",
                url=SEARCH_ROOT + "/multi-search", body=str(exc),
                hint="检查网络/代理；或改用官方入口「按模型 / 作者 / 榜单」",
            ) from exc
        except ValueError as exc:
            raise ApiError("站内搜索返回的内容无法解析", url=SEARCH_ROOT + "/multi-search", body=str(exc)[:300]) from exc

        results = data.get("results") or [{}]
        page_hits = results[0].get("hits") or []
        hits.extend(page_hits)
        if len(page_hits) < page_size:
            break
        offset += len(page_hits)
    return hits[:limit]


def hit_image_id(hit: dict) -> int | None:
    for key in ("id", "imageId", "image_id"):
        value = hit.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None
