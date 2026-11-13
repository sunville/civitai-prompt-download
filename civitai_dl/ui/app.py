"""主窗口。"""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from .. import APP_NAME, __version__
from ..config import (
    COLUMNS, COLUMN_KEYS, CONTENT_LEVELS, ENTRY_KINDS, PERIODS, SITES, SORTS, TOOLS, Settings,
)
from ..export import export_items
from ..parser import strip_angle_tags
from ..paths import app_data_dir, resource_path
from ..runner import TaskRunner, build_task_name
from ..store import Store
from .dialogs import (
    AboutDialog, ApiKeyHelpDialog, ExportDialog, ItemDetailDialog, ModelPickerDialog, SettingsDialog,
)
from .theme import (
    ACCENT, ACCENT_DARK, BG, CARD, ERROR, FONT_BOLD, FONT_MONO, FONT_TITLE, FONT_UI, MUTED, OK, TEXT, WARN,
    apply_theme,
)
from .widgets import LogView, ScrollFrame, Tooltip, truncate

STATUS_LABELS = {
    "pending": "等待中",
    "running": "运行中",
    "paused": "已暂停",
    "done": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}

SORT_MAP = {label: code for code, label in SORTS}
PERIOD_MAP = {label: code for code, label in PERIODS}
LEVEL_MAP = {label: code for code, label in CONTENT_LEVELS}
SITE_MAP = {label: code for code, label in SITES}


class MainWindow(tk.Tk):
    def __init__(self, settings: Settings, store: Store, logger):
        super().__init__()
        self.settings = settings
        self.store = store
        self.logger = logger
        self.events: queue.Queue = queue.Queue()
        self.runner: TaskRunner | None = None
        self.current_task_id: int | None = None
        self.pending_auto_start = False
        self._preview_task_map: dict[str, int | None] = {}
        self._task_item_ids: dict[str, int] = {}
        self._tray_icon = None
        self._closing = False
        self._limit_vcmd = (self.register(self._validate_limit_text), "%P")

        self.title(f"{APP_NAME} v{__version__}")
        self.geometry("1400x900")
        self.minsize(1220, 780)
        self.configure(bg=BG)
        apply_theme(self)
        self._setup_icon()
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()
        self._build_body()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_tasks()
        self._refresh_preview_task_list()
        self._refresh_preview()
        self.after(120, self._poll_events)
        self._log("INFO", "欢迎使用 Civitai 提示词批量下载器。请先配置采集条件，然后点「加入队列并开始」。")

    # ------------------------------------------------------------ 构建界面
    def _setup_icon(self) -> None:
        ico = resource_path("assets/icon.ico")
        try:
            if ico.exists():
                self.iconbitmap(str(ico))
        except tk.TclError:
            pass

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="导出结果…", accelerator="Ctrl+E", command=self._open_export)
        file_menu.add_command(label="打开输出目录", command=self._open_output_dir)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._quit)
        menubar.add_cascade(label="文件", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="设置…", command=self._open_settings)
        settings_menu.add_command(label="获取 API Key 帮助", command=lambda: ApiKeyHelpDialog(self))
        menubar.add_cascade(label="设置", menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使用说明", command=self._open_manual)
        help_menu.add_command(label="关于", command=lambda: AboutDialog(self))
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.config(menu=menubar)
        self.bind("<Control-e>", lambda _e: self._open_export())

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 6, 10, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="站点：", style="Card.TLabel").pack(side="left")
        self.site_var = tk.StringVar(value=dict(SITES).get(self.settings.site, SITES[0][1]))
        site_combo = ttk.Combobox(bar, textvariable=self.site_var, values=[label for _c, label in SITES],
                                  state="readonly", width=22)
        site_combo.pack(side="left", padx=(0, 12))
        site_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_site_changed())

        ttk.Label(bar, text="内容级别：", style="Card.TLabel").pack(side="left")
        self.content_var = tk.StringVar(value=dict(CONTENT_LEVELS).get(self.settings.content_level, CONTENT_LEVELS[0][1]))
        content_combo = ttk.Combobox(bar, textvariable=self.content_var,
                                     values=[label for _c, label in CONTENT_LEVELS], state="readonly", width=20)
        content_combo.pack(side="left", padx=(0, 12))
        content_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_content_changed())

        self.api_status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.api_status).pack(side="left", padx=8)
        ttk.Button(bar, text="设置", command=self._open_settings, style="Tool.TButton").pack(side="right")
        ttk.Button(bar, text="输出目录", command=self._open_output_dir, style="Tool.TButton").pack(side="right", padx=6)
        ttk.Button(bar, text="导出", command=self._open_export, style="Tool.TButton").pack(side="right")
        self._update_api_status()

    def _build_body(self) -> None:
        self.paned = tk.PanedWindow(self, orient="horizontal", sashwidth=6, bd=0, bg=BG, sashrelief="flat")
        self.paned.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(self.paned, width=430)
        self.paned.add(left, minsize=470, width=500)
        self._build_config(left)

        right = ttk.Frame(self.paned)
        self.paned.add(right, minsize=620)
        self._build_right(right)

    # ------------------------------------------------------- 左侧配置面板
    def _build_config(self, parent: ttk.Frame) -> None:
        title = ttk.Frame(parent, padding=(4, 4, 4, 0))
        title.pack(fill="x")
        ttk.Label(title, text="采集配置", style="Title.TLabel").pack(side="left")
        ttk.Label(title, text="  (Ctrl+E 导出)", style="Muted.TLabel").pack(side="left", pady=(6, 0))

        scroll = ScrollFrame(parent, width=450)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        # --- 采集方式
        entry_box = ttk.LabelFrame(body, text="1. 采集方式", padding=6)
        entry_box.pack(fill="x", pady=(6, 6), padx=2)
        self.entry_kind_var = tk.StringVar(value="model")
        for index, (code, label) in enumerate(ENTRY_KINDS):
            ttk.Radiobutton(entry_box, text=label, value=code, variable=self.entry_kind_var,
                            command=self._on_entry_kind_changed).grid(row=index // 2, column=index % 2,
                                                                      sticky="w", padx=(0, 8), pady=1)
        self.entry_area = ttk.Frame(entry_box)
        self.entry_area.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        entry_box.columnconfigure(0, weight=1)
        entry_box.columnconfigure(1, weight=1)
        self._on_entry_kind_changed()

        # --- 过滤条件
        filter_box = ttk.LabelFrame(body, text="2. 过滤条件", padding=6)
        filter_box.pack(fill="x", pady=(0, 6), padx=2)

        self.sort_var = tk.StringVar(value=dict(SORTS).get("Newest") or SORTS[0][1])
        self.period_var = tk.StringVar(value=PERIODS[-1][1])
        self.date_start_var = tk.StringVar()
        self.date_end_var = tk.StringVar()
        self.limit_var = tk.StringVar(value=str(self.settings.default_limit))
        self.tag_var = tk.StringVar()
        self.resolve_var = tk.BooleanVar(value=self.settings.resolve_model_names)
        self.tool_vars: dict[str, tk.BooleanVar] = {}

        row_sort = ttk.Frame(filter_box)
        row_sort.pack(fill="x", pady=2)
        ttk.Label(row_sort, text="排序：", width=9).pack(side="left")
        ttk.Combobox(row_sort, textvariable=self.sort_var, values=[label for _c, label in SORTS],
                     state="readonly", width=10).pack(side="left")
        ttk.Label(row_sort, text="周期：", width=6).pack(side="left", padx=(10, 0))
        ttk.Combobox(row_sort, textvariable=self.period_var, values=[label for _c, label in PERIODS],
                     state="readonly", width=10).pack(side="left")

        row_date = ttk.Frame(filter_box)
        row_date.pack(fill="x", pady=2)
        ttk.Label(row_date, text="日期：", width=9).pack(side="left")
        ttk.Entry(row_date, textvariable=self.date_start_var, width=10).pack(side="left")
        ttk.Label(row_date, text=" 至 ").pack(side="left")
        ttk.Entry(row_date, textvariable=self.date_end_var, width=10).pack(side="left")
        ttk.Label(filter_box, text="格式 YYYY-MM-DD；仅「最新 / 最旧」精确生效，其他排序用周期近似。",
                  style="Muted.TLabel", wraplength=410).pack(anchor="w")

        row_limit = ttk.Frame(filter_box)
        row_limit.pack(fill="x", pady=2)
        ttk.Label(row_limit, text="数量上限：", width=9).pack(side="left")
        self.limit_spin = ttk.Spinbox(
            row_limit, from_=1, to=100000, textvariable=self.limit_var, width=12,
            validate="key", validatecommand=self._limit_vcmd,
        )
        self.limit_spin.pack(side="left")
        self.limit_spin.bind("<FocusIn>", lambda _e: self.limit_spin.selection_range(0, "end"))
        self.limit_spin.bind("<Return>", lambda _e: self._commit_limit())
        self.limit_spin.bind("<KP_Enter>", lambda _e: self._commit_limit())
        self.limit_spin.bind("<FocusOut>", lambda _e: self._commit_limit())
        ttk.Label(row_limit, text=" 条（可直接键盘输入，回车确认）", style="Muted.TLabel").pack(side="left")
        ttk.Label(filter_box, text="上限是“筛选后保留”的条数；支持鼠标滚轮/上下键微调。",
                  style="Muted.TLabel", wraplength=410).pack(anchor="w")

        tool_box = ttk.Frame(filter_box)
        tool_box.pack(fill="x", pady=(4, 0))
        ttk.Label(tool_box, text="生成工具：", width=9).pack(side="left", anchor="n")
        tool_inner = ttk.Frame(tool_box)
        tool_inner.pack(side="left", fill="x")
        for index, tool in enumerate(TOOLS):
            var = tk.BooleanVar(value=tool in self.settings.tool_filter)
            self.tool_vars[tool] = var
            ttk.Checkbutton(tool_inner, text=tool, variable=var).grid(row=index // 2, column=index % 2,
                                                                      sticky="w", padx=(0, 10))

        row_tag = ttk.Frame(filter_box)
        row_tag.pack(fill="x", pady=(4, 0))
        ttk.Label(row_tag, text="Tags：", width=9).pack(side="left")
        ttk.Entry(row_tag, textvariable=self.tag_var).pack(side="left", fill="x", expand=True)
        ttk.Label(filter_box, text="提示词包含这些 tag 才保留（逗号分隔，需全部包含）。",
                  style="Muted.TLabel", wraplength=410).pack(anchor="w")

        ttk.Checkbutton(filter_box, text="解析模型 / LoRA 名称（更完整，稍慢）",
                        variable=self.resolve_var).pack(anchor="w", pady=(4, 0))

        # --- 操作
        action = ttk.Frame(body)
        action.pack(fill="x", pady=(4, 10), padx=2)
        self.btn_add_start = ttk.Button(action, text="加入队列并开始", command=lambda: self._add_task(start=True))
        self.btn_add_start.pack(fill="x", pady=2)
        self.btn_add = ttk.Button(action, text="仅加入队列", command=lambda: self._add_task(start=False))
        self.btn_add.pack(fill="x", pady=2)
        ttk.Label(body, text="提示：单任务默认 1000 条、约 1 请求/秒；想抓更多请分批建任务。",
                  style="Muted.TLabel", wraplength=470).pack(anchor="w", padx=4, pady=(0, 8))

    def _on_entry_kind_changed(self) -> None:
        kind = self.entry_kind_var.get()
        if getattr(self, "_last_entry_kind", None) != kind:
            self.selected_model = None
            self._last_entry_kind = kind
        for child in self.entry_area.winfo_children():
            child.destroy()

        if kind in ("model", "lora"):
            label = "Checkpoint" if kind == "model" else "LoRA"
            self.model_label_var = tk.StringVar(value="未选择模型")
            row = ttk.Frame(self.entry_area)
            row.pack(fill="x")
            ttk.Label(row, text=f"{label}：").pack(side="left")
            ttk.Label(row, textvariable=self.model_label_var, foreground=ACCENT_DARK).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="搜索 / 选择…", command=lambda: self._pick_model("Checkpoint" if kind == "model" else "LORA")).pack(side="right")
            ttk.Label(self.entry_area, text="也可以点「搜索 / 选择」后直接粘贴模型链接。",
                      style="Muted.TLabel", wraplength=440).pack(anchor="w")

        elif kind == "author":
            row = ttk.Frame(self.entry_area)
            row.pack(fill="x")
            ttk.Label(row, text="作者用户名：").pack(side="left")
            self.author_var = tk.StringVar()
            ttk.Entry(row, textvariable=self.author_var).pack(side="left", fill="x", expand=True)
            ttk.Label(self.entry_area, text="例如 loganhimura1267；可填作者主页链接。",
                      style="Muted.TLabel", wraplength=440).pack(anchor="w")

        elif kind == "ranking":
            ttk.Label(self.entry_area, text="按上面的排序 / 周期抓取全站榜单（建议配合周期与数量限制）。",
                      style="Muted.TLabel").pack(anchor="w")
            self.selected_model = None

        elif kind == "keyword":
            row = ttk.Frame(self.entry_area)
            row.pack(fill="x")
            ttk.Label(row, text="关键词：").pack(side="left")
            self.keyword_var = tk.StringVar()
            ttk.Entry(row, textvariable=self.keyword_var).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="搜索模型并选择…", command=self._search_models_by_keyword).pack(side="left", padx=(6, 0))
            self.model_label_var = tk.StringVar(value="未选择模型（先搜索并选择）")
            ttk.Label(self.entry_area, textvariable=self.model_label_var, foreground=ACCENT_DARK).pack(anchor="w")
            ttk.Label(self.entry_area, text="说明：官方接口不支持按关键词搜图片；这里会先搜模型，再抓取该模型的示例图。",
                      style="Muted.TLabel", wraplength=440).pack(anchor="w")

        elif kind == "urls":
            ttk.Label(self.entry_area, text="每行一个链接（图片页 / 模型页 / 帖子 / 作者主页）：").pack(anchor="w")
            self.urls_text = tk.Text(self.entry_area, height=4, wrap="none", font=FONT_MONO, relief="solid", bd=1)
            self.urls_text.pack(fill="x", pady=2)
            ttk.Label(self.entry_area, text="示例：https://civitai.com/images/123456",
                      style="Muted.TLabel").pack(anchor="w")

        elif kind == "site_search":
            row = ttk.Frame(self.entry_area)
            row.pack(fill="x")
            ttk.Label(row, text="关键词：").pack(side="left")
            self.keyword_var = tk.StringVar()
            ttk.Entry(row, textvariable=self.keyword_var).pack(side="left", fill="x", expand=True)
            ttk.Label(self.entry_area, text="⚠ 实验功能：依赖非官方站内搜索服务，可能失效；会逐条补全数据，速度较慢。",
                      foreground=WARN, wraplength=440).pack(anchor="w")

        self.selected_model = getattr(self, "selected_model", None)

    def _pick_model(self, model_type: str) -> None:
        dialog = ModelPickerDialog(self, self.settings, model_type)
        self.wait_window(dialog)
        if dialog.result:
            self.selected_model = dialog.result
            self.model_label_var.set(f"{dialog.result['name']}（{dialog.result['type']}，ID {dialog.result['id']}）")
            self._log("INFO", f"已选择模型：{dialog.result['name']}（ID {dialog.result['id']}）")

    def _search_models_by_keyword(self) -> None:
        keyword = self.keyword_var.get().strip() if hasattr(self, "keyword_var") else ""
        dialog = ModelPickerDialog(self, self.settings, "Checkpoint", initial_query=keyword)
        self.wait_window(dialog)
        if dialog.result:
            self.selected_model = dialog.result
            self.model_label_var.set(f"{dialog.result['name']}（ID {dialog.result['id']}）")
            self._log("INFO", f"关键词「{keyword}」已选择模型：{dialog.result['name']}")

    # ------------------------------------------------------- 右侧主区域
    def _build_right(self, parent: ttk.Frame) -> None:
        # 任务队列
        queue_box = ttk.LabelFrame(parent, text="任务队列", padding=6)
        queue_box.pack(fill="x", pady=(2, 4))

        toolbar = ttk.Frame(queue_box)
        toolbar.pack(fill="x")
        for text, command in (("刷新", self._refresh_tasks), ("开始 / 继续", self._start_selected),
                              ("暂停", self._pause_selected), ("停止", self._stop_selected),
                              ("重试失败", self._retry_selected), ("删除", self._delete_selected)):
            ttk.Button(toolbar, text=text, command=command, style="Tool.TButton").pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="查看结果", command=self._preview_selected_task, style="Tool.TButton").pack(side="left")

        cols = ("id", "name", "status", "kept", "fetched", "updated")
        self.queue_tree = ttk.Treeview(queue_box, columns=cols, show="headings", height=6, selectmode="browse")
        for key, label, width, anchor in (("id", "ID", 50, "center"), ("name", "任务", 340, "w"),
                                          ("status", "状态", 80, "center"), ("kept", "已保留", 70, "center"),
                                          ("fetched", "已抓取", 70, "center"), ("updated", "更新于", 130, "center")):
            self.queue_tree.heading(key, text=label)
            self.queue_tree.column(key, width=width, anchor=anchor, stretch=(key == "name"))
        qscroll = ttk.Scrollbar(queue_box, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=qscroll.set)
        self.queue_tree.pack(side="left", fill="x", expand=True, pady=(4, 0))
        qscroll.pack(side="right", fill="y", pady=(4, 0))
        self.queue_tree.bind("<Double-1>", lambda _e: self._preview_selected_task())

        # 进度
        progress_box = ttk.Frame(parent)
        progress_box.pack(fill="x", pady=(0, 4))
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(progress_box, variable=self.progress_var, maximum=1000)
        self.progress.pack(fill="x")
        self.progress_label = tk.StringVar(value="当前没有运行中的任务")
        ttk.Label(progress_box, textvariable=self.progress_label).pack(anchor="w", pady=(2, 0))

        # 日志
        log_box = ttk.LabelFrame(parent, text="运行日志", padding=4)
        log_box.pack(fill="both", expand=True, pady=(0, 4))
        self.log_view = LogView(log_box, height=7)
        self.log_view.pack(fill="both", expand=True)
        log_tools = ttk.Frame(log_box)
        log_tools.pack(fill="x", pady=(3, 0))
        ttk.Button(log_tools, text="清空日志", command=self.log_view.clear, style="Tool.TButton").pack(side="left")
        ttk.Button(log_tools, text="打开日志文件", command=self._open_log_file, style="Tool.TButton").pack(side="left", padx=4)

        # 结果预览
        preview_box = ttk.LabelFrame(parent, text="结果预览", padding=6)
        preview_box.pack(fill="both", expand=True)

        prow = ttk.Frame(preview_box)
        prow.pack(fill="x")
        ttk.Label(prow, text="任务：").pack(side="left")
        self.preview_task_var = tk.StringVar(value="全部结果")
        self.preview_task_combo = ttk.Combobox(prow, textvariable=self.preview_task_var, state="readonly", width=36)
        self.preview_task_combo.pack(side="left")
        self.preview_task_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())
        ttk.Label(prow, text="  搜索：").pack(side="left")
        self.preview_keyword_var = tk.StringVar()
        search_entry = ttk.Entry(prow, textvariable=self.preview_keyword_var, width=18)
        search_entry.pack(side="left")
        search_entry.bind("<Return>", lambda _e: self._refresh_preview())
        ttk.Button(prow, text="搜索", command=self._refresh_preview, style="Tool.TButton").pack(side="left", padx=4)
        ttk.Button(prow, text="刷新", command=self._refresh_preview, style="Tool.TButton").pack(side="left")
        ttk.Button(prow, text="列设置", command=self._open_column_settings, style="Tool.TButton").pack(side="right")
        ttk.Button(prow, text="导出", command=self._open_export, style="Tool.TButton").pack(side="right", padx=4)

        self._preview_rows: list[dict[str, Any]] = []
        self._preview_sort_key = ""
        self._preview_sort_desc = False
        table_frame = ttk.Frame(preview_box)
        table_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.preview_tree = ttk.Treeview(table_frame, columns=(), show="headings", height=10, selectmode="extended")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.preview_tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.preview_tree.xview)
        self.preview_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.preview_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        self.preview_tree.bind("<Double-1>", lambda _e: self._open_item_detail())
        self.preview_count = tk.StringVar(value="")
        ttk.Label(preview_box, textvariable=self.preview_count, style="Muted.TLabel").pack(anchor="w")

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 2))
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value=f"v{__version__}  ·  数据目录：{app_data_dir()}")
        ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel").pack(side="left")

    # ------------------------------------------------------------ 工具栏联动
    def _on_site_changed(self) -> None:
        self.settings.site = SITE_MAP.get(self.site_var.get(), "civitai.red")
        self.settings.save()
        self._log("INFO", f"站点切换为 {self.settings.site}")

    def _on_content_changed(self) -> None:
        self.settings.content_level = LEVEL_MAP.get(self.content_var.get(), "all")
        self.settings.save()

    def _update_api_status(self) -> None:
        if self.settings.api_key:
            self.api_status.set("API Key：已填")
        else:
            self.api_status.set("API Key：未填（匿名，可能被限流）")

    # ------------------------------------------------------------ 任务操作
    def _selected_task_id(self) -> int | None:
        selection = self.queue_tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _build_config_from_form(self) -> dict[str, Any] | None:
        kind = self.entry_kind_var.get()
        config: dict[str, Any] = {
            "entry": kind,
            "site": self.settings.site,
            "content_level": LEVEL_MAP.get(self.content_var.get(), "all"),
            "sort": SORT_MAP.get(self.sort_var.get(), "Newest"),
            "period": PERIOD_MAP.get(self.period_var.get(), "AllTime"),
            "date_start": self.date_start_var.get().strip(),
            "date_end": self.date_end_var.get().strip(),
            "tag_filter": [t.strip() for t in self.tag_var.get().split(",") if t.strip()],
            "tool_filter": [tool for tool, var in self.tool_vars.items() if var.get()],
            "resolve_model_names": bool(self.resolve_var.get()),
        }
        self._commit_limit()
        raw_limit = self.limit_var.get().strip().replace(",", "").replace("，", "")
        if not raw_limit.isdigit():
            messagebox.showwarning("提示", "数量上限请填写正整数（输入框里可以直接键盘输入，回车确认）", parent=self)
            return None
        config["limit"] = int(raw_limit)
        if not 1 <= config["limit"] <= 100000:
            messagebox.showwarning("提示", "数量上限范围为 1 – 100000 条", parent=self)
            return None
        if not config["tool_filter"]:
            if not messagebox.askyesno("提示", "所有生成工具都没有勾选，将抓不到任何图。是否继续？", parent=self):
                return None
        for key in ("date_start", "date_end"):
            value = config.get(key)
            if value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    messagebox.showwarning("提示", f"日期格式不对：{value}（应为 YYYY-MM-DD）", parent=self)
                    return None

        if kind in ("model", "lora"):
            if not self.selected_model:
                messagebox.showwarning("提示", "请先选择模型", parent=self)
                return None
            config["model_id"] = self.selected_model["id"]
            config["model_name"] = self.selected_model["name"]
        elif kind == "keyword":
            if not self.selected_model:
                messagebox.showwarning("提示", "请先搜索并选择模型", parent=self)
                return None
            config["model_id"] = self.selected_model["id"]
            config["model_name"] = self.selected_model["name"]
        elif kind == "author":
            username = self.author_var.get().strip() if hasattr(self, "author_var") else ""
            if not username:
                messagebox.showwarning("提示", "请填写作者用户名", parent=self)
                return None
            if username.startswith("http"):
                from ..parser import parse_target_url

                target = parse_target_url(username)
                if not target or target[0] != "user":
                    messagebox.showwarning("提示", "无法从链接中解析作者用户名", parent=self)
                    return None
                username = target[1]
            config["username"] = username
        elif kind == "urls":
            text = self.urls_text.get("1.0", "end").strip() if hasattr(self, "urls_text") else ""
            urls = [line.strip() for line in text.splitlines() if line.strip()]
            if not urls:
                messagebox.showwarning("提示", "请粘贴至少一个链接", parent=self)
                return None
            config["urls"] = urls
        elif kind == "site_search":
            keyword = self.keyword_var.get().strip() if hasattr(self, "keyword_var") else ""
            if not keyword:
                messagebox.showwarning("提示", "请输入搜索关键词", parent=self)
                return None
            config["keyword"] = keyword
        return config

    def _validate_limit_text(self, proposed: str) -> bool:
        """数量输入框只允许输入数字（允许暂时为空）。"""
        if proposed == "":
            return True
        return proposed.isdigit() and len(proposed) <= 7

    def _commit_limit(self) -> str:
        """把输入框内容规范成合法数字（空/非法回退到默认值，超范围收敛到 1–100000）。"""
        text = self.limit_var.get().strip().replace(",", "").replace("，", "")
        try:
            value = int(text) if text else self.settings.default_limit
        except ValueError:
            value = self.settings.default_limit
        value = max(1, min(value, 100000))
        self.limit_var.set(str(value))
        return "break"

    def _add_task(self, start: bool = True) -> None:
        config = self._build_config_from_form()
        if not config:
            return
        name = build_task_name(config)
        task_id = self.store.add_task(config, name)
        self._log("INFO", f"已加入队列：#{task_id} {name}")
        self._refresh_tasks()
        if start:
            self._start_task(task_id)

    def _start_selected(self) -> None:
        task_id = self._selected_task_id()
        if task_id is None:
            messagebox.showinfo("提示", "请先在任务队列里选中一个任务", parent=self)
            return
        self._start_task(task_id)

    def _start_task(self, task_id: int) -> None:
        if self.runner and self.runner.is_running:
            messagebox.showinfo("提示", "已有任务在运行，请等待完成或先停止", parent=self)
            return
        task = self.store.get_task(task_id)
        if not task:
            return
        if task["status"] == "done" and int(task["kept"]) == 0:
            # 上次没抓到任何数据（例如筛选组合过严/接口限制）：允许直接重跑
            self.store.update_task(
                task_id, status="pending", cursor=None, kept=0, fetched=0, requests=0,
                skipped_no_meta=0, skipped_tool=0, skipped_tags=0, skipped_date=0, skipped_dup=0,
                message="",
            )
            self._log("INFO", f"任务 #{task_id} 上次没有抓到数据，重新开始")
            task = self.store.get_task(task_id)
        elif task["status"] == "done" and int(task["kept"]) > 0:
            if not messagebox.askyesno("任务已完成", "这个任务已完成。是否从头再跑一次？\n（会新建一个相同配置的任务）", parent=self):
                return
            config = self._load_task_config(task)
            new_id = self.store.add_task(config, task["name"] + "（重跑）")
            self._refresh_tasks()
            task_id = new_id
            task = self.store.get_task(task_id)
        self.current_task_id = task_id
        self.runner = TaskRunner(self.store, self.settings, task_id, self._on_runner_event)
        self._update_progress_labels(0, 0, 0, task.get("message") or "启动中…")
        self.runner.start()
        self._refresh_tasks()

    def _load_task_config(self, task: dict[str, Any]) -> dict[str, Any]:
        import json

        try:
            return json.loads(task.get("config_json") or "{}")
        except ValueError:
            return {}

    def _pause_selected(self) -> None:
        if self.runner and self.runner.is_running and self.current_task_id is not None:
            self.runner.pause()
            self._log("INFO", "请求暂停任务…")
        else:
            messagebox.showinfo("提示", "当前没有运行中的任务", parent=self)

    def _stop_selected(self) -> None:
        if self.runner and self.runner.is_running and self.current_task_id is not None:
            if messagebox.askyesno("确认", "确定停止当前任务吗？已抓取的数据会保留，下次可继续。", parent=self):
                self.runner.cancel()
                self._log("WARN", "请求停止任务…")
        else:
            messagebox.showinfo("提示", "当前没有运行中的任务", parent=self)

    def _retry_selected(self) -> None:
        task_id = self._selected_task_id()
        if task_id is None:
            messagebox.showinfo("提示", "请先选中一个任务", parent=self)
            return
        task = self.store.get_task(task_id)
        if not task:
            return
        if task["status"] == "done":
            messagebox.showinfo("提示", "已完成的任务不需要重试；如需再抓请新建任务", parent=self)
            return
        self._start_task(task_id)

    def _delete_selected(self) -> None:
        task_id = self._selected_task_id()
        if task_id is None:
            messagebox.showinfo("提示", "请先选中一个任务", parent=self)
            return
        if self.runner and self.runner.is_running and self.current_task_id == task_id:
            messagebox.showinfo("提示", "任务正在运行，请先停止", parent=self)
            return
        if not messagebox.askyesno("确认删除", f"确定删除任务 #{task_id} 及其结果吗？此操作不可恢复。", parent=self):
            return
        self.store.delete_task(task_id)
        self._log("WARN", f"已删除任务 #{task_id}")
        self._refresh_tasks()
        self._refresh_preview()

    def _preview_selected_task(self) -> None:
        task_id = self._selected_task_id()
        if task_id is None:
            return
        task = self.store.get_task(task_id)
        if not task:
            return
        label = f"#{task_id} {task['name']}（{task['kept']} 条）"
        values = list(self.preview_task_combo["values"])
        if label not in values:
            self._refresh_preview_task_list()
            values = list(self.preview_task_combo["values"])
        if label in values:
            self.preview_task_var.set(label)
        self._refresh_preview()

    # ------------------------------------------------------------ 事件循环
    def _on_runner_event(self, kind: str, payload: dict[str, Any]) -> None:
        # 由工作线程调用，只是入队，保证线程安全
        self.events.put((kind, payload))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        if not self._closing:
            self.after(120, self._poll_events)

    def _handle_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "log":
            self._log(payload.get("level", "INFO"), payload.get("message", ""))
        elif kind == "progress":
            if payload.get("task_id") == self.current_task_id:
                self._update_progress_labels(payload.get("kept", 0), payload.get("fetched", 0),
                                             payload.get("requests", 0), "运行中…")
                self._update_queue_row(payload.get("task_id"))
        elif kind == "status":
            status = payload.get("status", "")
            message = payload.get("message", "")
            task_id = payload.get("task_id")
            if status == "paused":
                self._log("WARN", f"任务已暂停：{message}")
            elif status in ("done", "failed", "cancelled"):
                self._log("OK" if status == "done" else ("ERROR" if status == "failed" else "WARN"),
                          f"任务结束：{message}")
                self.current_task_id = None
                self._refresh_tasks()
                self._refresh_preview_task_list()
                self._refresh_preview()
                if status in ("done", "failed", "cancelled"):
                    self._auto_start_next()
            self._refresh_tasks()

    def _update_progress_labels(self, kept: int, fetched: int, requests: int, message: str) -> None:
        config = None
        if self.current_task_id is not None:
            task = self.store.get_task(self.current_task_id)
            if task:
                config = self._load_task_config(task)
        limit = int(config.get("limit") or self.settings.default_limit) if config else self.settings.default_limit
        self.progress.configure(maximum=max(1, limit))
        self.progress_var.set(min(kept, limit))
        self.progress_label.set(f"{message}  已保留 {kept}/{limit}  已抓取 {fetched}  请求 {requests} 次")

    def _update_queue_row(self, task_id: int | None) -> None:
        if task_id is None:
            return
        task = self.store.get_task(task_id)
        if not task:
            return
        iid = str(task_id)
        if self.queue_tree.exists(iid):
            self.queue_tree.item(iid, values=(task["id"], task["name"], STATUS_LABELS.get(task["status"], task["status"]),
                                              task["kept"], task["fetched"], str(task["updated_at"] or "")[:19].replace("T", " ")))

    def _auto_start_next(self) -> None:
        pending = [t for t in self.store.list_tasks(limit=200) if t["status"] == "pending"]
        if not pending:
            return
        next_task = sorted(pending, key=lambda t: t["id"])[0]
        self._log("INFO", f"自动开始队列中的下一个任务：#{next_task['id']} {next_task['name']}")
        self.after(900, lambda: self._start_task(int(next_task["id"])))

    # ------------------------------------------------------------ 刷新界面
    def _refresh_tasks(self) -> None:
        selected = self.queue_tree.selection()
        self.queue_tree.delete(*self.queue_tree.get_children())
        for task in self.store.list_tasks(limit=100):
            self.queue_tree.insert("", "end", iid=str(task["id"]), values=(
                task["id"], task["name"], STATUS_LABELS.get(task["status"], task["status"]),
                task["kept"], task["fetched"], str(task["updated_at"] or "")[:19].replace("T", " ")))
        if selected:
            for iid in selected:
                if self.queue_tree.exists(iid):
                    self.queue_tree.selection_set(iid)
                    self.queue_tree.see(iid)
                    break

    def _refresh_preview_task_list(self) -> None:
        current = self.preview_task_var.get()
        self._preview_task_map = {"全部结果": None}
        values = ["全部结果"]
        for task in self.store.list_tasks(limit=100):
            label = f"#{task['id']} {task['name']}（{task['kept']} 条）"
            values.append(label)
            self._preview_task_map[label] = int(task["id"])
        self.preview_task_combo.configure(values=values)
        if current not in self._preview_task_map:
            current = "全部结果"
        self.preview_task_var.set(current)

    def _refresh_preview(self) -> None:
        task_id = self._preview_task_map.get(self.preview_task_var.get())
        keyword = self.preview_keyword_var.get().strip() if hasattr(self, "preview_keyword_var") else ""
        rows = self.store.list_items(task_id=task_id, limit=1000, keyword=keyword)
        self._preview_rows = rows
        self._render_preview_table()
        total = self.store.count_items(task_id)
        self.preview_count.set(f"显示 {len(rows)} 条 / 共 {total} 条" + (f"（搜索：{keyword}）" if keyword else "")
                               + "  可按住 Ctrl/Shift 多选后导出选中行")

    def _render_preview_table(self) -> None:
        columns = [key for key in self.settings.preview_columns if key in COLUMN_KEYS]
        if not columns:
            columns = list(COLUMN_KEYS)
        labels = {key: label for key, label, _en, _desc in COLUMNS}
        widths = {"prompt": 320, "negativePrompt": 220, "lora": 180, "checkpoint": 160, "tool": 90,
                  "tags": 220, "imageId": 80, "pageUrl": 160, "baseModel": 110, "modelId": 90,
                  "modelVersionId": 110, "nsfwLevel": 80, "username": 110, "createdAt": 140, "rawJson": 120}
        self.preview_tree.configure(columns=columns, show="headings")
        rows = list(self._preview_rows)
        if self._preview_sort_key:
            def sort_key(row: dict[str, Any]) -> Any:
                value = row.get(self._preview_sort_key) if self._preview_sort_key != "imageId" else row.get("image_id")
                if self._preview_sort_key == "pageUrl":
                    value = row.get("page_url")
                if isinstance(value, (int, float)):
                    return (0, value)
                return (1, str(value or "").lower())

            rows.sort(key=sort_key, reverse=self._preview_sort_desc)
        for key in columns:
            arrow = ""
            if key == self._preview_sort_key:
                arrow = " ▼" if self._preview_sort_desc else " ▲"
            self.preview_tree.heading(key, text=labels.get(key, key) + arrow,
                                      command=lambda k=key: self._sort_preview(k))
            self.preview_tree.column(key, width=widths.get(key, 120), stretch=(key in ("prompt", "negativePrompt")))
        self.preview_tree.delete(*self.preview_tree.get_children())
        for row in rows:
            values = []
            for key in columns:
                if key == "imageId":
                    values.append(row.get("image_id"))
                elif key == "pageUrl":
                    values.append(row.get("page_url") or "")
                elif key == "rawJson":
                    values.append(truncate(row.get("raw_json"), 40))
                elif key == "prompt":
                    values.append(truncate(strip_angle_tags(row.get("prompt") or ""), 160))
                else:
                    values.append(truncate(row.get(key), 160))
            self.preview_tree.insert("", "end", iid=str(row["image_id"]), values=values)

    def _sort_preview(self, key: str) -> None:
        if self._preview_sort_key == key:
            self._preview_sort_desc = not self._preview_sort_desc
        else:
            self._preview_sort_key = key
            self._preview_sort_desc = False
        self._render_preview_table()

    def _open_item_detail(self) -> None:
        selection = self.preview_tree.selection()
        if not selection:
            return
        try:
            image_id = int(selection[0])
        except ValueError:
            return
        row = self.store.get_item(image_id)
        if row:
            ItemDetailDialog(self, row)

    def _open_column_settings(self) -> None:
        win = tk.Toplevel(self)
        win.title("预览列设置")
        win.transient(self)
        win.grab_set()
        win.geometry("360x520")
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="勾选要在预览表格里显示的列：").pack(anchor="w")
        vars_map: dict[str, tk.BooleanVar] = {}
        for key, label, _en, _desc in COLUMNS:
            var = tk.BooleanVar(value=key in self.settings.preview_columns)
            vars_map[key] = var
            ttk.Checkbutton(frame, text=label, variable=var).pack(anchor="w")

        def apply() -> None:
            selected = [key for key, var in vars_map.items() if var.get()]
            if not selected:
                messagebox.showwarning("提示", "至少保留一列", parent=win)
                return
            self.settings.preview_columns = selected
            self.settings.save()
            self._refresh_preview()
            win.destroy()

        ttk.Button(frame, text="保存", command=apply).pack(anchor="e", pady=8)

    # ------------------------------------------------------------ 菜单动作
    def _open_export(self) -> None:
        task_id = self._preview_task_map.get(self.preview_task_var.get())
        keyword = self.preview_keyword_var.get().strip() if hasattr(self, "preview_keyword_var") else ""
        selected_ids = []
        for iid in self.preview_tree.selection():
            try:
                selected_ids.append(int(iid))
            except (TypeError, ValueError):
                continue
        ExportDialog(self, self.store, self.settings, task_id, keyword, selected_ids=selected_ids)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self, self.settings)
        self.wait_window(dialog)
        if dialog.saved:
            self.site_var.set(dict(SITES).get(self.settings.site, SITES[0][1]))
            self.content_var.set(dict(CONTENT_LEVELS).get(self.settings.content_level, CONTENT_LEVELS[0][1]))
            self._update_api_status()
            self._log("OK", "设置已保存")

    def _open_output_dir(self) -> None:
        path = Path(self.settings.output_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("无法打开", f"无法打开输出目录：{exc}", parent=self)

    def _open_log_file(self) -> None:
        from ..paths import log_dir

        try:
            os.startfile(str(log_dir() / "app.log"))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("无法打开", f"无法打开日志文件：{exc}", parent=self)

    def _open_manual(self) -> None:
        candidates = [resource_path("使用说明.md"), resource_path("docs/03-使用说明.md"),
                      Path(__file__).resolve().parent.parent.parent / "docs" / "03-使用说明.md"]
        for candidate in candidates:
            if Path(candidate).exists():
                try:
                    os.startfile(str(candidate))  # type: ignore[attr-defined]
                    return
                except Exception:  # noqa: BLE001
                    break
        messagebox.showinfo("使用说明", "使用说明文件未找到。\n常用操作：\n"
                                    "1. 左侧选择采集方式与过滤条件；\n"
                                    "2. 点「加入队列并开始」；\n"
                                    "3. 任务完成后在下方预览、导出；\n"
                                    "4. 设置里可填 API Key、代理与默认目录。", parent=self)

    # ------------------------------------------------------------ 日志
    def _log(self, level: str, message: str) -> None:
        try:
            self.log_view.append(message, level)
        except tk.TclError:
            pass
        try:
            self.logger.log({"INFO": 20, "OK": 20, "WARN": 30, "ERROR": 40}.get(level, 20), message)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 托盘与退出
    def _on_close(self) -> None:
        if self.settings.minimize_to_tray and not self._closing:
            self._hide_to_tray()
        else:
            self._quit()

    def _hide_to_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            self.iconify()
            return
        if self._tray_icon is None:
            image = Image.new("RGB", (64, 64), (31, 111, 235))
            draw = ImageDraw.Draw(image)
            draw.text((18, 14), "C", fill="white")
            menu = pystray.Menu(
                pystray.MenuItem("显示主界面", lambda _icon, _item: self.after(0, self._show_from_tray), default=True),
                pystray.MenuItem("退出", lambda _icon, _item: self.after(0, self._quit)),
            )
            self._tray_icon = pystray.Icon("civitai_dl", image, APP_NAME, menu)
            threading.Thread(target=self._tray_icon.run, daemon=True).start()
        self.withdraw()
        self._log("INFO", "已最小化到系统托盘，双击托盘图标可恢复")

    def _show_from_tray(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            if self.runner and self.runner.is_running:
                self.runner.cancel()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.store.close()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
