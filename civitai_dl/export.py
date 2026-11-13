"""导出：CSV / JSONL / TXT / XLSX。"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .config import COLUMNS
from .parser import strip_angle_tags
from .store import Store

EXPORT_LABELS = {key: english for key, _cn, english, _desc in COLUMNS}
EXCEL_CELL_LIMIT = 32000
FORMAT_LABELS = [("csv", "CSV（Excel 可直接打开）"), ("jsonl", "JSON Lines"), ("txt", "TXT（仅提示词，每行一条）"),
                 ("xlsx", "Excel .xlsx")]


def _value(row: dict[str, Any], key: str) -> Any:
    if key == "rawJson":
        return row.get("raw_json") or ""
    if key == "prompt":
        return strip_angle_tags(row.get("prompt") or "")
    if key == "negativePrompt":
        return row.get("negative_prompt") or ""
    if key == "imageId":
        return row.get("image_id")
    if key == "pageUrl":
        return row.get("page_url") or ""
    if key == "baseModel":
        return row.get("base_model") or ""
    if key == "nsfwLevel":
        return row.get("nsfw_level") or ""
    if key == "createdAt":
        return row.get("created_at") or ""
    return row.get(key) or ""


def _rows(store: Store, task_id: int | None, columns: list[str], keyword: str = "",
          image_ids: list[int] | None = None) -> list[dict[str, Any]]:
    rows = list(store.iter_items(task_id=task_id))
    if image_ids is not None:
        wanted = set(image_ids)
        rows = [r for r in rows if int(r.get("image_id") or 0) in wanted]
    if keyword:
        needle = keyword.lower()
        rows = [r for r in rows if any(needle in str(r.get(f) or "").lower() for f in ("prompt", "negative_prompt", "lora", "checkpoint", "tags"))]
    return rows


def _split_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("nsfw_level") or "Unknown")].append(row)
    return dict(groups)


def _dedupe_columns(columns: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for key in columns:
        if key in EXPORT_LABELS and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> int:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        writer.writerow([EXPORT_LABELS[c] for c in columns])
        for row in rows:
            writer.writerow([_value(row, c) for c in columns])
    return len(rows)


def _json_line(row: dict[str, Any], columns: list[str]) -> str:
    payload: dict[str, Any] = {}
    raw = row.get("raw_json") or ""
    if "rawJson" in columns and raw:
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
    for key in columns:
        if key == "rawJson":
            continue
        payload[EXPORT_LABELS[key]] = _value(row, key)
    return json.dumps(payload, ensure_ascii=False)


def _write_jsonl(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> int:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(_json_line(row, columns) + "\n")
    return len(rows)


def _write_txt(path: Path, rows: list[dict[str, Any]]) -> int:
    written = 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            prompt = strip_angle_tags(str(row.get("prompt") or "")).strip()
            if not prompt:
                continue
            fh.write(prompt.replace("\r\n", "\n").replace("\r", "\n") + "\n")
            written += 1
    return written


def _write_xlsx(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> int:
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("prompts")
    sheet.append([EXPORT_LABELS[c] for c in columns])
    for row in rows:
        values = []
        for key in columns:
            value = _value(row, key)
            if isinstance(value, str):
                value = ILLEGAL_CHARACTERS_RE.sub("", value)
                if len(value) > EXCEL_CELL_LIMIT:
                    value = value[:EXCEL_CELL_LIMIT] + "…[截断]"
            values.append(value)
        sheet.append(values)
    workbook.save(str(path))
    return len(rows)


def export_items(
    store: Store,
    path: str | Path,
    fmt: str = "csv",
    columns: list[str] | None = None,
    task_id: int | None = None,
    nsfw_split: bool = False,
    keyword: str = "",
    image_ids: list[int] | None = None,
) -> tuple[int, list[Path]]:
    """导出结果，返回 (写入条数, 生成的文件列表)。"""
    path = Path(path)
    columns = _dedupe_columns(columns or [])
    if not columns:
        raise ValueError("至少选择一个导出字段")
    rows = _rows(store, task_id, columns, keyword=keyword, image_ids=image_ids)
    if not rows:
        raise ValueError("没有可导出的数据")

    files: list[Path] = []
    if nsfw_split:
        groups = _split_rows(rows)
        written = 0
        for level, group in groups.items():
            target = path.with_name(f"{path.stem}_{level}{path.suffix}")
            written += _export_group(target, group, columns, fmt)
            files.append(target)
        return written, files
    written = _export_group(path, rows, columns, fmt)
    return written, [path]


def _export_group(path: Path, rows: list[dict[str, Any]], columns: list[str], fmt: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        return _write_csv(path, rows, columns)
    if fmt == "jsonl":
        return _write_jsonl(path, rows, columns)
    if fmt == "txt":
        return _write_txt(path, rows)
    if fmt == "xlsx":
        return _write_xlsx(path, rows, columns)
    raise ValueError(f"不支持的导出格式：{fmt}")
