"""应用数据目录与资源路径。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_FOLDER = "CivitaiPromptDL"


def app_data_dir() -> Path:
    """用户数据目录（配置/数据库），保证存在。"""
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if base:
        root = Path(base) / APP_FOLDER
    else:  # 非 Windows 兜底
        root = Path.home() / f".{APP_FOLDER.lower()}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resource_path(name: str) -> Path:
    """PyInstaller onefile 下的资源路径。"""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parent.parent
    return root / name


def default_output_dir() -> Path:
    """默认导出目录：exe/程序目录下的 output。"""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent
    out = root / "output"
    out.mkdir(parents=True, exist_ok=True)
    return out


def log_dir() -> Path:
    root = app_data_dir() / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root
