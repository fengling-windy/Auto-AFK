import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from afk_core import autostart, config, doctor, idle, report
from afk_core.events import EventBus
from afk_core.idle import IdleWatcher
from afk_core.repair import RepairEngine
from afk_core.scheduler import Scheduler
from afk_core.supervisor import Supervisor

from gui.modern import (
    APP_QSS,
    AnimatedButton,
    AnimatedPill,
    AnimatedTabWidget,
    HoverGlowCard,
    StatusDot,
    add_shadow,
    apply_palette,
    enable_smooth_scroll,
    fade_in_widget,
    fade_in_window,
)
from gui.idle_prompt import (
    DECISION_ALL,
    DECISION_BACKGROUND,
    DECISION_STOP,
    IdlePromptDialog,
)
from gui.nav_rail import NavRail
from gui.project_page import ProjectPage
from gui.settings_page import SettingsPage
from gui.widget import DesktopWidget

STATUS_COLORS = {
    "success": "#34d399",
    "success_unverified": "#a3e635",
    "running": "#60a5fa",
    "pending": "#7d879c",
    "failed": "#f87171",
    "hang": "#fb923c",
    "stopped": "#c084fc",
}

STATUS_TEXT = {
    "success": "完成",
    "success_unverified": "完成(未校验)",
    "running": "运行中",
    "pending": "等待",
    "failed": "失败",
    "hang": "卡死",
    "stopped": "已停止",
}

PROJECT_COLORS = {
    "March7thAssistant": "#4fc3f7",
    "BAAH": "#81c784",
    "MaaEnd": "#ffb74d",
    "ZenlessZoneZero-OneDragon": "#b388ff",
    "BetterGI": "#ffd54f",
    "core": "#ce93d8",
    "emulator": "#4db6ac",
    "update": "#90a4ae",
}

DEFAULT_PALETTE = [
    "#4fc3f7",
    "#81c784",
    "#ffb74d",
    "#ce93d8",
    "#4db6ac",
    "#f06292",
    "#aed581",
    "#7986cb",
]

LEVEL_COLORS = {
    "error": "#f87171",
    "warn": "#fbbf24",
}

CHAIN_TEXT = {
    "idle": "空闲",
    "running": "运行中",
    "success": "全部完成",
    "failed": "有失败",
    "stopped": "已停止",
}

CHAIN_COLORS = {
    "idle": "#7d879c",
    "running": "#60a5fa",
    "success": "#34d399",
    "failed": "#f87171",
    "stopped": "#c084fc",
}


def make_icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#10141d"))
    painter.drawRoundedRect(0, 0, 64, 64, 14, 14)
    painter.setBrush(QColor("#3d7dfa"))
    painter.drawEllipse(8, 8, 48, 48)
    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("Arial", 17, QFont.Bold))
    painter.drawText(pix.rect(), Qt.AlignCenter, "AFK")
    painter.end()
    return QIcon(pix)


class ProjectCard(HoverGlowCard):
    def __init__(self, key, spec, color, on_start, on_stop, on_logs, on_gui=None):
        super().__init__()
        self.key = key
        self.color = color
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_logs = on_logs
        self.on_gui = on_gui
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        accent = QFrame()
        accent.setFixedHeight(4)
        accent.setStyleSheet("background: {0}; border-radius: 2px;".format(color))
        layout.addWidget(accent)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.dot = StatusDot(STATUS_COLORS["pending"])
        display_name = str(spec.get("display_name", key))
        self.name_label = QLabel(display_name.split("（")[0])
        self.name_label.setObjectName("cardName")
        if display_name != self.name_label.text():
            self.name_label.setToolTip(display_name)
        head.addWidget(self.dot)
        head.addWidget(self.name_label, 1)
        layout.addLayout(head)

        self.status_label = QLabel("等待")
        self.status_label.setObjectName("cardStatus")
        self.status_label.setStyleSheet("color: {0};".format(STATUS_COLORS["pending"]))
        layout.addWidget(self.status_label)

        self.detail_label = QLabel("未运行")
        self.detail_label.setObjectName("cardDetail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumHeight(30)
        layout.addWidget(self.detail_label)

        self.attempt_label = QLabel("")
        self.attempt_label.setObjectName("cardAttempt")
        layout.addWidget(self.attempt_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.gui_btn = AnimatedButton("界面", variant="mini")
        self.start_btn = AnimatedButton("启动", variant="mini")
        self.stop_btn = AnimatedButton("停止", variant="mini")
        self.logs_btn = AnimatedButton("日志", variant="mini")
        if self.on_gui is not None:

            def _gui_clicked():
                # 防抖：点击后短暂禁用，避免双击拉起两个实例
                self.gui_btn.setEnabled(False)
                self.on_gui(self.key)
                QTimer.singleShot(1500, lambda: self.gui_btn.setEnabled(True))

            self.gui_btn.clicked.connect(_gui_clicked)
        else:
            self.gui_btn.setVisible(False)
        self.start_btn.clicked.connect(lambda: self.on_start(self.key))
        self.stop_btn.clicked.connect(lambda: self.on_stop(self.key))
        self.logs_btn.clicked.connect(lambda: self.on_logs(self.key))
        buttons.addWidget(self.gui_btn)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.logs_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def update_state(self, run):
        status = run.get("status", "pending") if run else "pending"
        color = STATUS_COLORS.get(status, STATUS_COLORS["pending"])
        # 运行中/卡死时状态点呼吸脉冲（卡死闪得更快）
        period = 1.1 if status == "hang" else 1.6
        self.dot.set_state(color, pulse=status in ("running", "hang"), period=period)
        self.status_label.setText(STATUS_TEXT.get(status, status))
        self.status_label.setStyleSheet("color: {0};".format(color))
        if run is None:
            self.detail_label.setText("未运行")
            self.attempt_label.setText("")
            return
        parts = []
        if run.get("pid"):
            parts.append("pid={0}".format(run["pid"]))
        if run.get("exit_code") is not None:
            parts.append("exit={0}".format(run["exit_code"]))
        if run.get("last_message"):
            parts.append(run["last_message"][:110])
        self.detail_label.setText(" | ".join(parts) if parts else "运行中...")
        self.attempt_label.setText("尝试 {0} 次".format(run.get("attempt", 1)))


class MainWindow(QMainWindow):
    doctor_result = Signal(list)
    # IdleWatcher 在后台线程回调，必须用 Signal 把「该弹提示框了」投递回 GUI 线程：
    # Qt 的跨线程 signal/slot 会自动排队到接收者所在线程执行，而 QTimer.singleShot
    # 在「没有事件循环的线程」里创建的定时器永远不会触发（这正是空闲挂机空转的根因）。
    idle_triggered = Signal(float)

    def __init__(self):
        super().__init__()
        self.settings = config.settings()
        self.auto_run_requested = "--autorun" in sys.argv
        self.start_in_tray = "--tray" in sys.argv
        self.no_widget = "--no-widget" in sys.argv
        custom_colors = (self.settings.get("gui") or {}).get("project_colors") or {}
        self.project_colors = dict(PROJECT_COLORS)
        self.project_colors.update({str(k): str(v) for k, v in custom_colors.items()})
        self.setWindowTitle("Auto-AFK 一键挂机")
        self.resize(1360, 900)
        self.setWindowIcon(make_icon())

        self.bus = EventBus()
        self.bus.start_server()
        self.repair = RepairEngine(self.bus, self.settings)
        self.supervisor = Supervisor(self.bus, settings=self.settings, repair=self.repair, on_change=self._on_change)
        self.cards = {}
        self._log_line_count = 0
        self._did_intro = False
        self._last_chain_for_completion = None
        self.event_buffer = deque(maxlen=5000)
        self._build_ui()
        self._build_tray()

        self.queue = self.bus.subscribe()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(300)

        self.scheduler = Scheduler(config.settings, self.start_chain, self.bus)
        self.scheduler.start()

        # 空闲自动挂机：全局无鼠标键盘输入达到 idle.minutes 时弹提示框
        self._idle_dialog = None
        self.idle_triggered.connect(self._prompt_idle)
        self.idle_watcher = IdleWatcher(
            config.settings,
            self._on_idle_trigger,
            self.bus,
            is_busy=self._chain_is_busy,
            is_done_today=self._is_done_today,
        )
        self.idle_watcher.start()

        # 桌面小组件
        self.widget = None
        if not self.no_widget:
            self._build_widget()

        self.doctor_result.connect(self._render_doctor)
        try:
            from afk_core.housekeeping import cleanup_logs

            threading.Thread(
                target=lambda: cleanup_logs(self.bus, config.settings()),
                daemon=True,
                name="afk-housekeeping",
            ).start()
        except Exception:
            pass

        # 刚启动就自动更新全部项目（pre_run.at_startup，默认开）
        try:
            if self.supervisor.run_startup_updates():
                self._append_log("core", "info", "启动更新：正在后台更新全部项目...")
        except Exception:
            pass

        if self.auto_run_requested:
            self._append_log("core", "info", "--autorun 已启用，2 秒后自动开始一键挂机")
            QTimer.singleShot(2000, self.start_chain)
        elif (self.settings.get("gui", {}) or {}).get("autostart_chain_on_launch"):
            QTimer.singleShot(2000, self.start_chain)

        # 静默启动勾选的项目界面（隐藏到后台，进入对应导航页时嵌入显示）
        silent_keys = [
            str(k) for k in ((self.settings.get("gui") or {}).get("silent_open_projects") or [])
            if k in self.project_pages
        ]
        if silent_keys:
            self._append_log("core", "info", "静默启动项目界面: {0}".format(", ".join(silent_keys)))
            QTimer.singleShot(3000, lambda: self._silent_open_pages(silent_keys))

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # -- 左侧导航栏 --------------------------------------------------------
        self.nav = NavRail()
        self.nav.add_item("overview", "总览")
        self._nav_page_keys = []
        for key, spec in (config.registry().get("projects", {}) or {}).items():
            if spec.get("gui"):
                nav_key = "page:" + key
                self._nav_page_keys.append((key, nav_key))
                self.nav.add_item(nav_key, str(spec.get("display_name", key)).split("（")[0])
        self.nav.add_item("settings", "设置")
        self.nav.finish()
        self.nav.navigate.connect(self._navigate)
        root_layout.addWidget(self.nav)

        # -- 右侧页面堆栈 --------------------------------------------------------
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, 1)

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
        logo.setPixmap(make_icon().pixmap(40, 40))
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
        self.chain_pill = AnimatedPill("空闲", CHAIN_COLORS["idle"])
        self.chain_btn = AnimatedButton("一键挂机", variant="primary")
        self.chain_btn.setObjectName("primary")
        add_shadow(self.chain_btn, color="#3d7dfa", alpha=120, blur=18, dy=3)
        self.stop_all_btn = AnimatedButton("全部停止", variant="danger")
        self.stop_all_btn.setObjectName("danger")
        top.addWidget(self.chain_pill)
        top.addWidget(self.chain_btn)
        top.addWidget(self.stop_all_btn)
        header_layout.addLayout(top)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.doctor_btn = AnimatedButton("环境自检")
        self.patch_btn = AnimatedButton("校验补丁")
        self.open_logs_btn = AnimatedButton("打开日志")
        self.open_configs_btn = AnimatedButton("打开配置")
        self.update_btn = AnimatedButton("立即更新")
        self.mumu_btn = AnimatedButton("更新MuMu")
        for widget in (
            self.doctor_btn,
            self.patch_btn,
            self.open_logs_btn,
            self.open_configs_btn,
            self.update_btn,
            self.mumu_btn,
        ):
            controls.addWidget(widget)
        controls.addStretch(1)
        self.pre_run_check = QCheckBox("运行前更新")
        pre_cfg = self.settings.get("pre_run", {}) or {}
        self.pre_run_check.setChecked(bool(pre_cfg.get("enabled", True)))
        self.pre_run_check.toggled.connect(self._toggle_pre_run)
        self.schedule_check = QCheckBox("定时启动")
        self.schedule_time = QTimeEdit()
        self.schedule_time.setDisplayFormat("HH:mm")
        schedule_cfg = self.settings.get("schedule", {}) or {}
        self.schedule_check.setChecked(bool(schedule_cfg.get("enabled")))
        try:
            hour, minute = [int(x) for x in str(schedule_cfg.get("time", "04:00")).split(":")[:2]]
            self.schedule_time.setTime(QTime(hour, minute))
        except Exception:
            self.schedule_time.setTime(QTime(4, 0))
        self.schedule_check.toggled.connect(self._toggle_schedule)
        self.schedule_time.timeChanged.connect(self._toggle_schedule)
        controls.addWidget(self.pre_run_check)
        controls.addWidget(self.schedule_check)
        controls.addWidget(self.schedule_time)
        header_layout.addLayout(controls)
        root.addWidget(header)

        cards_title = QLabel("项目状态")
        cards_title.setObjectName("sectionTitle")
        root.addWidget(cards_title)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        for key, spec in (config.registry().get("projects", {}) or {}).items():
            card = ProjectCard(
                key, spec, self._project_color(key),
                self.start_project, self.stop_project, self.show_project_logs,
                on_gui=self.open_project_page if spec.get("gui") else None,
            )
            self.cards[key] = card
            cards_row.addWidget(card)
        root.addLayout(cards_row)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        self.tabs = AnimatedTabWidget()

        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(10, 10, 10, 10)
        log_layout.setSpacing(8)
        log_controls = QHBoxLayout()
        log_controls.setSpacing(8)
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("全部项目", "")
        for key in (config.registry().get("projects", {}) or {}).keys():
            self.filter_combo.addItem(key, key)
        self.filter_combo.addItem("core", "core")
        self.filter_combo.currentIndexChanged.connect(self._rerender_log)
        self.autoscroll_check = QCheckBox("自动滚动")
        self.autoscroll_check.setChecked(True)
        clear_btn = AnimatedButton("清空显示")
        clear_btn.clicked.connect(self._clear_log)
        log_controls.addWidget(QLabel("过滤:"))
        log_controls.addWidget(self.filter_combo)
        log_controls.addWidget(self.autoscroll_check)
        log_controls.addWidget(clear_btn)
        log_controls.addStretch(1)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(8000)
        self.log_view.setFont(QFont("Consolas", 9))
        enable_smooth_scroll(self.log_view)
        log_layout.addLayout(log_controls)
        log_layout.addWidget(self.log_view)
        self.tabs.addTab(log_tab, "实时日志")

        reports_tab = QWidget()
        reports_layout = QVBoxLayout(reports_tab)
        reports_layout.setContentsMargins(10, 10, 10, 10)
        report_controls = QHBoxLayout()
        refresh_reports = AnimatedButton("刷新")
        refresh_reports.clicked.connect(self.refresh_reports)
        open_report = AnimatedButton("打开选中报告")
        open_report.clicked.connect(self.open_selected_report)
        report_controls.addWidget(refresh_reports)
        report_controls.addWidget(open_report)
        report_controls.addStretch(1)
        self.report_list = QListWidget()
        enable_smooth_scroll(self.report_list)
        reports_layout.addLayout(report_controls)
        reports_layout.addWidget(self.report_list)
        self.tabs.addTab(reports_tab, "错误报告")

        self.registry_edit = QPlainTextEdit()
        self._load_text(self.registry_edit, config.PROJECTS_YAML)
        enable_smooth_scroll(self.registry_edit)
        registry_tab = self._file_tab(self.registry_edit, config.PROJECTS_YAML, "保存项目注册表")
        self.tabs.addTab(registry_tab, "项目注册表")

        self.settings_edit = QPlainTextEdit()
        self._load_text(self.settings_edit, config.SETTINGS_YAML)
        enable_smooth_scroll(self.settings_edit)
        settings_tab = self._file_tab(self.settings_edit, config.SETTINGS_YAML, "保存设置")
        self.tabs.addTab(settings_tab, "设置")

        self.repair_edit = QPlainTextEdit()
        self._load_text(self.repair_edit, config.REPAIR_YAML)
        enable_smooth_scroll(self.repair_edit)
        repair_tab = self._file_tab(self.repair_edit, config.REPAIR_YAML, "保存修复规则")
        self.tabs.addTab(repair_tab, "修复规则")

        doctor_tab = QWidget()
        doctor_layout = QVBoxLayout(doctor_tab)
        doctor_layout.setContentsMargins(10, 10, 10, 10)
        doctor_controls = QHBoxLayout()
        run_doctor = AnimatedButton("运行自检")
        run_doctor.clicked.connect(self.run_doctor)
        doctor_controls.addWidget(run_doctor)
        doctor_controls.addStretch(1)
        self.doctor_view = QPlainTextEdit()
        self.doctor_view.setReadOnly(True)
        self.doctor_view.setFont(QFont("Consolas", 9))
        enable_smooth_scroll(self.doctor_view)
        doctor_layout.addLayout(doctor_controls)
        doctor_layout.addWidget(self.doctor_view)
        self.tabs.addTab(doctor_tab, "环境自检")

        splitter.addWidget(self.tabs)
        root.addWidget(splitter, 1)

        self.stack.addWidget(overview)  # index 0 = 总览

        # -- 项目界面嵌入页 --------------------------------------------------------
        self.project_pages = {}
        for key, spec in (config.registry().get("projects", {}) or {}).items():
            if not spec.get("gui"):
                continue
            page = ProjectPage(
                key, spec, self._project_color(key),
                self.supervisor, self._append_log,
            )
            self.project_pages[key] = page
            self.stack.addWidget(page)

        # -- 设置页 ----------------------------------------------------------------
        self.settings_page = SettingsPage(log_fn=self._append_log)
        self.settings_page.changed.connect(self._on_settings_changed)
        self.stack.addWidget(self.settings_page)

        self.nav.set_active("overview", animate=False)
        self.setCentralWidget(central)

        self.chain_btn.clicked.connect(self.start_chain)
        self.stop_all_btn.clicked.connect(self.supervisor.stop_all)
        self.doctor_btn.clicked.connect(self.run_doctor)
        self.patch_btn.clicked.connect(self.verify_patches)
        self.open_logs_btn.clicked.connect(lambda: self._open_path(config.LOGS_DIR))
        self.open_configs_btn.clicked.connect(lambda: self._open_path(config.ROOT / "configs"))
        self.update_btn.clicked.connect(self.run_updates)
        self.mumu_btn.clicked.connect(self.run_mumu_updater)

    def _navigate(self, key):
        """导航栏切换页面（不自动启动项目界面，需点击页内「启动界面」）。"""
        page = None
        if key == "overview":
            page = self.stack.widget(0)
        elif key == "settings":
            self.settings_page.reload_from_disk()
            page = self.settings_page
        elif key.startswith("page:"):
            page = self.project_pages.get(key[5:])
            if page is not None and page._state == ProjectPage.STATE_HIDDEN:
                # 静默启动的界面：进入页面时嵌入显示
                page.ensure_started()
        else:
            return
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.nav.set_active(key, animate=False)
        if not key.startswith("page:"):
            # 含原生窗口容器（外部窗口）的页面不能用透明度特效
            fade_in_widget(page, duration=150)

    def open_project_page(self, key):
        """项目卡片「界面」按钮 → 切到嵌入页。"""
        if key in self.project_pages:
            self._navigate("page:" + key)
        else:
            threading.Thread(target=self.supervisor.open_project_gui, args=(key,), daemon=True, name="afk-gui-" + key).start()

    def _on_settings_changed(self):
        self.settings = config.settings()
        self.supervisor.settings = self.settings
        if self.widget is not None:
            gui_cfg = self.settings.get("gui") or {}
            want = bool(gui_cfg.get("widget_enabled", False))
            if self.widget.isVisible() != want:
                self.widget.setVisible(want)
        # 托盘勾选项跟随设置页变化（阻塞信号避免回写）
        idle_cfg = self.settings.get("idle") or {}
        for action, want in (
            (getattr(self, "widget_action", None), bool((self.settings.get("gui") or {}).get("widget_enabled", False))),
            (getattr(self, "idle_action", None), bool(idle_cfg.get("enabled", False))),
        ):
            if action is None or action.isChecked() == want:
                continue
            action.blockSignals(True)
            action.setChecked(want)
            action.blockSignals(False)

    def _silent_open_pages(self, keys):
        for key in keys:
            page = self.project_pages.get(key)
            if page is not None:
                page.ensure_started(silent=True)

    def _file_tab(self, edit, path, button_text):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        controls = QHBoxLayout()
        save = AnimatedButton(button_text, variant="primary")
        save.setObjectName("primary")
        save.clicked.connect(lambda: self._save_text(edit, path))
        reload_btn = AnimatedButton("重新加载")
        reload_btn.clicked.connect(lambda: self._load_text(edit, path))
        open_btn = AnimatedButton("打开所在目录")
        open_btn.clicked.connect(lambda: self._open_path(path.parent))
        controls.addWidget(save)
        controls.addWidget(reload_btn)
        controls.addWidget(open_btn)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(edit)
        return tab

    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.showNormal)
        chain_action = QAction("一键挂机", self)
        chain_action.triggered.connect(self.start_chain)
        self.bg_chain_action = QAction("仅运行不占用鼠标任务", self)
        self.bg_chain_action.triggered.connect(self.start_chain_background)
        stop_action = QAction("全部停止", self)
        stop_action.triggered.connect(self.supervisor.stop_all)
        self.widget_action = QAction("桌面小组件", self)
        self.widget_action.setCheckable(True)
        self.widget_action.setChecked(bool((self.settings.get("gui") or {}).get("widget_enabled", False)))
        self.widget_action.toggled.connect(self.toggle_widget)
        self.idle_action = QAction("空闲自动挂机", self)
        self.idle_action.setCheckable(True)
        self.idle_action.setChecked(bool((self.settings.get("idle") or {}).get("enabled", False)))
        self.idle_action.toggled.connect(self._toggle_idle_enabled)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(show_action)
        menu.addAction(chain_action)
        menu.addAction(self.bg_chain_action)
        menu.addAction(stop_action)
        menu.addSeparator()
        menu.addAction(self.widget_action)
        menu.addAction(self.idle_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.activateWindow()

    def _open_path(self, path):
        path = Path(path)
        if path.exists():
            os.startfile(str(path))
        else:
            QMessageBox.warning(self, "不存在", "路径不存在: {0}".format(path))

    def _load_text(self, edit, path):
        try:
            edit.setPlainText(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            edit.setPlainText("# 读取失败: {0}".format(exc))

    def _save_text(self, edit, path):
        text = edit.toPlainText()
        if str(path).lower().endswith((".yaml", ".yml")):
            try:
                yaml.safe_load(text)
            except Exception as exc:
                QMessageBox.critical(self, "YAML 校验失败", "配置未保存，请先修正语法错误：\n\n{0}".format(exc))
                return
        try:
            Path(path).write_text(text, encoding="utf-8")
            self._append_log("core", "info", "已保存 {0}".format(path))
            if Path(path) == Path(config.SETTINGS_YAML):
                self.settings = config.settings()
                self.supervisor.settings = self.settings
                if getattr(self, "settings_page", None) is not None:
                    self.settings_page.reload_from_disk()
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def _toggle_schedule(self, *_):
        schedule = self.settings.setdefault("schedule", {})
        schedule["enabled"] = bool(self.schedule_check.isChecked())
        schedule["time"] = self.schedule_time.time().toString("HH:mm")
        config.save_yaml(config.SETTINGS_YAML, self.settings)
        self.settings = config.settings()
        self._append_log("core", "info", "定时启动: {0} {1}".format("开" if schedule["enabled"] else "关", schedule["time"]))

    def _toggle_pre_run(self, checked):
        pre = self.settings.setdefault("pre_run", {})
        pre["enabled"] = bool(checked)
        config.save_yaml(config.SETTINGS_YAML, self.settings)
        self.supervisor.settings = config.settings()
        self._append_log("core", "info", "运行前更新: {0}".format("开" if checked else "关"))

    def run_updates(self):
        from afk_core.emulator import MuMuController
        from afk_core.updater import Updater

        def worker():
            settings = config.settings()
            pre = settings.get("pre_run") or {}
            if pre.get("update_emulator", True):
                MuMuController(self.bus, settings).update_check()
            if pre.get("update_projects", True):
                Updater(self.bus, settings).update_all_projects()

        self._append_log("core", "info", "开始手动更新（项目 + 模拟器）...")
        threading.Thread(target=worker, daemon=True, name="afk-manual-update").start()

    def run_mumu_updater(self):
        from afk_core.emulator import MuMuController

        def worker():
            settings = config.settings()
            emu_cfg = settings.setdefault("pre_run", {}).setdefault("emulator", {})
            emu_cfg["updater_mode"] = "gui"
            MuMuController(self.bus, settings).update_check()

        self._append_log("core", "info", "启动 MuMu 更新器...")
        threading.Thread(target=worker, daemon=True, name="afk-mumu-update").start()

    def start_chain(self):
        self.supervisor.settings = config.settings()
        self.supervisor.registry = config.registry()
        self.settings = self.supervisor.settings
        self.supervisor.run_chain()

    def start_chain_background(self):
        self.supervisor.settings = config.settings()
        self.supervisor.registry = config.registry()
        self.settings = self.supervisor.settings
        self.supervisor.start_chain(background_only=True)

    def start_project(self, key):
        threading.Thread(target=self.supervisor.start_project, args=(key,), daemon=True, name="afk-start-" + key).start()

    def stop_project(self, key):
        threading.Thread(target=self.supervisor.stop_project, args=(key,), daemon=True, name="afk-stop-" + key).start()

    def show_project_logs(self, key):
        self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(key)))
        self.tabs.setCurrentIndex(0)
        self.stack.setCurrentWidget(self.stack.widget(0))
        self.nav.set_active("overview", animate=False)

    def _clear_log(self):
        self.log_view.clear()
        self._log_line_count = 0

    def _project_color(self, project):
        colors = getattr(self, "project_colors", PROJECT_COLORS)
        if project in colors:
            return colors[project]
        if project in PROJECT_COLORS:
            return PROJECT_COLORS[project]
        index = sum(ord(ch) for ch in str(project)) % len(DEFAULT_PALETTE)
        return DEFAULT_PALETTE[index]

    def _append_log(self, project, level, message, autoscroll=True):
        ts = time.strftime("%H:%M:%S")
        project_color = self._project_color(project)
        if level == "error":
            message_color = LEVEL_COLORS["error"]
        elif level == "warn":
            message_color = LEVEL_COLORS["warn"]
        else:
            message_color = project_color
        html = '<span style="color:{0}">[{1}] [{2}]</span> <span style="color:{3}">{4}</span>'.format(
            project_color, ts, _escape_html(str(project)), message_color, _escape_html(str(message))
        )
        self.log_view.appendHtml(html)
        self._log_line_count += 1
        if autoscroll and self.autoscroll_check.isChecked():
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _matches_filter(self, event, filter_project):
        if not filter_project:
            return True
        return event.get("project") == filter_project

    def _rerender_log(self):
        self.log_view.clear()
        filter_project = self.filter_combo.currentData() or ""
        for event in self.event_buffer:
            if self._matches_filter(event, filter_project):
                self._append_log(event.get("project", "core"), event.get("level", "info"), event.get("message", ""), autoscroll=False)
        self._log_line_count = len(self.event_buffer)
        if self.autoscroll_check.isChecked():
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _poll(self):
        filter_project = self.filter_combo.currentData() or ""
        drained = 0
        while drained < 500:
            try:
                event = self.queue.get_nowait()
            except Exception:
                break
            drained += 1
            self.event_buffer.append(event)
            if event.get("kind") == "post_action" and (event.get("data") or {}).get("action") == "exit":
                self._append_log("core", "info", "结束后操作：关闭 Auto-AFK")
                QTimer.singleShot(800, self._quit)
                continue
            if self._matches_filter(event, filter_project):
                self._append_log(event.get("project", "core"), event.get("level", "info"), event.get("message", ""), autoscroll=False)
        if drained and self.autoscroll_check.isChecked():
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        snapshot = self.supervisor.status_snapshot()
        for key, card in self.cards.items():
            card.update_state(snapshot["runs"].get(key))
        # 导航栏指示灯：绿=界面已启动（启动中/已嵌入/后台），灰=未启动
        for key, nav_key in getattr(self, "_nav_page_keys", []):
            item = self.nav.item_for_key(nav_key)
            page = self.project_pages.get(key)
            if item is not None and page is not None:
                item.set_dot(page.dot_state())
        # 嵌入页存活检测
        for page in getattr(self, "project_pages", {}).values():
            page.tick()
        chain = snapshot["chain_status"]
        text = CHAIN_TEXT.get(chain, chain)
        color = CHAIN_COLORS.get(chain, CHAIN_COLORS["idle"])
        self.chain_pill.set_state(text, color)
        if self.widget is not None and self.widget.isVisible():
            self.widget.tick(
                snapshot,
                self.idle_watcher.status(),
                self.idle_watcher.threshold_seconds(),
                completed_today=self._is_done_today(),
            )
        # 链刚成功结束时刷新设置页的「今日状态」（避免用户看到过期信息）
        if chain != self._last_chain_for_completion:
            self._last_chain_for_completion = chain
            if getattr(self, "settings_page", None) is not None:
                try:
                    self.settings_page._refresh_completion_label()
                except Exception:
                    pass

    def _on_change(self):
        pass

    def refresh_reports(self):
        self.report_list.clear()
        for item in report.list_reports(limit=200):
            entry = "{0}  [{1}] {2}".format(item["name"], item.get("project", ""), item.get("reason", ""))
            list_item = QListWidgetItem(entry)
            list_item.setData(Qt.UserRole, item["dir"])
            self.report_list.addItem(list_item)

    def open_selected_report(self):
        item = self.report_list.currentItem()
        if item is None:
            return
        self._open_path(item.data(Qt.UserRole))

    def run_doctor(self):
        self.doctor_view.setPlainText("环境自检运行中...")
        self.tabs.setCurrentIndex(self.tabs.count() - 1)

        def worker():
            try:
                results = doctor.run_checks()
            except Exception as exc:
                results = [{"status": "fail", "name": "doctor", "detail": str(exc)}]
            self.doctor_result.emit(results)

        threading.Thread(target=worker, daemon=True, name="afk-doctor").start()

    def _chain_is_busy(self):
        """链或任一项目正在跑时，空闲检测不弹提示框。"""
        try:
            return bool(self.supervisor.chain_running())
        except Exception:
            return False

    def _is_done_today(self):
        """今天已成功完成过一轮链 -> 空闲时不再自动执行（可在设置里关掉此行为）。

        这个方法被 _poll（每 ~300ms）调用，因此刻意做两件事避免无谓开销：
          1. 用内存里的 self.settings，而不是 config.settings()（后者每次读 YAML 磁盘）；
          2. 完成标记按 mtime 缓存（见 completion.completed_today_cached），
             标记只在链成功时写一次，没必要每 300ms 重新解析 JSON。
        实测这样把每次调用从 ~3.6ms 降到纳秒级。
        """
        try:
            if not bool((self.settings.get("idle") or {}).get("skip_if_completed_today", True)):
                return False
            from afk_core import completion

            return bool(completion.completed_today_cached())
        except Exception:
            return False

    def _build_widget(self):
        self.widget = DesktopWidget(
            on_chain=self.start_chain,
            on_stop=self.supervisor.stop_all,
            on_show=self._show_main_window,
        )
        self.widget.set_move_handler(self._on_widget_moved)
        gui_cfg = self.settings.get("gui") or {}
        self.widget.restore_position(gui_cfg.get("widget_pos"))
        if bool(gui_cfg.get("widget_enabled", False)):
            self.widget.show()
        self.widget.tick(self.supervisor.status_snapshot(), self.idle_watcher.status(),
                         self.idle_watcher.threshold_seconds())

    def _on_widget_moved(self, pos):
        gui_cfg = self.settings.setdefault("gui", {})
        if list(gui_cfg.get("widget_pos") or []) == list(pos):
            return
        gui_cfg["widget_pos"] = [int(pos[0]), int(pos[1])]
        try:
            config.save_yaml(config.SETTINGS_YAML, self.settings)
        except Exception:
            pass

    def toggle_widget(self, checked=None):
        if self.widget is None:
            return
        show = (not self.widget.isVisible()) if checked is None else bool(checked)
        self.widget.setVisible(show)

        def mutate(settings):
            settings.setdefault("gui", {})["widget_enabled"] = show

        self._mutate_settings(mutate)
        if getattr(self, "settings_page", None) is not None:
            self.settings_page.reload_from_disk()
        self._append_log("core", "info", "桌面小组件: {0}".format("显示" if show else "隐藏"))

    def _show_main_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # -- 空闲自动挂机 ----------------------------------------------------------
    def _on_idle_trigger(self, idle_sec):
        """IdleWatcher 从后台线程回调。

        这里只做「把请求投递给 GUI 线程」这一件事：不再直接碰 log_view（跨线程
        操作 Qt 控件），也不再用该线程的 QTimer（无事件循环，回调不会触发）。
        """
        try:
            self.idle_triggered.emit(float(idle_sec))
        except Exception:
            pass

    def _prompt_idle(self, idle_sec):
        """在 GUI 线程真正弹提示框（由 idle_triggered 信号投递进来）。"""
        self._append_log(
            "core", "info", "空闲 {0}，弹出自动挂机提示框".format(idle.format_idle(idle_sec))
        )
        if self._chain_is_busy():
            self.idle_watcher.rearm()
            return
        if self._idle_dialog is not None and self._idle_dialog.isVisible():
            return
        dialog = IdlePromptDialog(
            idle_text=idle.format_idle(idle_sec),
            prompt_sec=self.idle_watcher.prompt_seconds(),
            parent=self,
        )
        self._idle_dialog = dialog
        self._show_main_window()
        dialog.exec()
        decision = dialog.decision
        self._idle_dialog = None
        self._handle_idle_decision(decision)

    def _handle_idle_decision(self, decision):
        if decision == DECISION_STOP:
            # 用户明确拒绝：重新武装，下次离开后仍会提醒
            self.idle_watcher.rearm()
            self._append_log("core", "info", "空闲提示：用户选择停止，本次不挂机")
            return
        if decision == DECISION_BACKGROUND:
            self._append_log("core", "info", "空闲提示：仅运行不占用鼠标任务")
            self.start_chain_background()
            return
        self._append_log("core", "info", "空闲提示：开始运行全部任务")
        self.start_chain()

    def _toggle_idle_enabled(self, checked):
        def mutate(settings):
            settings.setdefault("idle", {})["enabled"] = bool(checked)

        self._mutate_settings(mutate)
        if checked:
            self.idle_watcher.rearm()
        if getattr(self, "settings_page", None) is not None:
            self.settings_page.reload_from_disk()
        self._append_log(
            "core", "info", "空闲自动挂机: {0}".format("开" if checked else "关")
        )

    def _mutate_settings(self, mutate):
        settings = config.settings()
        mutate(settings)
        config.save_yaml(config.SETTINGS_YAML, settings)
        self.settings = config.settings()
        self.supervisor.settings = self.settings
        return self.settings

    def _render_doctor(self, results):
        lines = []
        summary = doctor.summary(results)
        for item in results:
            lines.append("[{0:<4}] {1:<30} {2}".format(item["status"], item["name"], item.get("detail", "")))
        lines.append("")
        lines.append("ok={ok} warn={warn} fail={fail} total={total}".format(**summary))
        self.doctor_view.setPlainText("\n".join(lines))

    def verify_patches(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("afk_patcher", config.MONITOR_KIT / "patcher.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report_data = module.cmd_verify(config.ROOT)
        lines = []
        for project, results in report_data.items():
            lines.append("[{0}]".format(project))
            for item, state in results:
                lines.append("  {0:<55} {1}".format(item, state))
        QMessageBox.information(self, "补丁状态", "\n".join(lines) or "无补丁定义")

    def _quit(self):
        self.timer.stop()
        if getattr(self, "scheduler", None) is not None:
            self.scheduler.stop()
        if getattr(self, "idle_watcher", None) is not None:
            self.idle_watcher.stop()
        if getattr(self, "widget", None) is not None:
            self.widget.hide()
        # 关闭各项目嵌入/静默启动的界面（窗口 WM_CLOSE + 结束界面进程树）
        for page in getattr(self, "project_pages", {}).values():
            try:
                page.shutdown()
            except Exception:
                pass
        self.bus.unsubscribe(self.queue)
        self.supervisor.stop_all()
        self.bus.stop_server()
        self.tray.hide()
        QApplication.quit()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._did_intro:
            return
        self._did_intro = True
        # 入场动画：窗口整体淡入，各卡片错峰级联淡入
        fade_in_window(self, duration=280)
        for i, card in enumerate(self.cards.values()):
            fade_in_widget(card, duration=300, delay=120 + i * 70)

    def closeEvent(self, event):
        if (self.settings.get("gui", {}) or {}).get("minimize_to_tray", False):
            event.ignore()
            self.hide()
            self.tray.showMessage("Auto-AFK", "已最小化到托盘", QSystemTrayIcon.Information, 2000)
        else:
            self._quit()


def _escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _force_show(window):
    """wscript / uv venv 启动器会把 nCmdShow=SW_HIDE 沿进程链传下来，导致
    Qt 主窗口被创建成隐藏状态（"界面不出现"）；显式 ShowWindow 强制显示。
    按启动链的显示状态覆盖时机带几次重试；只看 Win32 可见性，不触碰已销毁窗口。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        hwnd = int(window.winId())
        user32 = ctypes.windll.user32
    except Exception:
        return

    def _attempt():
        try:
            if not user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL
        except Exception:
            pass

    for delay in (0, 300, 1000, 3000):
        QTimer.singleShot(delay, _attempt)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)
    apply_palette(app)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    if window.start_in_tray:
        window.tray.showMessage("Auto-AFK", "已在托盘启动，自动挂机进行中", QSystemTrayIcon.Information, 3000)
    else:
        window.show()
        _force_show(window)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
