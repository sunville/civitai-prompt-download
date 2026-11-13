"""配置与常量。"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .paths import app_data_dir, default_output_dir

CONFIG_FILE = "config.json"

# 站点
SITES = [("civitai.red", "civitai.red（全部内容）"), ("civitai.com", "civitai.com（SFW）")]

# 内容级别
CONTENT_LEVELS = [("all", "全部（SFW + NSFW）"), ("sfw", "只要 SFW"), ("nsfw", "只要 NSFW")]

# 排序 / 周期
SORTS = [
    ("Newest", "最新"),
    ("Oldest", "最旧"),
    ("Most Reactions", "最热"),
    ("Most Comments", "最多评论"),
    ("Most Collected", "最多收藏"),
]
PERIODS = [("Day", "最近一天"), ("Week", "最近一周"), ("Month", "最近一月"), ("Year", "最近一年"), ("AllTime", "全部时间")]

# 生成工具分类
TOOLS = ["WebUI", "ComfyUI", "站内生成器", "SD.Next", "其他", "无参数"]

# 采集入口
ENTRY_KINDS = [
    ("model", "按模型 / Checkpoint"),
    ("lora", "按 LoRA"),
    ("author", "按作者"),
    ("ranking", "按榜单"),
    ("keyword", "关键词（搜模型）"),
    ("urls", "图片 / 页面链接列表"),
    ("site_search", "站内搜索图片（实验）"),
]

# 字段定义：(key, 预览列名, 导出列名, 说明)
COLUMNS: list[tuple[str, str, str, str]] = [
    ("imageId", "图片ID", "imageId", "图片唯一 ID"),
    ("pageUrl", "页面链接", "pageUrl", "civitai 图片页链接"),
    ("prompt", "正向提示词", "positivePrompt", "prompt"),
    ("negativePrompt", "负向提示词", "negativePrompt", "negative prompt"),
    ("lora", "LoRA", "lora", "LoRA 名称与权重"),
    ("checkpoint", "Checkpoint", "checkpoint", "Checkpoint 名称"),
    ("tool", "生成工具", "tool", "WebUI / ComfyUI / 站内生成器 等"),
    ("tags", "Tags", "tags", "从 prompt 拆出的 tag（逗号分隔）"),
    ("baseModel", "基础模型", "baseModel", "civitai baseModel"),
    ("modelId", "模型ID", "modelId", "checkpoint/lora 主模型 ID"),
    ("modelVersionId", "模型版本ID", "modelVersionId", "主模型版本 ID"),
    ("nsfwLevel", "NSFW分级", "nsfwLevel", "None / Soft / Mature / X（默认不导出）"),
    ("username", "作者", "username", "图片作者"),
    ("createdAt", "发布时间", "createdAt", "图片发布时间"),
    ("rawJson", "原始JSON", "rawJSON", "接口返回的整包 JSON（体积大，默认不导出）"),
]
COLUMN_KEYS = [c[0] for c in COLUMNS]

DEFAULT_PREVIEW_COLUMNS = ["prompt", "negativePrompt", "lora", "checkpoint", "tool", "tags"]
DEFAULT_EXPORT_COLUMNS = [
    "imageId", "pageUrl", "prompt", "negativePrompt", "lora", "checkpoint",
    "baseModel", "modelId", "modelVersionId", "tool", "tags",
]


def _default(obj: Any) -> Any:
    if isinstance(obj, list):
        return list(obj)
    return obj


@dataclass
class Settings:
    site: str = "civitai.red"
    content_level: str = "all"
    api_key: str = ""
    proxy_mode: str = "system"          # system / direct / manual
    proxy_url: str = ""
    rate_interval: float = 1.0          # 秒 / 请求
    timeout: int = 60
    retries: int = 4
    default_limit: int = 1000
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    resolve_model_names: bool = True
    download_thumbnails: bool = False   # 预留，当前版本不下载图片
    tool_filter: list[str] = field(default_factory=lambda: list(TOOLS))
    log_keep_days: int = 14
    site_search_token: str = ""         # 高级：站内搜索服务 token（留空用内置公开 token）
    minimize_to_tray: bool = True
    last_export_format: str = "csv"
    preview_columns: list[str] = field(default_factory=lambda: list(DEFAULT_PREVIEW_COLUMNS))

    # ---------- 持久化 ----------
    @classmethod
    def load(cls) -> "Settings":
        path = app_data_dir() / CONFIG_FILE
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        obj = cls()
        for f in fields(cls):
            if f.name in data and data[f.name] is not None:
                setattr(obj, f.name, data[f.name])
        obj._sanitize()
        return obj

    def _sanitize(self) -> None:
        if self.site not in dict(SITES):
            self.site = "civitai.red"
        if self.content_level not in dict(CONTENT_LEVELS):
            self.content_level = "all"
        if self.proxy_mode not in ("system", "direct", "manual"):
            self.proxy_mode = "system"
        try:
            self.rate_interval = min(10.0, max(0.2, float(self.rate_interval)))
        except (TypeError, ValueError):
            self.rate_interval = 1.0
        try:
            self.timeout = min(300, max(10, int(self.timeout)))
            self.retries = min(10, max(0, int(self.retries)))
            self.default_limit = min(100000, max(1, int(self.default_limit)))
        except (TypeError, ValueError):
            self.timeout, self.retries, self.default_limit = 60, 4, 1000
        self.tool_filter = [t for t in self.tool_filter if t in TOOLS] or list(TOOLS)
        self.preview_columns = [c for c in self.preview_columns if c in COLUMN_KEYS] or list(DEFAULT_PREVIEW_COLUMNS)

    def save(self) -> None:
        path = app_data_dir() / CONFIG_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def setup_logging() -> logging.Logger:
    from .paths import log_dir

    logger = logging.getLogger("civitai_dl")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_dir() / "app.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger
