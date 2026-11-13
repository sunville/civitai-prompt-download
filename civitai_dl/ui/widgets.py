"""通用 Tk 组件：可滚动面板、带颜色日志框、工具提示。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from .theme import BG, ERROR, FONT_MONO, FONT_UI, INFO, MUTED, OK, TEXT, WARN


class ScrollFrame(ttk.Frame):
    """Canvas + 内嵌 Frame 的竖向滚动容器。"""

    def __init__(self, master: tk.Misc, width: int = 400, **kwargs: Any):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, width=width, highlightthickness=0, bd=0, bg=BG)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda _e: self._bind_wheel(False))

    def _on_inner_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, active: bool) -> None:
        if active:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event: tk.Event) -> None:
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass


class LogView(ttk.Frame):
    """带颜色标签的只读日志框。"""

    def __init__(self, master: tk.Misc, height: int = 8):
        super().__init__(master)
        self.text = tk.Text(self, height=height, wrap="word", font=FONT_MONO, bd=1, relief="solid",
                            bg="#fbfcfe", fg=TEXT, state="disabled", padx=4, pady=2)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.tag_configure("INFO", foreground=TEXT)
        self.text.tag_configure("WARN", foreground=WARN)
        self.text.tag_configure("ERROR", foreground=ERROR)
        self.text.tag_configure("OK", foreground=OK)
        self.text.tag_configure("MUTED", foreground=MUTED)

    def append(self, message: str, level: str = "INFO") -> None:
        import datetime

        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.text.configure(state="normal")
        self.text.insert("end", f"[{stamp}] {message}\n", level if level in ("INFO", "WARN", "ERROR", "OK", "MUTED") else "INFO")
        # 控制日志长度，避免长时间运行吃内存
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > 2000:
            self.text.delete("1.0", f"{lines - 1500}.0")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class Tooltip:
    """简单鼠标悬停提示。"""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._cancel, add="+")

    def _schedule(self, _event: tk.Event) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self, _event: tk.Event | None = None) -> None:
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._window:
            self._window.destroy()
            self._window = None

    def _show(self) -> None:
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{x}+{y}")
        tk.Label(win, text=self.text, background="#ffffe1", foreground=TEXT, relief="solid",
                 borderwidth=1, font=FONT_UI, justify="left", padx=6, pady=3).pack()
        self._window = win


def truncate(text: Any, limit: int = 120) -> str:
    value = str(text or "").replace("\r", " ").replace("\n", " ")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def center_window(win: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    parent.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")
