"""离屏渲染 GUI 视觉预览截图（不启动 Supervisor/EventBus，只构建视觉外壳）。

用法：
    QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR="C:\\Windows\\Fonts" \
        .venv/Scripts/python.exe tools/ui_preview.py [输出目录]

产出：
    ui_preview_default.png    总览页默认状态（上个/当前/下个舞台 + 多列实时日志）
    ui_preview_order.png      运行顺序页
    ui_preview_projects.png   项目配置页
    ui_preview_settings.png   设置页
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTime, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QStackedWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from gui import main as gui_main
from gui.advanced_panel import AdvancedPanel
from gui.cards import STATUS_TEXT, _theme_status_colors
from gui.live_log_columns import CORE_KEY, CORE_TITLE, MultiColumnLogBoard
from gui.modern import AnimatedButton, AnimatedPill, add_shadow, apply_theme
from gui.nav_rail import NavRail
from gui.project_status_page import ProjectConfigPage
from gui.run_order_page import RunOrderPage
from gui.run_stage import RunStageWidget
from gui.settings_page import SettingsPage

FAKE_RUNS = {
    "March7thAssistant": {
        "status": "running",
        "run_id": "demo-m7a-1",
        "pid": 22048,
        "started_at": time.time() - 92,
        "last_message": "开始每日任务：清体力",
        "attempt": 1,
    },
    "BAAH": {
        "status": "success",
        "run_id": "demo-baah-1",
        "pid": 10492,
        "exit_code": 0,
        "finished_at": time.time() - 12,
        "attempt": 1,
    },
    "MaaEnd": {"status": "pending"},
    "ZenlessZoneZero-OneDragon": {"status": "pending"},
    "BetterGI": {
        "status": "failed",
        "pid": 8802,
        "exit_code": 1,
        "last_message": "识别超时，等待修复引擎介入",
        "attempt": 2,
    },
}

FAKE_PROJECTS = {
    "March7thAssistant": {
        "display_name": "三月七小助手（崩坏：星穹铁道 / 云游戏）",
        "gui": {"args": ["app.py"]},
        "launch_type": "python", "path": "March7thAssistant",
        "python": ".venv\\Scripts\\python.exe", "args": ["main.py"],
        "config_dir": "configs\\March7thAssistant", "retry": 1,
        "hang_timeout": 900, "require_admin": True, "require_finish": True,
        "min_runtime_sec": 120, "repo": "moesnow/March7thAssistant",
        "version": "v2026.11.20-2-gd7915805",
    },
    "BAAH": {
        "display_name": "BAAH（蔚蓝档案 / MuMu 模拟器 ADB）",
        "gui": {"args": ["jsoneditor.py"]},
        "launch_type": "python", "path": "BAAH",
        "python": ".venv\\Scripts\\python.exe",
        "args": ["jsoneditor.py", "{config_name}"],
        "config_name": "国际服挂机.json", "config_dir": "configs\\BAAH",
        "retry": 1, "hang_timeout": 900, "require_admin": True,
        "repo": "BlueArchiveArisHelper/BAAH", "version": "BAAH2.5.13",
    },
    "MaaEnd": {
        "display_name": "MaaEnd（明日方舟：终末地 / 独占前台）",
        "gui": {"exe": "install\\mxu.exe"},
        "launch_type": "exe", "path": "MaaEnd", "exe": "install\\mxu.exe",
        "instance": "全套日常",
        "retry": 1, "hang_timeout": 1800, "repo": "MaaEnd/MaaEnd",
        "version": "v3.3.0-beta.1-6-g9e260d506",
    },
    "ZenlessZoneZero-OneDragon": {
        "display_name": "绝区零一条龙（绝区零 / CLI 独占）",
        "gui": {"args": ["one_dragon.py"]},
        "launch_type": "python", "path": "ZenlessZoneZero-OneDragon",
        "python": ".venv\\Scripts\\python.exe",
        "args": ["src\\zzz_od\\application\\zzz_application_launcher.py"],
        "retry": 1, "hang_timeout": 1800,
        "repo": "OneDragon-Anything/ZenlessZoneZero-OneDragon",
        "version": "v2.6.1-11-g1d57ea8d",
    },
    "BetterGI": {
        "display_name": "BetterGI（原神 / 更好的原神）",
        "gui": {"exe": "publish\\BetterGI.exe"},
        "launch_type": "exe", "path": "BetterGI", "exe": "publish\\BetterGI.exe",
        "retry": 1, "hang_timeout": 1800,
        "repo": "babalae/better-genshin-impact", "version": "0.66.0-106-gf24ea3e",
    },
}

FAKE_REGISTRY = {"projects": FAKE_PROJECTS, "groups": [], "reserved": []}
FAKE_SETTINGS = {
    "chain": {
        "order": ["BAAH", "March7thAssistant", "MaaEnd", "ZenlessZoneZero-OneDragon", "BetterGI"],
        "parallel_blocks": [["BAAH", "March7thAssistant"]],
    }
}

FAKE_LOG = [
    ("core", "info", "启动更新：正在后台更新全部项目..."),
    ("core", "info", "定时启动: 开 04:00"),
    ("March7thAssistant", "info", "启动成功 pid=22048"),
    ("March7thAssistant", "info", "开始每日任务：清体力"),
    ("BAAH", "info", "启动成功 pid=10492"),
    ("BAAH", "info", "结算完成，退出码 0"),
    ("BetterGI", "error", "识别超时，等待修复引擎介入"),
    ("core", "warn", "BetterGI 失败，尝试 2/3"),
] * 8


def _noop(*_args, **_kwargs):
    pass


def _subtitle_text():
    names = [str(spec.get("display_name", k)).split("（")[0].strip() for k, spec in FAKE_PROJECTS.items()]
    return "一键挂机 · {0} 统一编排".format(" + ".join(names)) if names else "一键挂机 · 统一编排"


def _name_of(key):
    spec = FAKE_PROJECTS.get(key) or {}
    return str(spec.get("display_name", key)).split("（")[0] or key


def _config_of(key):
    spec = FAKE_PROJECTS.get(key) or {}
    return str(spec.get("instance") or spec.get("config_name") or "")


def _status_of(status):
    palette = _theme_status_colors()
    return STATUS_TEXT.get(status, status), palette.get(status, palette["pending"])


def build_overview():
    overview = QWidget()
    overview.setObjectName("central")
    root = QVBoxLayout(overview)
    root.setContentsMargins(14, 12, 14, 10)
    root.setSpacing(12)

    header = QFrame()
    header.setObjectName("header")
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(18, 13, 18, 11)
    header_layout.setSpacing(9)

    top = QHBoxLayout()
    top.setSpacing(12)
    logo = QLabel()
    logo.setPixmap(gui_main.make_icon().pixmap(36, 36))
    title_box = QVBoxLayout()
    title_box.setSpacing(1)
    title = QLabel("Auto-AFK")
    title.setObjectName("title")
    subtitle = QLabel(_subtitle_text())
    subtitle.setObjectName("subtitle")
    title_box.addWidget(title)
    title_box.addWidget(subtitle)
    top.addWidget(logo)
    top.addLayout(title_box, 1)
    chain_pill = AnimatedPill("运行中", gui_main._theme_chain_colors()["running"])
    chain_btn = AnimatedButton("挂机中…", variant="primary")
    chain_btn.setObjectName("primary")
    chain_btn.setEnabled(False)
    add_shadow(chain_btn, alpha=105, blur=16, dy=2)
    stop_all_btn = AnimatedButton("全部停止", variant="danger")
    stop_all_btn.setObjectName("danger")
    top.addWidget(chain_pill)
    top.addWidget(chain_btn)
    top.addWidget(stop_all_btn)
    header_layout.addLayout(top)

    def _divider():
        line = QFrame()
        line.setObjectName("toolbarDivider")
        line.setFixedHeight(16)
        return line

    controls = QHBoxLayout()
    controls.setSpacing(6)
    for item in (
        AnimatedButton("环境自检"),
        AnimatedButton("校验补丁"),
        _divider(),
        AnimatedButton("打开日志"),
        AnimatedButton("打开配置"),
        _divider(),
        AnimatedButton("立即更新"),
        AnimatedButton("更新MuMu"),
    ):
        controls.addWidget(item)
    controls.addStretch(1)
    pre_run_check = QCheckBox("运行前更新")
    pre_run_check.setChecked(True)
    schedule_check = QCheckBox("定时启动")
    schedule_check.setChecked(True)
    schedule_time = QTimeEdit()
    schedule_time.setDisplayFormat("HH:mm")
    schedule_time.setTime(QTime(4, 0))
    controls.addWidget(pre_run_check)
    controls.addWidget(schedule_check)
    controls.addWidget(schedule_time)
    header_layout.addLayout(controls)
    root.addWidget(header)

    stage_title = QLabel("运行状态")
    stage_title.setObjectName("sectionTitle")
    root.addWidget(stage_title)
    run_stage = RunStageWidget()
    run_stage.set_gui_probe(lambda k: bool((FAKE_PROJECTS.get(k) or {}).get("gui")))
    run_stage.update_stage(
        tasks=FAKE_SETTINGS["chain"]["order"],
        runs=FAKE_RUNS,
        queue=FAKE_SETTINGS["chain"]["order"],
        chain_status="running",
        name_of=_name_of,
        config_of=_config_of,
        color_of=gui_main._project_color_for,
        status_of=_status_of,
    )
    root.addWidget(run_stage, 0)

    splitter = QSplitter(Qt.Vertical)
    splitter.setChildrenCollapsible(False)
    log_board = MultiColumnLogBoard(auto_open=True)
    log_board.set_catalog([(k, _name_of(k)) for k in FAKE_PROJECTS])
    log_board.open_column(CORE_KEY, CORE_TITLE, gui_main._project_color_for(CORE_KEY))
    log_board.sync_running_projects(
        FAKE_RUNS,
        lambda k: (_name_of(k), gui_main._project_color_for(k)),
        _status_of,
    )
    splitter.addWidget(log_board)
    root.addWidget(splitter, 1)
    return overview, chain_pill, log_board


def populate_log(log_board):
    by_proj = {}
    level_colors = gui_main._theme_level_colors()
    ts = time.strftime("%H:%M:%S")
    for project, level, message in FAKE_LOG[:48]:
        pcol = gui_main._project_color_for(project)
        mcol = level_colors.get(level, pcol)
        html = '<span style="color:{0}">[{1}] [{2}]</span> <span style="color:{3}">{4}</span>'.format(
            pcol, ts, project, mcol, message
        )
        by_proj.setdefault(project, []).append(html)
    log_board.append_batch(by_proj)


def build_shell():
    central = QWidget()
    central.setObjectName("central")
    central.resize(1360, 900)
    root = QHBoxLayout(central)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    nav = NavRail()
    nav.add_item("overview", "总览", icon="home")
    nav.add_item("order", "运行顺序", icon="order")
    nav.add_item("projects", "项目配置", icon="status")
    nav.add_item("settings", "设置", icon="settings")
    nav.finish()
    nav.set_active("overview", animate=False)
    root.addWidget(nav)

    stack = QStackedWidget()
    root.addWidget(stack, 1)

    overview, chain_pill, log_board = build_overview()
    populate_log(log_board)
    stack.addWidget(overview)

    order_page = RunOrderPage(
        registry_getter=lambda: FAKE_REGISTRY,
        settings_getter=lambda: FAKE_SETTINGS,
        color_getter=gui_main._project_color_for,
        log_fn=_noop,
        runs_getter=lambda: FAKE_RUNS,
    )
    stack.addWidget(order_page)

    projects_page = ProjectConfigPage(
        registry_getter=lambda: FAKE_REGISTRY,
        color_getter=gui_main._project_color_for,
        log_fn=_noop,
    )
    stack.addWidget(projects_page)

    advanced = AdvancedPanel()
    settings_page = SettingsPage(log_fn=_noop, registry=FAKE_REGISTRY, advanced=advanced)
    stack.addWidget(settings_page)

    nav.navigate.connect(lambda key: _navigate(stack, nav, key))
    return central, stack, nav, chain_pill, order_page, projects_page, settings_page, advanced


def _navigate(stack, nav, key):
    mapping = {"overview": 0, "order": 1, "projects": 2, "settings": 3}
    idx = mapping.get(key)
    if idx is None or idx >= stack.count():
        return
    stack.setCurrentIndex(idx)
    nav.set_active(key, animate=False)


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs"
    theme = sys.argv[2] if len(sys.argv) > 2 else "dark"
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    apply_theme(app, theme)

    window, stack, nav, _pill, _order, _projects, _settings, _adv = build_shell()
    window.show()

    def shot_overview():
        window.grab().save(str(out_dir / "ui_preview_default.png"))
        nav.navigate.emit("order")
        QTimer.singleShot(100, shot_order)

    def shot_order():
        window.grab().save(str(out_dir / "ui_preview_order.png"))
        nav.navigate.emit("projects")
        QTimer.singleShot(120, shot_projects)

    def shot_projects():
        window.grab().save(str(out_dir / "ui_preview_projects.png"))
        nav.navigate.emit("settings")
        QTimer.singleShot(100, shot_settings)

    def shot_settings():
        window.grab().save(str(out_dir / "ui_preview_settings.png"))
        app.quit()

    QTimer.singleShot(120, shot_overview)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
