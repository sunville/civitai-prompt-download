"""任务执行器：分页抓取 → 过滤 → 解析 → 入库，支持暂停/继续/取消/断点续传。"""
from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import date, datetime
from typing import Any, Callable

from .api import CivitaiClient
from .config import Settings
from .errors import ApiError, AppError, CancelledError
from .parser import build_record, normalize_meta, parse_target_url, url_query_value
from .resolver import ModelNameResolver
from .search import hit_image_id, search_images
from .store import Store

PAGE_SIZE = 100
LEVEL_NSFW = {"Soft", "Mature", "X"}


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _item_date(item: dict[str, Any]) -> date | None:
    raw = item.get("createdAt")
    if not raw:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


class TaskRunner:
    """一个任务一个线程；事件通过回调交给 UI。"""

    def __init__(self, store: Store, settings: Settings, task_id: int,
                 on_event: Callable[[str, dict[str, Any]], None] | None = None):
        self.store = store
        self.settings = settings
        self.task_id = task_id
        self.on_event = on_event or (lambda kind, payload: None)
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: CivitaiClient | None = None
        self._resolver: ModelNameResolver | None = None
        self._counters = {
            "kept": 0, "fetched": 0, "skipped_no_meta": 0, "skipped_tool": 0,
            "skipped_tags": 0, "skipped_date": 0, "skipped_dup": 0,
        }
        self._cursor_state: dict[str, Any] = {}
        self._limit = 1000
        self._config: dict[str, Any] = {}
        self._finished = False

    # ---------------- 对外控制 ----------------
    def start(self) -> threading.Thread:
        self._thread = threading.Thread(target=self._thread_main, name=f"task-{self.task_id}", daemon=True)
        self._thread.start()
        return self._thread

    def pause(self) -> None:
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.pause_event.clear()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------------- 事件 ----------------
    def _emit(self, kind: str, **payload: Any) -> None:
        payload.setdefault("task_id", self.task_id)
        try:
            self.on_event(kind, payload)
        except Exception:  # noqa: BLE001  UI 回调异常不影响任务
            pass

    def _log(self, level: str, message: str) -> None:
        self._emit("log", level=level, message=message)

    def _save_progress(self, status: str | None = None, message: str | None = None) -> None:
        fields: dict[str, Any] = dict(self._counters)
        fields["cursor"] = json.dumps(self._cursor_state, ensure_ascii=False)
        if self._client is not None:
            fields["requests"] = self._client.request_count
        if status:
            fields["status"] = status
        if message is not None:
            fields["message"] = message
        self.store.update_task(self.task_id, **fields)
        self._emit("progress", kept=self._counters["kept"], fetched=self._counters["fetched"],
                   requests=(self._client.request_count if self._client else 0))

    # ---------------- 线程主体 ----------------
    def _thread_main(self) -> None:
        try:
            self._run()
        except CancelledError:
            self._save_progress(status="cancelled", message="已取消")
            self._log("WARN", "任务已取消")
            self._emit("status", status="cancelled", message="已取消")
        except AppError as exc:
            self._save_progress(status="failed", message=exc.friendly())
            self._log("ERROR", exc.friendly())
            if exc.detail:
                self._log("ERROR", "详情：" + exc.detail.replace("\n", " | ")[:800])
            self._emit("status", status="failed", message=exc.friendly())
        except Exception as exc:  # noqa: BLE001
            detail = traceback.format_exc()
            self._log("ERROR", f"未预期错误：{exc}")
            self._log("ERROR", detail.replace("\n", " | ")[:1200])
            self._save_progress(status="failed", message=f"未预期错误：{exc}")
            self._emit("status", status="failed", message=f"未预期错误：{exc}")

    def _prepare(self) -> None:
        task = self.store.get_task(self.task_id)
        if not task:
            raise AppError("任务不存在")
        self._config = json.loads(task.get("config_json") or "{}")
        self._config.setdefault("site", self.settings.site)
        self._limit = int(self._config.get("limit") or self.settings.default_limit)
        try:
            self._cursor_state = json.loads(task.get("cursor") or "{}") or {}
        except ValueError:
            self._cursor_state = {}
        # 续跑时以数据库实际条数为准
        self._counters["kept"] = self.store.count_items(self.task_id)
        for key in ("fetched", "skipped_no_meta", "skipped_tool", "skipped_tags", "skipped_date", "skipped_dup"):
            self._counters[key] = int(task.get(key) or 0)

        self._client = CivitaiClient(self.settings, log=self._log, cancel=self.cancel_event)
        self._resolver = ModelNameResolver(
            self._client, self.store,
            enabled=bool(self._config.get("resolve_model_names", self.settings.resolve_model_names)),
            log=self._log,
        )
        self.store.update_task(self.task_id, status="running", message="")
        self._log("INFO", f"开始任务：{task.get('name')}")
        self._log("INFO", f"目标：保留 {self._limit} 条；站点 {self._config.get('site')}；"
                          f"内容级别 {self._config.get('content_level')}；排序 {self._config.get('sort')}")

    def _run(self) -> None:
        self._prepare()
        entry = str(self._config.get("entry") or "model")
        if entry == "site_search":
            self._run_site_search()
        elif entry == "urls":
            self._run_urls()
        elif entry in ("model", "lora", "keyword"):
            versions = self._resolve_task_model_versions()
            if versions:
                self._run_versions(entry, versions)
            else:
                self._run_paginated(entry=entry, target_index=0)
        else:
            self._run_paginated(entry=entry, target_index=0)
        if not self._finished:
            self._finish_done("已完成")

    # ---------------- 入口：分页抓取 ----------------
    def _base_params(self) -> dict[str, Any]:
        cfg = self._config
        sort = str(cfg.get("sort") or "Newest")
        params: dict[str, Any] = {"limit": PAGE_SIZE, "withMeta": "true", "sort": sort}
        if sort not in ("Newest", "Oldest"):
            params["period"] = str(cfg.get("period") or "AllTime")
        params["nsfw"] = "None" if cfg.get("content_level") == "sfw" else "X"
        return params

    def _build_params(self, entry: str, cursor: str | None = None) -> dict[str, Any]:
        cfg = self._config
        params = self._base_params()
        if entry in ("model", "lora", "keyword"):
            if cfg.get("model_version_id"):
                params["modelVersionId"] = int(cfg["model_version_id"])
            elif cfg.get("model_id"):
                params["modelId"] = int(cfg["model_id"])
        elif entry == "author":
            params["username"] = str(cfg.get("username") or "")
        elif entry == "ranking":
            pass
        if cursor:
            params["cursor"] = cursor
        return params

    def _resolve_task_model_versions(self) -> list[int]:
        """按模型/ LoRA 抓取时，解析出该模型的全部版本 ID（官方 modelId 过滤对部分模型失效）。"""
        cfg = self._config
        if cfg.get("model_version_ids"):
            return [int(v) for v in cfg["model_version_ids"] if str(v).isdigit()]
        if cfg.get("model_version_id"):
            return [int(cfg["model_version_id"])]
        model_id = cfg.get("model_id")
        if not model_id:
            return []
        versions = self._fetch_model_versions(int(model_id))
        if versions:
            self._log("INFO", f"将按 {len(versions)} 个模型版本逐个抓取（官方 modelId 过滤对部分模型不生效）")
        return versions

    def _fetch_model_versions(self, model_id: int) -> list[int]:
        assert self._client is not None
        try:
            data = self._client.model(int(model_id))
        except AppError as exc:
            self._log("WARN", f"获取模型 {model_id} 的版本列表失败：{exc.friendly()}，尝试直接按 modelId 抓取")
            return []
        all_versions = data.get("modelVersions") or []
        versions = [int(v["id"]) for v in all_versions if v.get("id")]
        if len(versions) > 30:
            self._log("WARN", f"模型「{data.get('name')}」共有 {len(versions)} 个版本，只抓取最新的 30 个版本")
            versions = versions[:30]
        return versions

    def _run_versions(self, entry: str, versions: list[int], persist_state: bool = True,
                      finish: bool = True) -> None:
        """按模型版本逐个分页抓取。"""
        assert self._client is not None
        if persist_state:
            version_index = int(self._cursor_state.get("version_index") or 0)
            cursor = self._cursor_state.get("cursor")
        else:
            version_index, cursor = 0, None
        max_requests = max(60, int(self._limit * 0.3) + 30)
        page_no = 0
        for idx in range(version_index, len(versions)):
            version_id = versions[idx]
            if idx != version_index:
                cursor = None
            self._log("INFO", f"开始抓取模型版本 {version_id}（{idx + 1}/{len(versions)}）")
            while True:
                self._checkpoint()
                params = self._base_params()
                params["modelVersionId"] = version_id
                if cursor:
                    params["cursor"] = cursor
                data = self._client.images(**params)
                items = data.get("items") or []
                metadata = data.get("metadata") or {}
                page_no += 1
                self._counters["fetched"] += len(items)
                self._log("INFO", f"版本 {version_id} 第 {page_no} 页：返回 {len(items)} 条")
                stop_by_date = False
                stop_by_limit = False
                for item in items:
                    self._checkpoint()
                    result = self._handle_item(item)
                    if result == "stop_date":
                        stop_by_date = True
                        break
                    if result == "stop_limit":
                        stop_by_limit = True
                        break
                if stop_by_limit or self._counters["kept"] >= self._limit:
                    if finish:
                        self._finish_done(f"达到数量上限（{self._limit} 条）")
                    return
                if self._client.request_count >= max_requests:
                    if finish:
                        self._finish_done(f"达到请求上限（{max_requests} 次），已保留 {self._counters['kept']} 条")
                    return
                if stop_by_date:
                    if finish:
                        self._finish_done("已到达日期范围边界")
                    return
                next_cursor = metadata.get("nextCursor")
                if not next_cursor:
                    break
                if next_cursor == cursor:
                    break
                cursor = next_cursor
                if persist_state:
                    self._cursor_state = {"version_index": idx, "cursor": cursor}
                    self._save_progress()
            if persist_state:
                self._cursor_state = {"version_index": idx + 1, "cursor": None}
                self._save_progress()
        if finish:
            self._finish_done(f"没有更多数据（已遍历 {len(versions)} 个模型版本）")

    def _run_paginated(self, entry: str, target_index: int = 0) -> None:
        assert self._client is not None
        cursor = self._cursor_state.get("cursor")
        max_requests = max(60, int(self._limit * 0.3) + 30)
        page_no = 0
        while True:
            self._checkpoint()
            params = self._build_params(entry, cursor)
            data = self._client.images(**params)
            items = data.get("items") or []
            metadata = data.get("metadata") or {}
            page_no += 1
            self._counters["fetched"] += len(items)
            self._log("INFO", f"第 {page_no} 页：返回 {len(items)} 条")
            stop_by_date = False
            stop_by_limit = False
            for item in items:
                self._checkpoint()
                result = self._handle_item(item)
                if result == "stop_date":
                    stop_by_date = True
                    break
                if result == "stop_limit":
                    stop_by_limit = True
                    break
            if stop_by_limit or self._counters["kept"] >= self._limit:
                self._finish_done(f"达到数量上限（{self._limit} 条）")
                return
            if self._client.request_count >= max_requests:
                self._finish_done(f"达到请求上限（{max_requests} 次），已保留 {self._counters['kept']} 条")
                return
            if stop_by_date:
                self._finish_done("已到达日期范围边界")
                return
            next_cursor = metadata.get("nextCursor")
            if not next_cursor:
                self._finish_done("没有更多数据")
                return
            if next_cursor == cursor:
                self._finish_done("接口返回的 cursor 未变化，停止以避免死循环")
                return
            cursor = next_cursor
            self._cursor_state = {"target_index": target_index, "cursor": cursor}
            self._save_progress()

    # ---------------- 入口：链接列表 ----------------
    def _run_urls(self) -> None:
        assert self._client is not None
        urls = [u for u in (self._config.get("urls") or []) if str(u).strip()]
        if not urls:
            raise AppError("没有可解析的链接")
        start_index = int(self._cursor_state.get("target_index") or 0)
        for index in range(start_index, len(urls)):
            target = parse_target_url(urls[index])
            if not target:
                self._log("WARN", f"无法识别的链接，跳过：{urls[index]}")
                continue
            kind, value = target
            self._checkpoint()
            self._log("INFO", f"处理链接（{kind}）：{urls[index]}")
            if kind == "image":
                self._fetch_single_image(int(value))
                if self._counters["kept"] >= self._limit:
                    break
                continue
            if kind == "model":
                versions = self._fetch_model_versions(int(value))
                if versions:
                    self._run_versions("model", versions, persist_state=False, finish=False)
                    if self._counters["kept"] >= self._limit:
                        self._finish_done(f"达到数量上限（{self._limit} 条）")
                        return
                    self._cursor_state = {"target_index": index + 1, "cursor": None}
                    self._save_progress()
                    continue
            query = url_query_value(kind, value)
            if not query:
                self._log("WARN", f"暂不支持的链接类型，跳过：{urls[index]}")
                continue
            cursor = None
            if start_index == index and self._cursor_state.get("cursor"):
                cursor = self._cursor_state["cursor"]
            max_requests = max(50, int(self._limit * 0.25) + 30)
            while True:
                self._checkpoint()
                params: dict[str, Any] = {
                    "limit": PAGE_SIZE, "withMeta": "true", "sort": "Newest",
                    "nsfw": "None" if self._config.get("content_level") == "sfw" else "X",
                    query[0]: query[1],
                }
                if cursor:
                    params["cursor"] = cursor
                data = self._client.images(**params)
                items = data.get("items") or []
                self._counters["fetched"] += len(items)
                for item in items:
                    self._checkpoint()
                    if self._handle_item(item) in ("stop_date", "stop_limit"):
                        break
                if self._counters["kept"] >= self._limit:
                    break
                metadata = data.get("metadata") or {}
                cursor = metadata.get("nextCursor")
                if not cursor:
                    break
                if self._client.request_count >= max_requests:
                    break
                self._cursor_state = {"target_index": index, "cursor": cursor}
                self._save_progress()
            if self._counters["kept"] >= self._limit:
                self._finish_done(f"达到数量上限（{self._limit} 条）")
                return
            self._cursor_state = {"target_index": index + 1, "cursor": None}
            self._save_progress()

    def _fetch_single_image(self, image_id: int) -> None:
        assert self._client is not None
        data = self._client.images(imageId=image_id, withMeta="true")
        items = data.get("items") or []
        self._counters["fetched"] += len(items)
        if not items:
            self._counters["skipped_no_meta"] += 1
            self._log("WARN", f"图片 {image_id} 查不到数据（可能是成熟内容且未填 API Key）")
            return
        self._handle_item(items[0])

    # ---------------- 入口：站内搜索（实验） ----------------
    def _run_site_search(self) -> None:
        assert self._client is not None
        keyword = str(self._config.get("keyword") or "").strip()
        if not keyword:
            raise AppError("站内搜索需要填写关键词")
        self._log("WARN", "实验功能：站内搜索服务不是 civitai 官方公开契约，可能随时失效")
        hits = search_images(
            keyword,
            limit=max(self._limit * 3, 100),
            token=self.settings.site_search_token,
            nsfw_mask=31 if self._config.get("content_level") != "sfw" else 1,
            timeout=self.settings.timeout,
        )
        self._log("INFO", f"站内搜索命中 {len(hits)} 条，开始逐条补全元数据（较慢，请耐心等待）")
        tools = set(self._config.get("tool_filter") or [])
        for hit in hits:
            self._checkpoint()
            if self._counters["kept"] >= self._limit:
                break
            image_id = hit_image_id(hit)
            if not image_id:
                continue
            tag_names = " ".join(hit.get("tagNames") or []) if isinstance(hit.get("tagNames"), list) else ""
            if tag_names:
                lowered = tag_names.lower()
                if any(tag.lower() not in lowered for tag in (self._config.get("tag_filter") or [])):
                    self._counters["skipped_tags"] += 1
                    continue
            tool_names = hit.get("toolNames") or []
            if tools and tool_names and not any(str(t) in tools for t in tool_names):
                self._counters["skipped_tool"] += 1
                continue
            self._fetch_single_image(image_id)
        self._finish_done("站内搜索完成")

    # ---------------- 单条处理 ----------------
    def _handle_item(self, item: dict[str, Any]) -> str:
        cfg = self._config
        if self._counters["kept"] >= self._limit:
            return "stop_limit"
        meta = normalize_meta(item.get("meta"))
        prompt = str(meta.get("prompt") or "")
        if not meta or not prompt.strip():
            self._counters["skipped_no_meta"] += 1
            if self._counters["skipped_no_meta"] % 50 == 1:
                self._log("INFO", f"已跳过 {self._counters['skipped_no_meta']} 条没有提示词的数据")
            return "ok"

        level = str(item.get("nsfwLevel") or "")
        content_level = cfg.get("content_level") or "all"
        if content_level == "sfw" and level != "None":
            return "ok"
        if content_level == "nsfw" and level not in LEVEL_NSFW:
            return "ok"

        # 日期范围（仅“最新/最旧”排序下精确生效）
        sort = str(cfg.get("sort") or "Newest")
        start = _parse_date(str(cfg.get("date_start") or ""))
        end = _parse_date(str(cfg.get("date_end") or ""))
        if sort in ("Newest", "Oldest") and (start or end):
            created = _item_date(item)
            if created:
                if sort == "Newest":  # 从新到旧：比开始日期更旧 → 结束；比结束日期更新 → 跳过
                    if start and created < start:
                        return "stop_date"
                    if end and created > end:
                        return "ok"
                else:  # 从旧到新：比结束日期更新 → 结束；比开始日期更旧 → 跳过
                    if end and created > end:
                        return "stop_date"
                    if start and created < start:
                        return "ok"

        record = build_record(item, str(cfg.get("site") or self.settings.site),
                              self._resolver.get if self._resolver else None)

        tool_filter = set(cfg.get("tool_filter") or [])
        if tool_filter and record["tool"] not in tool_filter:
            self._counters["skipped_tool"] += 1
            return "ok"

        tag_filter = [t.strip().lower() for t in (cfg.get("tag_filter") or []) if str(t).strip()]
        if tag_filter:
            text = (record["tags"] or "").lower()
            if any(tag not in text for tag in tag_filter):
                self._counters["skipped_tags"] += 1
                return "ok"

        record["task_id"] = self.task_id
        result = self.store.add_item(record)
        if result == "inserted":
            self._counters["kept"] += 1
            if self._counters["kept"] % 25 == 0:
                self._log("INFO", f"已保留 {self._counters['kept']} 条")
                self._save_progress()
        else:
            self._counters["skipped_dup"] += 1
        return "ok"

    def _finish_done(self, message: str) -> None:
        self._finished = True
        self._save_progress(status="done", message=message)
        self._log("INFO", f"任务结束：{message}")
        self._log("INFO", f"统计：保留 {self._counters['kept']}；抓取 {self._counters['fetched']}；"
                          f"无提示词 {self._counters['skipped_no_meta']}；工具过滤 {self._counters['skipped_tool']}；"
                          f"标签过滤 {self._counters['skipped_tags']}；重复 {self._counters['skipped_dup']}")
        self._emit("status", status="done", message=message)

    def _checkpoint(self) -> None:
        if self.cancel_event.is_set():
            raise CancelledError()
        if self.pause_event.is_set():
            self._log("INFO", "任务已暂停，点击「继续」恢复")
            self._save_progress(status="paused", message="已暂停")
            self._emit("status", status="paused", message="已暂停")
            while self.pause_event.is_set():
                if self.cancel_event.is_set():
                    raise CancelledError()
                time.sleep(0.2)
            self.store.update_task(self.task_id, status="running", message="")
            self._emit("status", status="running", message="已继续")
            self._log("INFO", "任务继续执行")


def build_task_name(config: dict[str, Any]) -> str:
    """给任务起个可读的名字。"""
    entry = config.get("entry")
    if entry in ("model", "lora", "keyword"):
        base = config.get("model_name") or f"模型 {config.get('model_id') or config.get('model_version_id')}"
    elif entry == "author":
        base = f"作者 {config.get('username')}"
    elif entry == "ranking":
        base = f"榜单 {config.get('sort')}"
    elif entry == "urls":
        count = len(config.get("urls") or [])
        base = f"链接列表（{count} 个）"
    elif entry == "site_search":
        base = f"站内搜索「{config.get('keyword')}」"
    else:
        base = str(entry)
    return f"{base} · {config.get('limit')} 条"
