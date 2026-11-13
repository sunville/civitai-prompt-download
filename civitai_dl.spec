# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件、无控制台、无 UPX。"""

datas = [
    ("assets/icon.ico", "assets"),
    ("assets/icon.png", "assets"),
    ("docs/03-使用说明.md", "."),
    ("docs/04-误报处理说明.md", "."),
]

hiddenimports = [
    "pystray._win32",
    "PIL._tkinter_finder",
]

excludes = [
    "numpy", "pandas", "matplotlib", "scipy", "IPython", "pytest",
    "PySide6", "PyQt5", "PyQt6", "wx",
]

a = Analysis(
    ["run_app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CivitaiPromptDL",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version="scripts/version_info.txt",
)
