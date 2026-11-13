"""核心模块单元测试（标准库 unittest，无需 pytest）。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from civitai_dl.config import COLUMN_KEYS, DEFAULT_PREVIEW_COLUMNS, Settings
from civitai_dl.export import export_items
from civitai_dl.parser import (
    build_record,
    classify_tool,
    parse_target_url,
    prompt_tags,
    tags_text,
)
from civitai_dl.store import Store

A1111_ITEM = {
    "id": 1001,
    "url": "https://image.civitai.com/a/original=true/a.jpeg",
    "nsfwLevel": "Soft",
    "baseModel": "SDXL 1.0",
    "username": "tester",
    "modelVersionIds": [5001],
    "createdAt": "2025-03-04T05:06:07.000Z",
    "meta": {
        "prompt": "masterpiece, (best quality:1.2), 1girl, <lora:detail:0.8>",
        "negativePrompt": "bad hands",
        "Model": "ckpt_v1",
        "sampler": "Euler a",
        "steps": 20,
        "resources": [
            {"type": "lora", "name": "detail", "weight": 0.8},
            {"type": "checkpoint", "name": "ckpt_v1", "weight": 1},
        ],
    },
}

ONSITE_ITEM = {
    "id": 1002,
    "nsfwLevel": "None",
    "baseModel": "Illustrious",
    "username": "tester",
    "modelVersionIds": [5002],
    "createdAt": "2025-03-05T05:06:07.000Z",
    "meta": {
        "prompt": "a cat, cinematic",
        "Model": "urn:air:sdxl:checkpoint:civitai:123@456",
        "engine": "krea",
        "ecosystem": "Illustrious",
    },
}


class ParserTest(unittest.TestCase):
    def test_tool_classification(self) -> None:
        self.assertEqual(classify_tool(A1111_ITEM["meta"]), "WebUI")
        self.assertEqual(classify_tool(ONSITE_ITEM["meta"]), "站内生成器")
        self.assertEqual(classify_tool(None), "无参数")
        self.assertEqual(classify_tool({}), "无参数")

    def test_prompt_tags(self) -> None:
        tags = prompt_tags("masterpiece, (best quality:1.2), 1girl, <lora:detail:0.8>, 1girl")
        self.assertEqual(tags, ["masterpiece", "best quality", "1girl"])
        self.assertEqual(tags_text("a, b, a"), "a, b")

    def test_build_record(self) -> None:
        record = build_record(A1111_ITEM, "civitai.red")
        self.assertEqual(record["prompt"], A1111_ITEM["meta"]["prompt"])
        self.assertEqual(record["negative_prompt"], "bad hands")
        self.assertEqual(record["tool"], "WebUI")
        self.assertEqual(record["checkpoint"], "ckpt_v1")
        self.assertIn("detail:0.8", record["lora"])
        self.assertEqual(record["page_url"], "https://civitai.red/images/1001")
        self.assertTrue(record["prompt_hash"])
        # 原始 JSON 可还原
        self.assertEqual(json.loads(record["raw_json"])["id"], 1001)

    def test_nested_meta(self) -> None:
        item = {"id": 1, "nsfwLevel": "None", "meta": {"id": 1, "meta": {"prompt": "hello, world"}}}
        record = build_record(item, "civitai.com")
        self.assertEqual(record["prompt"], "hello, world")
        self.assertEqual(record["tags"], "hello, world")

    def test_url_parsing(self) -> None:
        cases = {
            "https://civitai.com/images/123": ("image", 123),
            "https://civitai.red/models/456/name": ("model", 456),
            "https://civitai.com/posts/789": ("post", 789),
            "https://civitai.com/user/someone": ("user", "someone"),
            "999": ("image", 999),
        }
        for text, expected in cases.items():
            self.assertEqual(parse_target_url(text), expected, text)
        self.assertIsNone(parse_target_url("https://example.com/abc"))


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.task_id = self.store.add_task({"entry": "ranking"}, "任务")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _record(self, image_id: int, prompt: str = "a, b") -> dict:
        item = dict(A1111_ITEM)
        item["id"] = image_id
        item["meta"] = dict(A1111_ITEM["meta"])
        item["meta"]["prompt"] = prompt
        record = build_record(item, "civitai.red")
        record["task_id"] = self.task_id
        return record

    def test_dedupe_and_counts(self) -> None:
        self.assertEqual(self.store.add_item(self._record(1)), "inserted")
        self.assertEqual(self.store.add_item(self._record(1)), "dup_image")
        self.assertEqual(self.store.add_item(self._record(2, "a, b")), "dup_prompt")
        self.assertEqual(self.store.add_item(self._record(3, "c, d")), "inserted")
        self.assertEqual(self.store.count_items(self.task_id), 2)

    def test_resume_fields(self) -> None:
        self.store.update_task(self.task_id, cursor='{"cursor": "abc"}', kept=5, fetched=20)
        task = self.store.get_task(self.task_id)
        self.assertEqual(task["kept"], 5)
        self.assertEqual(task["fetched"], 20)
        self.assertIn("abc", task["cursor"])


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "t.db")
        self.task_id = self.store.add_task({"entry": "ranking"}, "任务")
        for image_id, level in ((11, "None"), (12, "X")):
            item = dict(A1111_ITEM)
            item["id"] = image_id
            item["nsfwLevel"] = level
            item["meta"] = dict(A1111_ITEM["meta"])
            item["meta"]["prompt"] = f"prompt {image_id}, test"
            record = build_record(item, "civitai.red")
            record["task_id"] = self.task_id
            self.store.add_item(record)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_formats(self) -> None:
        cols = ["imageId", "prompt", "negativePrompt", "lora", "checkpoint", "tool", "tags", "rawJson"]
        for fmt, ext in (("csv", ".csv"), ("jsonl", ".jsonl"), ("txt", ".txt"), ("xlsx", ".xlsx")):
            path = self.root / f"out{ext}"
            count, files = export_items(self.store, path, fmt=fmt, columns=cols, task_id=self.task_id)
            self.assertEqual(count, 2, fmt)
            self.assertTrue(Path(files[0]).exists())
        csv_text = (self.root / "out.csv").read_text(encoding="utf-8-sig")
        self.assertTrue(csv_text.startswith("imageId,positivePrompt"))
        txt_lines = (self.root / "out.txt").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(txt_lines), 2)

    def test_nsfw_split(self) -> None:
        count, files = export_items(self.store, self.root / "split.csv", fmt="csv",
                                    columns=["prompt"], task_id=self.task_id, nsfw_split=True)
        self.assertEqual(count, 2)
        self.assertEqual(len(files), 2)
        self.assertEqual(sorted(p.name for p in files), ["split_None.csv", "split_X.csv"])

    def test_empty_columns_raise(self) -> None:
        with self.assertRaises(ValueError):
            export_items(self.store, self.root / "empty.csv", fmt="csv", columns=[], task_id=self.task_id)


class SettingsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        settings = Settings()
        self.assertIn(settings.site, ("civitai.red", "civitai.com"))
        self.assertIn(settings.content_level, ("all", "sfw", "nsfw"))
        self.assertTrue(1 <= settings.default_limit <= 100000)
        self.assertTrue(all(column in COLUMN_KEYS for column in settings.preview_columns))
        self.assertTrue(set(DEFAULT_PREVIEW_COLUMNS) <= set(COLUMN_KEYS))

    def test_sanitize(self) -> None:
        settings = Settings(site="bogus", content_level="bogus", rate_interval=-5,
                            preview_columns=["nope"])
        settings._sanitize()
        self.assertEqual(settings.site, "civitai.red")
        self.assertEqual(settings.content_level, "all")
        self.assertGreaterEqual(settings.rate_interval, 0.2)
        self.assertTrue(settings.preview_columns)


if __name__ == "__main__":
    unittest.main()
