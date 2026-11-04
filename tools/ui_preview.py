"""离屏渲染 GUI 视觉预览截图（不启动 Supervisor/EventBus，只构建视觉外壳）。

用法：
    QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR="C:\\Windows\\Fonts" \
        .venv/Scripts/python.exe tools/ui_preview.py [输出目录]

产出：
    ui_preview_default.png    总览页默认状态
    ui_preview_states.png     总览页动效状态（涟漪/按压/悬停/指示条中段）
    ui_preview_project.png    项目界面嵌入页
    ui_preview_settings.png   设置页
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from gui import main as gui_main
from gui.modern import (
    AnimatedButton,
    AnimatedPill,
    AnimatedTabWidget,
    FlowLayout,
    add_shadow,
    apply_palette,
    enable_smooth_scroll,
)
from gui.nav_rail import NavRail
from gui.project_page import ProjectPage
from gui.settings_page import SettingsPage

FAKE_RUNS = {
    "March7thAssistant": {"status": "running", "pid": 22048, "last_message": "开始每日任务：清体力", "attempt": 1},
    "BAAH": {"status": "success", "pid": 10492, "exit_code": 0, "attempt": 1},
    "MaaEnd": {"status": "pending"},
    "ZenlessZoneZero-OneDragon": {"status": "pending"},
    "BetterGI": {"status": "failed", "pid": 8802, "exit_code": 1, "last_message": "识别超时，等待修复引擎介入", "attempt": 2},
}

# 必须和 configs/core/afk_projects.yaml 保持 5 个项目：预览图是拿来核对布局的，
# 少一个项目就看不到 FlowLayout 在 1360 宽度下真实的 3+2 折行（曾漏掉绝区零）。
FAKE_PROJECTS = {
    "March7thAssistant": {"display_name": "三月七小助手（崩坏：星穹铁道 / 云游戏）", "gui": {"args": ["app.py"]}},
    "BAAH": {"display_name": "BAAH（蔚蓝档案 / MuMu 模拟器 ADB）", "gui": {"args": ["jsoneditor.py"]}},
    "MaaEnd": {"display_name": "MaaEnd（明日方舟：终末地 / 独占前台）", "gui": {"exe": "install\\mxu.exe"}},
    "ZenlessZoneZero-OneDragon": {"display_name": "绝区零一龙（绝区零 / CLI 独占）", "gui": {"args": ["one_dragon.py"]}},
    "BetterGI": {"display_name": "BetterGI（原神 / 更好的原神）", "gui": {"exe": "publish\\BetterGI.exe"}},
}

FAKE_REGISTRY = {"projects": FAKE_PROJECTS, "groups": [], "reserved": []}

FAKE_LOG = [
    ("core", "info", "启动更新：正在后台更新全部项目..."),
    ("core", "info", "定时启动: 开 04:00"),
    ("March7thAssistant", "info", "启动成功 pid=22048"),
    ("March7thAssistant", "info", "开始每日任务：清体力"),
    ("BAAH", "info", "启动成功 pid=10492"),
    ("BAAH", "info", "结算完成，退出码 0"),
    ("BetterGI", "error", "识别超时，等待修复引擎介入"),
    ("core", "warn", "BetterGI 失败，尝试 2/3"),
] * 12


class FakeSupervisor:
    def spawn_project_gui(self, key):
        return None


def _noop(*args, **kwargs):
    pass


def build_overview():
    overview = QWidget()
    overview.setObjectName("central")
    root = QVBoxLayout(overview)
    root.setContentsMargins(14, 12, 14, 10)
    root.setSpacing(12)

    header = QFrame()
    header.setObjectName("header")
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(18, 14, 18, 12)
    header_layout.setSpacing(10)

    top = QHBoxLayout()
    top.setSpacing(12)
    logo = QLabel()
    logo.setPixmap(gui_main.make_icon().pixmap(40, 40))
    title_box = QVBoxLayout()
    title_box.setSpacing(0)
    title = QLabel("Auto-AFK")
    title.setObjectName("title")
    subtitle = QLabel("一键挂机 · 三月七小助手 + BAAH + MaaEnd 统一编排")
    subtitle.setObjectName("subtitle")
    title_box.addWidget(title)
    title_box.addWidget(subtitle)
    top.addWidget(logo)
    top.addLayout(title_box)
    top.addStretch(1)
    chain_pill = AnimatedPill("空闲", gui_main.CHAIN_COLORS["idle"])
    chain_btn = AnimatedButton("一键挂机", variant="primary")
    chain_btn.setObjectName("primary")
    add_shadow(chain_btn, color="#3d7dfa", alpha=120, blur=18, dy=3)
    stop_all_btn = AnimatedButton("全部停止", variant="danger")
    stop_all_btn.setObjectName("danger")
    top.addWidget(chain_pill)
    top.addWidget(chain_btn)
    top.addWidget(stop_all_btn)
    header_layout.addLayout(top)

    controls = QHBoxLayout()
    controls.setSpacing(8)
    for text in ("环境自检", "校验补丁", "打开日志", "打开配置", "立即更新", "更新MuMu"):
        controls.addWidget(AnimatedButton(text))
    controls.addStretch(1)
    pre_run_check = QCheckBox("运行前更新")
    pre_run_check.setChecked(True)
    schedule_check = QCheckBox("定时启动")
    schedule_check.setChecked(True)
    schedule_time = QTimeEdit()
    schedule_time.setDisplayFormat("HH:mm")
    controls.addWidget(pre_run_check)
    controls.addWidget(schedule_check)
    controls.addWidget(schedule_time)
    header_layout.addLayout(controls)
    root.addWidget(header)

    cards_title = QLabel("项目状态")
    cards_title.setObjectName("sectionTitle")
    root.addWidget(cards_title)
    cards_row = FlowLayout(h_spacing=12, v_spacing=12)
    cards = []
    for key, spec in FAKE_PROJECTS.items():
        card = gui_main.ProjectCard(
            key, spec, gui_main.PROJECT_COLORS.get(key, "#4fc3f7"),
            _noop, _noop, _noop, on_gui=_noop,
        )
        card.update_state(FAKE_RUNS.get(key))
        cards.append(card)
        cards_row.addWidget(card)
    root.addLayout(cards_row)

    splitter = QSplitter(Qt.Vertical)
    splitter.setChildrenCollapsible(False)
    tabs = AnimatedTabWidget()

    log_tab = QWidget()
    log_layout = QVBoxLayout(log_tab)
    log_layout.setContentsMargins(10, 10, 10, 10)
    log_layout.setSpacing(8)
    log_controls = QHBoxLayout()
    log_controls.setSpacing(8)
    filter_combo = QComboBox()
    filter_combo.addItem("全部项目", "")
    for key in FAKE_PROJECTS:
        filter_combo.addItem(key, key)
    filter_combo.addItem("core", "core")
    autoscroll_check = QCheckBox("自动滚动")
    autoscroll_check.setChecked(True)
    clear_btn = AnimatedButton("清空显示")
    log_controls.addWidget(QLabel("过滤:"))
    log_controls.addWidget(filter_combo)
    log_controls.addWidget(autoscroll_check)
    log_controls.addWidget(clear_btn)
    log_controls.addStretch(1)
    log_view = QPlainTextEdit()
    log_view.setReadOnly(True)
    log_view.setFont(QFont("Consolas", 9))
    enable_smooth_scroll(log_view)
    log_layout.addLayout(log_controls)
    log_layout.addWidget(log_view, 1)
    tabs.addTab(log_tab, "实时日志")

    reports_tab = QWidget()
    reports_layout = QVBoxLayout(reports_tab)
    reports_layout.setContentsMargins(10, 10, 10, 10)
    report_controls = QHBoxLayout()
    report_controls.addWidget(AnimatedButton("刷新"))
    report_controls.addWidget(AnimatedButton("打开选中报告"))
    report_controls.addStretch(1)
    report_list = QListWidget()
    enable_smooth_scroll(report_list)
    for _ in range(8):
        report_list.addItem(QListWidgetItem("2026-10-03_detect-fail_1551  [BetterGI]  连续 3 次识别超时"))
    reports_layout.addLayout(report_controls)
    reports_layout.addWidget(report_list, 1)
    tabs.addTab(reports_tab, "错误报告")

    splitter.addWidget(tabs)
    root.addWidget(splitter, 1)
    return overview, tabs, cards, chain_pill, log_view


def populate_log(log_view):
    for project, level, message in FAKE_LOG[:60]:
        ts = time.strftime("%H:%M:%S")
        project_color = gui_main.PROJECT_COLORS.get(project, "#4fc3f7")
        if level == "error":
            message_color = gui_main.LEVEL_COLORS["error"]
        elif level == "warn":
            message_color = gui_main.LEVEL_COLORS["warn"]
        else:
            message_color = project_color
        html = '<span style="color:{0}">[{1}] [{2}]</span> <span style="color:{3}">{4}</span>'.format(
            project_color, ts, project, message_color, message
        )
        log_view.appendHtml(html)


def apply_state_shots(tabs, cards, chain_pill):
    bar = tabs.tabBar()
    if bar.count() >= 2:
        start = QRectF(bar.tabRect(0))
        end = QRectF(bar.tabRect(1))
        mid = QRectF(
            (start.left() + end.left()) / 2.0, end.top(),
            (start.width() + end.width()) / 2.0, end.height(),
        )
        bar._indicator = mid
        bar.update()
    for card in cards:
        card._hover = 1.0
        card.update()
    for card in cards:
        if card.dot._pulse:
            card.dot._phase = 0.55
            card.dot.update()
    buttons = card.window().findChildren(AnimatedButton)
    header_btns = [b for b in buttons if b.text() in ("一键挂机", "全部停止")]
    if header_btns:
        b = header_btns[0]
        b._hover = 1.0
        b._press = 0.6
        b._ripple = 0.45
        b._ripple_center = QPointF(b.width() * 0.5, b.height() * 0.6)
        b.update()
    if len(header_btns) > 1:
        d = header_btns[1]
        d._hover = 1.0
        d.update()
    chain_pill.set_state("运行中", gui_main.CHAIN_COLORS["running"])


def build_shell():
    central = QWidget()
    central.setObjectName("central")
    central.resize(1360, 900)
    root = QHBoxLayout(central)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    nav = NavRail()
    nav.add_item("overview", "总览")
    nav.add_item("proj-cap", "项目界面", section=True)
    for key, spec in FAKE_PROJECTS.items():
        nav.add_item("page:" + key, str(spec.get("display_name", key)).split("（")[0])
    nav.add_item("sys-cap", "系统", section=True)
    nav.add_item("settings", "设置")
    nav.finish()
    # 真实 MainWindow 在 _build_ui 末尾会 set_active("overview")；预览若跳过这一步，
    # 就没有任何导航项被选中，左侧高亮条也不会落位（此前正是这块未定位的
    # 指示条在左上角画出一块蓝色，压在品牌 Logo 上）。
    nav.set_active("overview", animate=False)
    root.addWidget(nav)

    stack = QWidget()
    from PySide6.QtWidgets import QStackedWidget

    stack = QStackedWidget()
    root.addWidget(stack, 1)

    overview, tabs, cards, chain_pill, log_view = build_overview()
    populate_log(log_view)
    stack.addWidget(overview)

    pages = {}
    for i, (key, spec) in enumerate(FAKE_PROJECTS.items()):
        page = ProjectPage(key, spec, gui_main.PROJECT_COLORS.get(key, "#4fc3f7"), FakeSupervisor(), _noop)
        if i == 0:
            page._set_state(ProjectPage.STATE_STARTING, "正在启动界面进程...", pulse=True)
        pages[key] = page
        stack.addWidget(page)

    settings_page = SettingsPage(log_fn=_noop, registry=FAKE_REGISTRY)
    stack.addWidget(settings_page)

    nav.navigate.connect(lambda key: _navigate(stack, nav, key, pages))
    return central, stack, nav, tabs, cards, chain_pill, pages, settings_page


def _navigate(stack, nav, key, pages):
    if key == "overview":
        page = stack.widget(0)
    elif key == "settings":
        page = stack.widget(stack.count() - 1)
    elif key.startswith("page:"):
        page = pages.get(key[5:])
    else:
        return
    if page is None:
        return
    stack.setCurrentWidget(page)
    nav.set_active(key, animate=False)


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyleSheet(gui_main.APP_QSS)
    apply_palette(app)

    window, stack, nav, tabs, cards, chain_pill, pages, settings_page = build_shell()
    window.show()

    def shot_overview():
        window.grab().save(str(out_dir / "ui_preview_default.png"))
        apply_state_shots(tabs, cards, chain_pill)
        QTimer.singleShot(80, shot_states)

    def shot_states():
        window.grab().save(str(out_dir / "ui_preview_states.png"))
        nav.navigate.emit("page:BetterGI")
        QTimer.singleShot(80, shot_project)

    def shot_project():
        window.grab().save(str(out_dir / "ui_preview_project.png"))
        nav.navigate.emit("settings")
        QTimer.singleShot(80, shot_settings)

    def shot_settings():
        window.grab().save(str(out_dir / "ui_preview_settings.png"))
        app.quit()

    QTimer.singleShot(120, shot_overview)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
