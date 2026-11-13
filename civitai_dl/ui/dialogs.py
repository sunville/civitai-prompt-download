"""各类对话框：模型选择、导出、设置、关于、API Key 帮助、结果详情。"""
from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from ..api import CivitaiClient
from ..config import COLUMNS, CONTENT_LEVELS, DEFAULT_EXPORT_COLUMNS, SITES, Settings
from ..errors import AppError
from ..export import FORMAT_LABELS, export_items
from ..naming import build_export_filename
from ..parser import parse_target_url, strip_angle_tags
from ..store import Store
from .theme import BG, CARD, FONT_MONO, FONT_UI, MUTED, TEXT
from .widgets import center_window


def _modal(win: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    win.transient(parent)
    win.resizable(True, True)
    win.grab_set()
    center_window(win, parent, width, height)


# ---------------------------------------------------------------- 模型选择

class ModelPickerDialog(tk.Toplevel):
    """搜索 / 粘贴链接选择 Checkpoint 或 LoRA。"""

    def __init__(self, parent: tk.Misc, settings: Settings, model_type: str = "", initial_query: str = ""):
        super().__init__(parent)
        self.settings = settings
        self.model_type = model_type
        self.result: dict[str, Any] | None = None
        self._rows: list[dict[str, Any]] = []
        self._queue: queue.Queue = queue.Queue()
        self._client = CivitaiClient(settings)

        self.title("选择模型")
        _modal(self, parent, 860, 580)
        self._build()
        if initial_query:
            self.query_var.set(initial_query)
            self.after(200, self._search)
        self.after(100, self._poll)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="关键词：").pack(side="left")
        self.query_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query_var, width=26)
        entry.pack(side="left", padx=(0, 6))
        entry.bind("<Return>", lambda _e: self._search())
        self.type_var = tk.StringVar(value=self.model_type or "全部类型")
        ttk.Combobox(top, textvariable=self.type_var, values=["全部类型", "Checkpoint", "LORA"],
                     state="readonly", width=11).pack(side="left", padx=4)
        self.btn_search = ttk.Button(top, text="搜索", command=self._search)
        self.btn_search.pack(side="left", padx=4)

        link_row = ttk.Frame(outer)
        link_row.pack(fill="x", pady=(8, 0))
        ttk.Label(link_row, text="或粘贴模型链接 / ID：").pack(side="left")
        self.link_var = tk.StringVar()
        ttk.Entry(link_row, textvariable=self.link_var, width=46).pack(side="left", padx=(0, 6))
        entry2 = ttk.Entry(link_row, textvariable=self.link_var)
        entry2.pack_forget()
        ttk.Button(link_row, text="解析", command=self._resolve_link).pack(side="left")

        container = ttk.Frame(outer)
        container.pack(fill="both", expand=True, pady=6)
        cols = ("name", "type", "base", "id")
        self.tree = ttk.Treeview(container, columns=cols, show="headings", height=14, selectmode="browse")
        for key, label, width in (("name", "名称", 380), ("type", "类型", 90), ("base", "基础模型", 200), ("id", "ID", 80)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w" if key != "id" else "center", stretch=(key == "name"))
        scroll = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self._choose())
        self.tree.bind("<Return>", lambda _e: self._choose())

        self.status_var = tk.StringVar(value="输入关键词搜索；也可以直接粘贴模型链接")
        ttk.Label(outer, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w")

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="选择", command=self._choose).pack(side="right", padx=(0, 6))

    def _search(self) -> None:
        query = self.query_var.get().strip()
        if not query:
            self.status_var.set("请先输入关键词")
            return
        types = "" if self.type_var.get() == "全部类型" else self.type_var.get()
        self.btn_search.config(state="disabled")
        self.status_var.set("搜索中…")
        threading.Thread(target=self._search_worker, args=(query, types), daemon=True).start()

    def _search_worker(self, query: str, types: str) -> None:
        try:
            data = self._client.search_models(query=query, types=types, limit=30)
            self._queue.put(("ok", data.get("items") or []))
        except AppError as exc:
            self._queue.put(("err", exc.friendly()))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("err", f"搜索失败：{exc}"))

    def _resolve_link(self) -> None:
        text = self.link_var.get().strip()
        target = parse_target_url(text)
        if not target:
            messagebox.showwarning("无法识别", "请输入模型链接（如 https://civitai.com/models/123456）或数字 ID", parent=self)
            return
        kind, value = target
        if kind not in ("model", "model_version"):
            messagebox.showwarning("无法识别", "请粘贴模型页面链接（/models/数字）或模型 ID", parent=self)
            return
        model_id = int(value)
        self.status_var.set("解析中…")
        threading.Thread(target=self._resolve_worker, args=(model_id,), daemon=True).start()

    def _resolve_worker(self, model_id: int) -> None:
        try:
            data = self._client.model(model_id)
            self._queue.put(("ok", [data]))
        except AppError as exc:
            self._queue.put(("err", exc.friendly()))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("err", f"解析失败：{exc}"))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                self.btn_search.config(state="normal")
                if kind == "err":
                    self.status_var.set(str(payload))
                else:
                    self._fill_rows(payload)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)

    def _fill_rows(self, items: list[dict[str, Any]]) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rows = []
        for model in items:
            versions = model.get("modelVersions") or []
            base = ", ".join(model.get("baseModels") or []) or (versions[0].get("baseModel") if versions else "")
            row = {
                "id": model.get("id"),
                "name": model.get("name") or "",
                "type": model.get("type") or "",
                "base": base,
                "version_id": versions[0].get("id") if versions else None,
            }
            if self.model_type and row["type"] != self.model_type:
                continue
            self._rows.append(row)
            self.tree.insert("", "end", values=(row["name"], row["type"], row["base"], row["id"]))
        if self._rows:
            self.status_var.set(f"找到 {len(self._rows)} 个结果；双击或选中后点「选择」")
        else:
            self.status_var.set("没有结果，换个关键词试试")

    def _choose(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中一个模型", parent=self)
            return
        index = self.tree.index(selection[0])
        if 0 <= index < len(self._rows):
            self.result = self._rows[index]
        self.destroy()


# ---------------------------------------------------------------- 导出

class ExportDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, store: Store, settings: Settings, task_id: int | None,
                 keyword: str = "", selected_ids: list[int] | None = None):
        super().__init__(parent)
        self.store = store
        self.settings = settings
        self.keyword = keyword
        self.selected_ids = list(selected_ids or [])
        self.initial_task_id = task_id
        self.selected_label = f"仅选中结果（{len(self.selected_ids)} 条）" if self.selected_ids else ""
        self._config_cache: dict[int, dict[str, Any]] = {}
        self.result: dict[str, Any] | None = None
        self.title("导出结果")
        _modal(self, parent, 760, 660)
        self._build(task_id)

    def _build(self, task_id: int | None) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        row1 = ttk.Frame(outer)
        row1.pack(fill="x")
        ttk.Label(row1, text="导出范围：").pack(side="left")
        self.scope_map: dict[str, int | None] = {"全部结果": None}
        values = ["全部结果"]
        if self.selected_label:
            values.append(self.selected_label)
        for task in self.store.list_tasks(limit=100):
            label = f"#{task['id']} {task['name']}（{task['kept']} 条）"
            values.append(label)
            self.scope_map[label] = int(task["id"])
        self.scope_var = tk.StringVar(value="全部结果")
        if self.selected_label:
            self.scope_var.set(self.selected_label)
        elif task_id is not None:
            for label, tid in self.scope_map.items():
                if tid == task_id:
                    self.scope_var.set(label)
                    break
        self.scope_combo = ttk.Combobox(row1, textvariable=self.scope_var, values=values,
                                        state="readonly", width=56)
        self.scope_combo.pack(side="left", padx=6)
        self.scope_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_scope_changed())

        row2 = ttk.Frame(outer)
        row2.pack(fill="x", pady=(10, 0))
        ttk.Label(row2, text="导出格式：").pack(side="left")
        self.fmt_labels = [label for _code, label in FORMAT_LABELS]
        self.fmt_codes = [code for code, _label in FORMAT_LABELS]
        self.fmt_var = tk.StringVar(value=self.settings.last_export_format or "csv")
        current_label = dict(FORMAT_LABELS).get(self.fmt_var.get(), self.fmt_labels[0])
        self.fmt_combo = ttk.Combobox(row2, values=self.fmt_labels, state="readonly", width=26)
        self.fmt_combo.set(current_label)
        self.fmt_combo.pack(side="left", padx=6)
        self.fmt_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_format(self.fmt_combo.get()))
        self.split_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="按 NSFW 分级拆分成多个文件", variable=self.split_var).pack(side="left", padx=12)

        ttk.Label(outer, text="导出字段（TXT 只导出正向提示词，忽略此处勾选）：",
                  style="Section.TLabel").pack(anchor="w", pady=(12, 4))
        cols_frame = ttk.Frame(outer)
        cols_frame.pack(fill="x")
        self.col_vars: dict[str, tk.BooleanVar] = {}
        for index, (key, label, english, _desc) in enumerate(COLUMNS):
            var = tk.BooleanVar(value=key in DEFAULT_EXPORT_COLUMNS)
            self.col_vars[key] = var
            ttk.Checkbutton(cols_frame, text=f"{label}（{english}）", variable=var).grid(
                row=index // 2, column=index % 2, sticky="w", padx=(0, 12), pady=1)
        btn_row = ttk.Frame(outer)
        btn_row.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_row, text="全选", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(btn_row, text="全部取消", command=lambda: self._set_all(False)).pack(side="left", padx=6)
        ttk.Button(btn_row, text="恢复默认", command=self._set_default).pack(side="left")

        ttk.Label(outer, text="保存到：", style="Section.TLabel").pack(anchor="w", pady=(12, 4))
        path_row = ttk.Frame(outer)
        path_row.pack(fill="x")
        self.path_var = tk.StringVar(value="")
        ttk.Entry(path_row, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(path_row, text="浏览…", command=self._browse).pack(side="left", padx=6)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(14, 0), side="bottom")
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="开始导出", command=self._do_export).pack(side="right", padx=(0, 6))
        self.path_var.set(self._default_path(self.fmt_var.get()))

    def _on_format(self, label: str) -> None:
        index = self.fmt_labels.index(label) if label in self.fmt_labels else 0
        self.fmt_var.set(self.fmt_codes[index])
        self.path_var.set(self._default_path(self.fmt_var.get()))

    def _on_scope_changed(self) -> None:
        self.path_var.set(self._default_path(self.fmt_var.get()))

    def _config_for_scope(self, scope: str) -> dict[str, Any] | None:
        """取出当前导出范围对应任务的配置，用于生成默认文件名。"""
        if scope == self.selected_label:
            task_id = self.initial_task_id
        elif scope == "全部结果":
            task_id = None
        else:
            task_id = self.scope_map.get(scope)
        if task_id is None:
            return None
        task_id = int(task_id)
        if task_id in self._config_cache:
            return self._config_cache[task_id]
        task = self.store.get_task(task_id)
        config: dict[str, Any] | None = None
        if task:
            try:
                config = json.loads(task.get("config_json") or "{}")
            except ValueError:
                config = None
        if config is not None:
            self._config_cache[task_id] = config
        return config

    def _default_path(self, code: str) -> str:
        ext = {"csv": ".csv", "jsonl": ".jsonl", "txt": ".txt", "xlsx": ".xlsx"}.get(code, ".csv")
        base = Path(self.settings.output_dir)
        if not base.exists():
            base = Path(os.path.expanduser("~"))
        config = self._config_for_scope(self.scope_var.get())
        return str(base / build_export_filename(config, ext))

    def _set_all(self, value: bool) -> None:
        for var in self.col_vars.values():
            var.set(value)

    def _set_default(self) -> None:
        for key, var in self.col_vars.items():
            var.set(key in DEFAULT_EXPORT_COLUMNS)

    def _browse(self) -> None:
        code = self.fmt_var.get()
        ext = {"csv": ".csv", "jsonl": ".jsonl", "txt": ".txt", "xlsx": ".xlsx"}.get(code, ".csv")
        path = filedialog.asksaveasfilename(parent=self, title="保存导出文件", defaultextension=ext,
                                            initialdir=self.settings.output_dir,
                                            filetypes=[("全部文件", "*.*")])
        if path:
            self.path_var.set(path)

    def _do_export(self) -> None:
        code = self.fmt_var.get()
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("提示", "请填写保存路径", parent=self)
            return
        columns = [key for key, var in self.col_vars.items() if var.get()]
        if code != "txt" and not columns:
            messagebox.showwarning("提示", "至少勾选一个导出字段", parent=self)
            return
        scope = self.scope_var.get()
        use_selection = bool(self.selected_label) and scope == self.selected_label
        task_id = None if use_selection else self.scope_map.get(scope)
        image_ids = self.selected_ids if use_selection else None
        self.status_var.set("导出中…")
        self.update_idletasks()
        try:
            count, files = export_items(self.store, path, fmt=code, columns=columns,
                                        task_id=task_id, nsfw_split=self.split_var.get(),
                                        keyword=self.keyword, image_ids=image_ids)
        except ValueError as exc:
            messagebox.showwarning("无法导出", str(exc), parent=self)
            self.status_var.set("")
            return
        self.settings.last_export_format = code
        self.settings.save()
        file_list = "\n".join(str(f) for f in files)
        self.status_var.set(f"完成：{count} 条")
        if messagebox.askyesno("导出完成", f"已导出 {count} 条到：\n{file_list}\n\n是否打开所在文件夹？", parent=self):
            try:
                os.startfile(str(Path(files[0]).parent))  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        self.result = {"count": count, "files": [str(f) for f in files]}
        self.destroy()


# ---------------------------------------------------------------- 设置

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, settings: Settings):
        super().__init__(parent)
        self.settings = settings
        self.saved = False
        self.title("设置")
        _modal(self, parent, 680, 750)
        self._build()

    def _add_row(self, parent: tk.Misc, label: str, factory, hint: str = "") -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=3)
        ttk.Label(frame, text=label, width=17, anchor="w").pack(side="left")
        widget = factory(frame)
        widget.pack(side="left", fill="x", expand=True)
        if hint:
            ttk.Label(frame, text=hint, style="Muted.TLabel").pack(side="left", padx=(6, 0))

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="账号与站点", style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        key_frame = ttk.Frame(outer)
        key_frame.pack(fill="x", pady=3)
        ttk.Label(key_frame, text="API Key：", width=17, anchor="w").pack(side="left")
        self.api_var = tk.StringVar(value=self.settings.api_key)
        self.api_entry = ttk.Entry(key_frame, textvariable=self.api_var, show="*")
        self.api_entry.pack(side="left", fill="x", expand=True)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(key_frame, text="显示", variable=self.show_key,
                        command=lambda: self.api_entry.config(show="" if self.show_key.get() else "*")).pack(side="left", padx=6)
        ttk.Button(key_frame, text="如何获取？", command=lambda: ApiKeyHelpDialog(self)).pack(side="left")

        self.site_map = {label: code for code, label in SITES}
        self.site_var = tk.StringVar(value=dict(SITES).get(self.settings.site, SITES[0][1]))
        site_row = ttk.Frame(outer)
        site_row.pack(fill="x", pady=3)
        ttk.Label(site_row, text="默认站点：", width=17, anchor="w").pack(side="left")
        ttk.Combobox(site_row, textvariable=self.site_var, values=[label for _c, label in SITES],
                     state="readonly", width=26).pack(side="left")

        level_map = {label: code for code, label in CONTENT_LEVELS}
        self.level_var = tk.StringVar(value=dict(CONTENT_LEVELS).get(self.settings.content_level, CONTENT_LEVELS[0][1]))
        level_row = ttk.Frame(outer)
        level_row.pack(fill="x", pady=3)
        ttk.Label(level_row, text="默认内容级别：", width=17, anchor="w").pack(side="left")
        ttk.Combobox(level_row, textvariable=self.level_var, values=[label for _c, label in CONTENT_LEVELS],
                     state="readonly", width=26).pack(side="left")

        ttk.Label(outer, text="网络与限速", style="Section.TLabel").pack(anchor="w", pady=(10, 4))
        self.rate_var = tk.StringVar(value=str(self.settings.rate_interval))
        self._add_row(outer, "请求间隔（秒）：", lambda p: ttk.Entry(p, textvariable=self.rate_var, width=10),
                      "建议 1.0，太快容易被限流")
        self.timeout_var = tk.StringVar(value=str(self.settings.timeout))
        self._add_row(outer, "超时（秒）：", lambda p: ttk.Entry(p, textvariable=self.timeout_var, width=10))
        self.retries_var = tk.StringVar(value=str(self.settings.retries))
        self._add_row(outer, "重试次数：", lambda p: ttk.Entry(p, textvariable=self.retries_var, width=10))

        proxy_labels = {"system": "跟随系统（推荐）", "direct": "直连（不用代理）", "manual": "手动填写代理"}
        self.proxy_labels = proxy_labels
        self.proxy_mode_var = tk.StringVar(value=proxy_labels.get(self.settings.proxy_mode, "跟随系统（推荐）"))
        proxy_row = ttk.Frame(outer)
        proxy_row.pack(fill="x", pady=3)
        ttk.Label(proxy_row, text="代理模式：", width=17, anchor="w").pack(side="left")
        ttk.Combobox(proxy_row, textvariable=self.proxy_mode_var, values=list(proxy_labels.values()),
                     state="readonly", width=22).pack(side="left")
        self.proxy_url_var = tk.StringVar(value=self.settings.proxy_url)
        self._add_row(outer, "代理地址：", lambda p: ttk.Entry(p, textvariable=self.proxy_url_var),
                      "如 http://127.0.0.1:7890")

        ttk.Label(outer, text="其他", style="Section.TLabel").pack(anchor="w", pady=(10, 4))
        self.limit_var = tk.StringVar(value=str(self.settings.default_limit))
        self._add_row(outer, "单任务默认条数：", lambda p: ttk.Entry(p, textvariable=self.limit_var, width=10))
        self.resolve_var = tk.BooleanVar(value=self.settings.resolve_model_names)
        ttk.Checkbutton(outer, text="解析模型 / LoRA 名称（每张图可能多几次请求，但结果更完整）",
                        variable=self.resolve_var).pack(anchor="w", pady=2)
        self.tray_var = tk.BooleanVar(value=self.settings.minimize_to_tray)
        ttk.Checkbutton(outer, text="关闭窗口时最小化到系统托盘", variable=self.tray_var).pack(anchor="w", pady=2)

        out_row = ttk.Frame(outer)
        out_row.pack(fill="x", pady=3)
        ttk.Label(out_row, text="默认输出目录：", width=17, anchor="w").pack(side="left")
        self.out_var = tk.StringVar(value=self.settings.output_dir)
        ttk.Entry(out_row, textvariable=self.out_var).pack(side="left", fill="x", expand=True)
        ttk.Button(out_row, text="浏览…", command=self._browse_out).pack(side="left", padx=6)

        advanced = ttk.LabelFrame(outer, text="高级（站内搜索实验功能）")
        advanced.pack(fill="x", pady=(8, 0))
        token_row = ttk.Frame(advanced, padding=6)
        token_row.pack(fill="x")
        ttk.Label(token_row, text="搜索服务 Token：", width=17, anchor="w").pack(side="left")
        self.token_var = tk.StringVar(value=self.settings.site_search_token)
        ttk.Entry(token_row, textvariable=self.token_var, show="*").pack(side="left", fill="x", expand=True)
        ttk.Label(advanced, text="留空则使用内置公开 token；该功能不是 civitai 官方契约，可能失效。",
                  style="Muted.TLabel").pack(anchor="w", padx=6, pady=(0, 6))

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(12, 0), side="bottom")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="保存", command=self._save).pack(side="right", padx=(0, 6))

    def _browse_out(self) -> None:
        path = filedialog.askdirectory(parent=self, title="选择默认输出目录",
                                       initialdir=self.settings.output_dir or str(Path.home()))
        if path:
            self.out_var.set(path)

    def _save(self) -> None:
        def number(text: str, default: float) -> float:
            try:
                return float(text)
            except (TypeError, ValueError):
                return default

        settings = self.settings
        settings.api_key = self.api_var.get().strip()
        settings.site = self.site_map.get(self.site_var.get(), settings.site)
        level_map = {label: code for code, label in CONTENT_LEVELS}
        settings.content_level = level_map.get(self.level_var.get(), settings.content_level)
        settings.rate_interval = max(0.2, number(self.rate_var.get(), settings.rate_interval))
        settings.timeout = int(max(10, number(self.timeout_var.get(), settings.timeout)))
        settings.retries = int(max(0, number(self.retries_var.get(), settings.retries)))
        mode_labels = {v: k for k, v in self.proxy_labels.items()}
        settings.proxy_mode = mode_labels.get(self.proxy_mode_var.get(), "system")
        settings.proxy_url = self.proxy_url_var.get().strip()
        settings.default_limit = int(max(1, number(self.limit_var.get(), settings.default_limit)))
        settings.resolve_model_names = self.resolve_var.get()
        settings.minimize_to_tray = self.tray_var.get()
        settings.site_search_token = self.token_var.get().strip()
        output = self.out_var.get().strip()
        if output:
            settings.output_dir = output
        if settings.proxy_mode == "manual" and not settings.proxy_url:
            messagebox.showwarning("提示", "选择了手动代理，但没有填写代理地址", parent=self)
            return
        settings.save()
        self.saved = True
        self.destroy()


# ---------------------------------------------------------------- 关于 / 帮助

class AboutDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        from .. import __version__
        from ..paths import app_data_dir

        self.title("关于")
        _modal(self, parent, 560, 480)
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Civitai 提示词批量下载器", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text=f"版本 {__version__}", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        text = tk.Text(outer, wrap="word", height=15, font=FONT_UI, background=CARD, relief="solid", bd=1)
        text.pack(fill="both", expand=True)
        content = (
            "本软件用于批量下载 civitai 上公开可见图片的提示词与生成参数，仅供个人学习、研究、创作参考。\n\n"
            "使用须知：\n"
            "1. 请遵守 civitai 的服务条款与速率限制，不要用于恶意爬取、倒卖数据或批量盗图。\n"
            "2. 不采集需要破解权限才能查看的私密内容；不采集、不生成涉及未成年人的性相关内容。\n"
            "3. 提示词与图片版权归原作者所有，转载/商用前请取得授权；导出结果会保留来源链接。\n"
            "4. 账号凭据（API Key）只保存在本机配置文件中，不会上传到任何服务器。\n"
            "5. 本软件按“现状”提供，不承诺数据完整（部分图片作者未公开提示词）。\n\n"
            f"数据目录：{app_data_dir()}\n\n"
            "技术说明：数据来自 civitai 官方 REST API；界面使用 Tkinter；结果默认保存在本地 SQLite。"
        )
        text.insert("1.0", content)
        text.configure(state="disabled")
        ttk.Button(outer, text="关闭", command=self.destroy).pack(anchor="e", pady=(10, 0))


class ApiKeyHelpDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("如何获取 API Key")
        _modal(self, parent, 580, 440)
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="API Key 不是必填，但强烈建议填写：", style="Section.TLabel").pack(anchor="w")
        text = tk.Text(outer, wrap="word", height=13, font=FONT_UI, background=CARD, relief="solid", bd=1)
        text.pack(fill="both", expand=True, pady=(6, 8))
        content = (
            "填写 API Key 后可以：\n"
            "• 更稳定地访问接口（匿名请求更容易被限流）；\n"
            "• 查询单张成熟内容图片的详细信息；\n"
            "• 在需要时访问更高浏览级别的内容。\n\n"
            "获取步骤：\n"
            "1. 登录 civitai 网站；\n"
            "2. 打开「账号设置」（右上角头像 → Account settings）；\n"
            "3. 找到 API Keys / API 令牌区域，创建一个 Key；\n"
            "4. 复制生成的 Key，粘贴到本软件的「设置 → API Key」中并保存。\n\n"
            "注意：API Key 等同于你的账号权限，请勿分享给他人；本软件只把它保存在本机配置文件中。\n"
            "如果 Key 失效或权限不足，任务日志会明确提示「请重新填写 API Key」。"
        )
        text.insert("1.0", content)
        text.configure(state="disabled")
        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Button(row, text="打开 civitai 账号设置",
                   command=lambda: webbrowser.open("https://civitai.com/user/account")).pack(side="left")
        ttk.Button(row, text="关闭", command=self.destroy).pack(side="right")


class ItemDetailDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, row: dict[str, Any]):
        super().__init__(parent)
        self.row = row
        self.title(f"结果详情 · 图片 {row.get('image_id')}")
        _modal(self, parent, 800, 640)
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text=f"图片 ID：{row.get('image_id')}", style="Section.TLabel").pack(side="left")
        ttk.Label(head, text=f"  工具：{row.get('tool')}  Checkpoint：{row.get('checkpoint')}",
                  style="Muted.TLabel").pack(side="left")
        if row.get("page_url"):
            ttk.Button(head, text="打开图片页", command=lambda: webbrowser.open(str(row["page_url"]))).pack(side="right")

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True, pady=8)

        def add_tab(title: str, value: Any, mono: bool = False) -> None:
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=title)
            text = tk.Text(frame, wrap="word", font=FONT_MONO if mono else FONT_UI, background=CARD,
                           relief="solid", bd=1)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            text.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            text.insert("1.0", str(value or "（空）"))
            text.configure(state="disabled")

        add_tab("正向提示词", strip_angle_tags(row.get("prompt")))
        add_tab("负向提示词", row.get("negative_prompt"))
        add_tab("LoRA / 模型", f"LoRA：{row.get('lora') or '（无）'}\n\nCheckpoint：{row.get('checkpoint') or '（无）'}\n\n"
                                f"模型 ID：{row.get('model_id')}\n版本 ID：{row.get('model_version_id')}\n"
                                f"基础模型：{row.get('base_model')}")
        add_tab("Tags", row.get("tags"))
        add_tab("原始 JSON", row.get("raw_json"), mono=True)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="复制正向提示词", command=lambda: self._copy(row.get("prompt"))).pack(side="left")
        ttk.Button(bottom, text="复制负向提示词", command=lambda: self._copy(row.get("negative_prompt"))).pack(side="left", padx=6)
        ttk.Button(bottom, text="关闭", command=self.destroy).pack(side="right")

    def _copy(self, value: Any) -> None:
        self.clipboard_clear()
        self.clipboard_append(str(value or ""))
        messagebox.showinfo("已复制", "已复制到剪贴板", parent=self)
