"""导出文件名生成：checkpoint 名称 + 排序条件 + 周期/日期范围。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .config import PERIODS, SORTS

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s+")


def sanitize_filename(name: str, max_len: int = 90) -> str:
    """去掉 Windows 文件名非法字符，限制长度。"""
    name = _INVALID_CHARS.sub("_", name or "")
    name = _MULTI_SPACE.sub(" ", name).strip(" ._")
    name = name[:max_len].rstrip(" ._")
    return name or "未命名"


def _entry_base(config: dict[str, Any]) -> str:
    entry = config.get("entry")
    if config.get("model_name"):
        return str(config["model_name"])
    if entry == "author" and config.get("username"):
        return f"作者 {config['username']}"
    if entry == "ranking":
        return "榜单"
    if entry == "urls":
        return "链接列表"
    if entry == "site_search" and config.get("keyword"):
        return f"搜索 {config['keyword']}"
    if entry == "keyword" and config.get("model_name"):
        return str(config["model_name"])
    if config.get("model_id"):
        return f"模型 {config['model_id']}"
    return "结果"


def build_export_filename(config: dict[str, Any] | None, ext: str) -> str:
    """生成默认导出文件名（含扩展名）。

    规则：checkpoint / 入口名称 + 排序条件 + 周期或日期范围；
    有日期范围时不再写周期。
    """
    ext = ext if ext.startswith(".") else "." + ext
    if not config:
        return f"全部结果_{datetime.now():%Y%m%d_%H%M%S}{ext}"
    parts = [_entry_base(config)]
    sort_label = dict(SORTS).get(str(config.get("sort") or "Newest"), "最新")
    parts.append(sort_label)
    start = str(config.get("date_start") or "").strip()
    end = str(config.get("date_end") or "").strip()
    if start or end:
        parts.append(f"{start or '不限'}至{end or '不限'}")
    else:
        period_label = dict(PERIODS).get(str(config.get("period") or "AllTime"), "全部时间")
        parts.append(period_label)
    name = sanitize_filename("_".join(part for part in parts if part))
    return name + ext
