"""SQLite 存储：任务、结果、模型名称缓存。"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ITEM_FIELDS = [
    "image_id", "page_url", "image_url", "prompt", "negative_prompt", "tags", "lora",
    "checkpoint", "tool", "base_model", "model_id", "model_version_id", "nsfw_level",
    "username", "created_at", "raw_json", "prompt_hash", "image_type",
]

TASK_FIELDS = [
    "name", "config_json", "status", "cursor", "kept", "fetched", "skipped_no_meta",
    "skipped_tool", "skipped_tags", "skipped_date", "skipped_dup", "requests",
    "message", "created_at", "updated_at",
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    # ---------------- 基础 ----------------
    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    cursor TEXT,
                    kept INTEGER NOT NULL DEFAULT 0,
                    fetched INTEGER NOT NULL DEFAULT 0,
                    skipped_no_meta INTEGER NOT NULL DEFAULT 0,
                    skipped_tool INTEGER NOT NULL DEFAULT 0,
                    skipped_tags INTEGER NOT NULL DEFAULT 0,
                    skipped_date INTEGER NOT NULL DEFAULT 0,
                    skipped_dup INTEGER NOT NULL DEFAULT 0,
                    requests INTEGER NOT NULL DEFAULT 0,
                    message TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS items (
                    image_id INTEGER PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    page_url TEXT,
                    image_url TEXT,
                    prompt TEXT,
                    negative_prompt TEXT,
                    tags TEXT,
                    lora TEXT,
                    checkpoint TEXT,
                    tool TEXT,
                    base_model TEXT,
                    model_id INTEGER,
                    model_version_id INTEGER,
                    nsfw_level TEXT,
                    username TEXT,
                    created_at TEXT,
                    raw_json TEXT,
                    prompt_hash TEXT,
                    image_type TEXT,
                    inserted_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_items_task_prompt
                    ON items(task_id, prompt_hash) WHERE prompt_hash IS NOT NULL AND prompt_hash != '';
                CREATE INDEX IF NOT EXISTS idx_items_task ON items(task_id);
                CREATE TABLE IF NOT EXISTS model_cache (
                    version_id INTEGER PRIMARY KEY,
                    model_id INTEGER,
                    name TEXT,
                    type TEXT,
                    base_model TEXT,
                    fetched_at TEXT
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- 任务 ----------------
    def add_task(self, config: dict[str, Any], name: str) -> int:
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO tasks (name, config_json, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (name, json.dumps(config, ensure_ascii=False), "pending", now, now),
            )
            return int(cur.lastrowid)

    def update_task(self, task_id: int, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if k in TASK_FIELDS}
        if not fields:
            return
        fields["updated_at"] = _now()
        columns = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE tasks SET {columns} WHERE id=?", (*fields.values(), task_id))

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def delete_task(self, task_id: int, delete_items: bool = True) -> None:
        with self._lock, self._conn:
            if delete_items:
                self._conn.execute("DELETE FROM items WHERE task_id=?", (task_id,))
            self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    # ---------------- 结果 ----------------
    def add_item(self, record: dict[str, Any]) -> str:
        """返回 inserted / dup_image / dup_prompt。"""
        with self._lock, self._conn:
            if self._conn.execute("SELECT 1 FROM items WHERE image_id=?", (record["image_id"],)).fetchone():
                return "dup_image"
            ph = record.get("prompt_hash") or ""
            if ph and self._conn.execute(
                "SELECT 1 FROM items WHERE task_id=? AND prompt_hash=?", (record["task_id"], ph)
            ).fetchone():
                return "dup_prompt"
            values = [record.get("task_id")]
            values.extend(record.get(f) for f in ITEM_FIELDS)
            values.append(_now())
            columns = "task_id, " + ", ".join(ITEM_FIELDS) + ", inserted_at"
            placeholders = ", ".join("?" * len(values))
            try:
                self._conn.execute(f"INSERT INTO items ({columns}) VALUES ({placeholders})", values)
            except sqlite3.IntegrityError:
                return "dup_prompt"
            return "inserted"

    def get_item(self, image_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM items WHERE image_id=?", (image_id,)).fetchone()
        return dict(row) if row else None

    def count_items(self, task_id: int | None = None) -> int:
        with self._lock:
            if task_id is None:
                row = self._conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) AS c FROM items WHERE task_id=?", (task_id,)).fetchone()
        return int(row["c"])

    def list_items(
        self,
        task_id: int | None = None,
        limit: int = 500,
        offset: int = 0,
        keyword: str = "",
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM items"
        args: list[Any] = []
        clauses: list[str] = []
        if task_id is not None:
            clauses.append("task_id=?")
            args.append(task_id)
        if keyword:
            clauses.append("(prompt LIKE ? OR negative_prompt LIKE ? OR lora LIKE ? OR checkpoint LIKE ? OR tags LIKE ?)")
            like = f"%{keyword}%"
            args.extend([like] * 5)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY image_id DESC LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def iter_items(self, task_id: int | None = None, batch: int = 500) -> Iterable[dict[str, Any]]:
        offset = 0
        while True:
            rows = self.list_items(task_id=task_id, limit=batch, offset=offset)
            if not rows:
                return
            yield from rows
            if len(rows) < batch:
                return
            offset += batch

    # ---------------- 模型名缓存 ----------------
    def get_model_cache(self, version_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM model_cache WHERE version_id=?", (version_id,)).fetchone()
        return dict(row) if row else None

    def put_model_cache(self, version_id: int, model_id: int | None, name: str, type_: str, base_model: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO model_cache (version_id, model_id, name, type, base_model, fetched_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(version_id) DO UPDATE SET "
                "model_id=excluded.model_id, name=excluded.name, type=excluded.type, "
                "base_model=excluded.base_model, fetched_at=excluded.fetched_at",
                (version_id, model_id, name, type_, base_model, _now()),
            )
