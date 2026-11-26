# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：构建 Windows 桌面端 Auto-AFK.exe（--onedir 模式）。"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ICON_PATH = ROOT / "gui" / "afk.ico"
ASSETS_DIR = ROOT / "gui" / "assets"

datas = []
if ASSETS_DIR.is_dir():
    datas.append((str(ASSETS_DIR), "gui/assets"))

hiddenimports = [
    "afk_core",
    "afk_core.ansi",
    "afk_core.autostart",
    "afk_core.cli",
    "afk_core.completion",
    "afk_core.config",
    "afk_core.configsync",
    "afk_core.doctor",
    "afk_core.emulator",
    "afk_core.events",
    "afk_core.housekeeping",
    "afk_core.idle",
    "afk_core.listval",
    "afk_core.monitor",
    "afk_core.notify",
    "afk_core.numval",
    "afk_core.proc",
    "afk_core.project_ops",
    "afk_core.repair",
    "afk_core.report",
    "afk_core.scheduler",
    "afk_core.supervisor",
    "afk_core.updater",
    "gui",
    "gui.advanced_panel",
    "gui.cards",
    "gui.icons",
    "gui.idle_prompt",
    "gui.live_log_columns",
    "gui.main",
    "gui.modern",
    "gui.nav_rail",
    "gui.project_status_page",
    "gui.run_order_page",
    "gui.run_stage",
    "gui.settings_page",
    "gui.spring",
    "gui.themes",
    "gui.widget",
    "yaml",
    "psutil",
    "mss",
    "winotify",
    "onepush",
]

excludes = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "tkinter",
]

a = Analysis(
    [str(ROOT / "gui" / "main.py")],
    pathex=[str(ROOT)],
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

exe_kwargs = {}
if ICON_PATH.is_file():
    exe_kwargs["icon"] = str(ICON_PATH)
version_file = Path(SPECPATH) / "version_info.txt"
if version_file.is_file():
    exe_kwargs["version"] = str(version_file)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Auto-AFK",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    **exe_kwargs,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Auto-AFK",
)
