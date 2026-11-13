"""自检：不打开界面，验证核心模块与导出功能。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from . import __version__


def _sample_images() -> list[dict]:
    return [
        {
            "id": 111,
            "url": "https://image.civitai.com/x/y/original=true/y.jpeg",
            "nsfwLevel": "Soft",
            "baseModel": "SDXL 1.0",
            "username": "tester",
            "modelVersionIds": [9001],
            "createdAt": "2025-01-02T03:04:05.000Z",
            "meta": {
                "prompt": "masterpiece, (best quality:1.2), 1girl, <lora:detail:0.8>, <lora:style2:0.5>",
                "negativePrompt": "low quality, blurry",
                "Model": "myCheckpoint_v12",
                "sampler": "Euler a",
                "steps": 28,
                "cfgScale": 7,
                "seed": 123,
                "width": 1024,
                "height": 1024,
                "resources": [
                    {"type": "lora", "name": "detail", "weight": 0.8},
                    {"type": "checkpoint", "name": "myCheckpoint_v12", "weight": 1},
                ],
                "civitaiResources": [{"type": "lora", "modelVersionId": 9002, "weight": 0.5}],
            },
        },
        {
            "id": 222,
            "url": "https://image.civitai.com/x/z/original=true/z.jpeg",
            "nsfwLevel": "None",
            "baseModel": "Illustrious",
            "username": "tester2",
            "modelVersionIds": [9100],
            "createdAt": "2025-02-03T04:05:06.000Z",
            "meta": {
                "prompt": "photo of a cat, cinematic",
                "Model": "urn:air:sdxl:checkpoint:civitai:1224788@2864868",
                "engine": "krea",
                "ecosystem": "Illustrious",
                "civitaiResources": [{"type": "checkpoint", "modelVersionId": 2864868, "weight": 1}],
            },
        },
        {"id": 333, "url": "", "nsfwLevel": "None", "baseModel": "Flux", "meta": None, "modelVersionIds": []},
    ]


def run_selftest(live: bool = False) -> int:
    from .config import Settings
    from .export import export_items
    from .parser import build_record, classify_tool, parse_target_url, prompt_tags
    from .store import Store

    lines: list[str] = []
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        lines.append(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    lines.append(f"Civitai 提示词批量下载器 v{__version__} 自检")

    # 1. 解析
    images = _sample_images()
    a1111 = build_record(images[0], "civitai.red")
    onsite = build_record(images[1], "civitai.red")
    empty = images[2]
    check("工具识别 WebUI", classify_tool(images[0]["meta"]) == "WebUI", classify_tool(images[0]["meta"]))
    check("工具识别 站内生成器", classify_tool(images[1]["meta"]) == "站内生成器", classify_tool(images[1]["meta"]))
    check("工具识别 无参数", classify_tool(None) == "无参数")
    check("正向提示词", a1111["prompt"].startswith("masterpiece"))
    check("负向提示词", a1111["negative_prompt"] == "low quality, blurry")
    check("LoRA 拆列", "detail:0.8" in a1111["lora"] and "style2:0.5" in a1111["lora"], a1111["lora"])
    check("Checkpoint 名称", a1111["checkpoint"] == "myCheckpoint_v12", a1111["checkpoint"])
    check("tags 拆分", "detail" not in a1111["tags"] and "best quality" in a1111["tags"], a1111["tags"])
    check("URN checkpoint 解析", onsite["checkpoint"] not in ("", None) and not onsite["checkpoint"].startswith("urn:"),
          onsite["checkpoint"])
    check("无 meta 记录可生成", build_record(empty, "civitai.red")["prompt"] == "")

    # 2. URL 解析
    check("图片链接", parse_target_url("https://civitai.com/images/123456") == ("image", 123456))
    check("模型链接", parse_target_url("https://civitai.red/models/128568/cyberpunk") == ("model", 128568))
    check("帖子链接", parse_target_url("https://civitai.com/posts/32375445") == ("post", 32375445))
    check("用户链接", parse_target_url("https://civitai.com/user/annawilliams") == ("user", "annawilliams"))
    check("纯数字按图片", parse_target_url("123456") == ("image", 123456))

    # 3. 存储 + 导出
    with tempfile.TemporaryDirectory(prefix="civitai_dl_selftest_") as tmp:
        store = Store(Path(tmp) / "test.db")
        config = {"entry": "ranking", "site": "civitai.red", "content_level": "all", "sort": "Newest",
                  "limit": 10, "tool_filter": [], "tag_filter": [], "resolve_model_names": False}
        task_id = store.add_task(config, "自检任务")
        rec_a = dict(a1111)
        rec_a["task_id"] = task_id
        rec_b = dict(onsite)
        rec_b["task_id"] = task_id
        rec_a2 = dict(a1111)
        rec_a2["image_id"] = 444
        rec_a2["task_id"] = task_id
        check("入库成功", store.add_item(rec_a) == "inserted")
        check("图片去重", store.add_item(rec_a) == "dup_image")
        check("提示词去重", store.add_item(rec_a2) == "dup_prompt")
        check("第二条入库", store.add_item(rec_b) == "inserted")
        check("条数正确", store.count_items(task_id) == 2, str(store.count_items(task_id)))
        store.update_task(task_id, status="done", kept=2)

        cols = ["imageId", "prompt", "negativePrompt", "lora", "checkpoint", "tool", "tags", "rawJson"]
        for fmt, ext in (("csv", ".csv"), ("jsonl", ".jsonl"), ("txt", ".txt"), ("xlsx", ".xlsx")):
            out = Path(tmp) / f"out{ext}"
            count, files = export_items(store, out, fmt=fmt, columns=cols, task_id=task_id)
            check(f"导出 {fmt}", count == 2 and files and Path(files[0]).exists(), str(files))
        csv_text = (Path(tmp) / "out.csv").read_text(encoding="utf-8-sig")
        check("CSV 带 BOM/表头", csv_text.startswith("imageId,positivePrompt") and "myCheckpoint_v12" in csv_text)
        txt_text = (Path(tmp) / "out.txt").read_text(encoding="utf-8")
        txt_lines = txt_text.strip().splitlines()
        check("TXT 无表头且每行一条", len(txt_lines) == 2 and any(line.startswith("masterpiece") for line in txt_lines),
              str(txt_lines)[:120])
        split_count, split_files = export_items(store, Path(tmp) / "split.csv", fmt="csv", columns=cols,
                                                task_id=task_id, nsfw_split=True)
        check("按分级拆分", split_count == 2 and len(split_files) == 2, str([Path(f).name for f in split_files]))
        store.close()

    # 4. 界面模块可导入（不创建窗口）
    try:
        import civitai_dl.ui.app  # noqa: F401
        import civitai_dl.ui.dialogs  # noqa: F401

        check("界面模块导入", True)
    except Exception as exc:  # noqa: BLE001
        check("界面模块导入", False, repr(exc))

    try:
        import pystray  # noqa: F401
        from PIL import Image, ImageDraw  # noqa: F401

        check("托盘模块导入", True)
    except Exception as exc:  # noqa: BLE001
        check("托盘模块导入", False, repr(exc))

    # 5. 真实网络（可选）
    if live:
        try:
            from .api import CivitaiClient

            settings = Settings()
            settings.site = "civitai.red"
            client = CivitaiClient(settings)
            data = client.images(limit=2, withMeta="true", sort="Newest", nsfw="X")
            items = data.get("items") or []
            check("真实 API 请求", bool(items), f"{len(items)} 条；cursor={bool((data.get('metadata') or {}).get('nextCursor'))}")
            if items:
                rec = build_record(items[0], "civitai.red")
                check("真实数据解析", bool(rec["tool"]), f"tool={rec['tool']} level={rec['nsfw_level']}")
        except Exception as exc:  # noqa: BLE001
            check("真实 API 请求", False, repr(exc))

    report = "\n".join(lines)
    print(report)
    try:
        from .paths import app_data_dir

        (app_data_dir() / "selftest_result.txt").write_text(report, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if failures:
        print("\n失败项：" + "、".join(failures))
    return 0 if not failures else 1
