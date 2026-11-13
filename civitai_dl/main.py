"""程序入口。"""
from __future__ import annotations

import argparse
import ctypes
import multiprocessing
import sys
from pathlib import Path


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _fatal(message: str) -> None:
    from .config import setup_logging

    try:
        setup_logging().exception(message)
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "Civitai 提示词批量下载器 - 启动失败", 0x10)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    from . import APP_NAME, __version__

    parser = argparse.ArgumentParser(prog="civitai_dl", description=f"{APP_NAME} v{__version__}")
    parser.add_argument("--version", action="store_true", help="显示版本号")
    parser.add_argument("--selftest", action="store_true", help="运行自检（不打开界面）")
    parser.add_argument("--live", action="store_true", help="自检时额外做一次真实 API 请求（需要能访问 civitai）")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0

    if args.selftest:
        from .selftest import run_selftest

        return run_selftest(live=args.live)

    multiprocessing.freeze_support()
    _enable_dpi_awareness()
    try:
        from .config import Settings, setup_logging
        from .paths import app_data_dir
        from .store import Store
        from .ui.app import MainWindow

        logger = setup_logging()
        settings = Settings.load()
        store = Store(app_data_dir() / "data.db")
        window = MainWindow(settings, store, logger)
        window.mainloop()
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback

        _fatal(f"程序启动失败：{exc}\n\n{traceback.format_exc()}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
