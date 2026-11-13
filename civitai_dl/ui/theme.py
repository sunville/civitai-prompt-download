"""ttk 主题与全局样式。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#f3f5f8"
CARD = "#ffffff"
BORDER = "#d8dee6"
TEXT = "#1f2933"
MUTED = "#5c6b7a"
ACCENT = "#1f6feb"
ACCENT_DARK = "#1552b0"
OK = "#1a7f37"
WARN = "#b35900"
ERROR = "#c62828"
INFO = TEXT

FONT_UI = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 12, "bold")
FONT_MONO = ("Consolas", 9)


def apply_theme(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
    root.option_add("*Font", FONT_UI)
    root.option_add("*TCombobox*Listbox.font", FONT_UI)
    root.option_add("*Text.font", FONT_UI)

    style.configure(".", font=FONT_UI, background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD, relief="solid", borderwidth=1)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 8))
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=FONT_TITLE)
    style.configure("Section.TLabel", background=BG, foreground=ACCENT_DARK, font=FONT_BOLD)
    style.configure("TLabelframe", background=BG, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT_DARK, font=FONT_BOLD)
    style.configure("TButton", padding=(8, 3), font=FONT_UI)
    style.configure("Tool.TButton", padding=(6, 2), font=FONT_UI)
    style.configure("TEntry", fieldbackground="#ffffff", padding=2)
    style.configure("TCombobox", padding=(2, 1))
    style.configure("TSpinbox", padding=2)
    style.configure("TCheckbutton", background=BG, font=FONT_UI)
    style.configure("Card.TCheckbutton", background=CARD, font=FONT_UI)
    style.configure("TRadiobutton", background=BG, font=FONT_UI)
    style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff",
                    foreground=TEXT, rowheight=22, font=FONT_UI)
    style.configure("Treeview.Heading", font=FONT_BOLD, padding=(4, 3))
    style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", TEXT)])
    style.configure("TNotebook", background=BG)
    style.configure("TNotebook.Tab", padding=(10, 4), font=FONT_UI)
    style.configure("TProgressbar", thickness=14)
    style.configure("Status.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 8))
    return style
