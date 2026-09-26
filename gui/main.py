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
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from afk_core import config, doctor, report
from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.scheduler import Scheduler
from afk_core.supervisor import Supervisor

APP_QSS = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
}
QMainWindow, QWidget#central {
    background: #0e1116;
}
QFrame#header {
    background: #121722;
    border: 1px solid #1f2836;
    border-radius: 16px;
}
QLabel#title {
    font-size: 20px;
    font-weight: 700;
    color: #eef2f9;
}
QLabel#subtitle {
    font-size: 12px;
    color: #7d879c;
}
QLabel#pill {
    background: #1b2333;
    border: 1px solid #2a3547;
    border-radius: 12px;
    padding: 4px 14px;
    color: #9fb0cc;
    font-size: 12px;
    font-weight: 600;
}
QLabel#sectionTitle {
    color: #7d879c;
    font-size: 12px;
    font-weight: 600;
    padding-left: 2px;
}
QFrame#card {
    background: #151b26;
    border: 1px solid #222c3c;
    border-radius: 14px;
}
QFrame#card:hover {
    border: 1px solid #2f4a75;
}
QLabel#cardName {
    font-size: 14px;
    font-weight: 700;
    color: #e8edf7;
}
QLabel#cardStatus {
    font-size: 13px;
    font-weight: 700;
}
QLabel#cardDetail {
    color: #7d879c;
    font-size: 11px;
}
QLabel#cardAttempt {
    color: #5f6a7f;
    font-size: 11px;
}
QPushButton {
    background: #1b2333;
    color: #cfd8ea;
    border: 1px solid #2a3547;
    border-radius: 9px;
    padding: 6px 14px;
    font-size: 12px;
}
QPushButton:hover {
    background: #223049;
    border-color: #3a4d6d;
}
QPushButton:pressed {
    background: #182238;
}
QPushButton:disabled {
    color: #59627a;
    background: #161c28;
}
QPushButton#primary {
    background: #3b82f6;
    border: 1px solid #3b82f6;
    color: #ffffff;
    font-weight: 700;
    padding: 7px 20px;
}
QPushButton#primary:hover {
    background: #4f92ff;
    border-color: #4f92ff;
}
QPushButton#danger {
    background: #2a1c24;
    border: 1px solid #4b2b36;
    color: #ff8a9b;
}
QPushButton#danger:hover {
    background: #3a222d;
}
QPushButton#mini {
    padding: 4px 10px;
    font-size: 11px;
    border-radius: 7px;
}
QTabWidget::pane {
    border: 1px solid #1f2836;
    border-radius: 12px;
    background: #121722;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: #7d879c;
    padding: 9px 18px;
    margin-right: 4px;
    border-bottom: 2px solid transparent;
    font-size: 12px;
    font-weight: 600;
}
QTabBar::tab:hover {
    color: #b9c6de;
}
QTabBar::tab:selected {
    color: #eef2f9;
    border-bottom: 2px solid #3b82f6;
}
QPlainTextEdit, QTextEdit {
    background: #0b0e13;
    border: 1px solid #1f2836;
    border-radius: 10px;
    color: #d7deec;
    selection-background-color: #2b4a7a;
    padding: 6px;
}
QListWidget {
    background: #0b0e13;
    border: 1px solid #1f2836;
    border-radius: 10px;
    color: #cfd8ea;
    padding: 4px;
}
QListWidget::item {
    padding: 7px 8px;
    border-radius: 7px;
}
QListWidget::item:selected {
    background: #1e2c44;
    color: #ffffff;
}
QComboBox, QTimeEdit {
    background: #1b2333;
    border: 1px solid #2a3547;
    border-radius: 8px;
    padding: 5px 10px;
    color: #cfd8ea;
    min-width: 80px;
}
QComboBox::drop-down, QTimeEdit::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background: #151b26;
    border: 1px solid #2a3547;
    color: #cfd8ea;
    selection-background-color: #1e2c44;
    outline: none;
}
QCheckBox {
    color: #9fb0cc;
    font-size: 12px;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #33405a;
    background: #131926;
}
QCheckBox::indicator:checked {
    background: #3b82f6;
    border-color: #3b82f6;
}
QSplitter::handle {
    background: transparent;
    height: 8px;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #2a3547;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #3a4d6d;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #2a3547;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    width: 0;
    background: transparent;
}
QToolTip {
    background: #1b2333;
    color: #dfe6f5;
    border: 1px solid #33405a;
    padding: 4px 8px;
    border-radius: 6px;
}
"""

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
    painter.setBrush(QColor("#121722"))
    painter.drawRoundedRect(0, 0, 64, 64, 14, 14)
    painter.setBrush(QColor("#3b82f6"))
    painter.drawEllipse(8, 8, 48, 48)
    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("Arial", 17, QFont.Bold))
    painter.drawText(pix.rect(), Qt.AlignCenter, "AFK")
    painter.end()
    return QIcon(pix)


class ProjectCard(QFrame):
    def __init__(self, key, spec, color, on_start, on_stop, on_logs):
        super().__init__()
        self.key = key
        self.color = color
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_logs = on_logs
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
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.dot.setStyleSheet("background: {0}; border-radius: 5px;".format(STATUS_COLORS["pending"]))
        self.name_label = QLabel(spec.get("display_name", key))
        self.name_label.setObjectName("cardName")
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
        self.start_btn = QPushButton("启动")
        self.start_btn.setObjectName("mini")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("mini")
        self.logs_btn = QPushButton("日志")
        self.logs_btn.setObjectName("mini")
        self.start_btn.clicked.connect(lambda: self.on_start(self.key))
        self.stop_btn.clicked.connect(lambda: self.on_stop(self.key))
        self.logs_btn.clicked.connect(lambda: self.on_logs(self.key))
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.logs_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def update_state(self, run):
        if run is None:
            self.dot.setStyleSheet("background: {0}; border-radius: 5px;".format(STATUS_COLORS["pending"]))
            self.status_label.setText("等待")
            self.status_label.setStyleSheet("color: {0};".format(STATUS_COLORS["pending"]))
            self.detail_label.setText("未运行")
            self.attempt_label.setText("")
            return
        status = run.get("status", "pending")
        color = STATUS_COLORS.get(status, STATUS_COLORS["pending"])
        self.dot.setStyleSheet("background: {0}; border-radius: 5px;".format(color))
        self.status_label.setText(STATUS_TEXT.get(status, status))
        self.status_label.setStyleSheet("color: {0};".format(color))
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

    def __init__(self):
        super().__init__()
        self.settings = config.settings()
        self.auto_run_requested = "--autorun" in sys.argv
        self.start_in_tray = "--tray" in sys.argv
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
        self.event_buffer = deque(maxlen=5000)
        self._build_ui()
        self._build_tray()

        self.queue = self.bus.subscribe()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(300)

        self.scheduler = Scheduler(config.settings, self.start_chain, self.bus)
        self.scheduler.start()

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

        if self.auto_run_requested:
            self._append_log("core", "info", "--autorun 已启用，2 秒后自动开始一键挂机")
            QTimer.singleShot(2000, self.start_chain)
        elif (self.settings.get("gui", {}) or {}).get("autostart_chain_on_launch"):
            QTimer.singleShot(2000, self.start_chain)

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 12)
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
        self.chain_pill = QLabel("空闲")
        self.chain_pill.setObjectName("pill")
        self.chain_btn = QPushButton("一键挂机")
        self.chain_btn.setObjectName("primary")
        self.stop_all_btn = QPushButton("全部停止")
        self.stop_all_btn.setObjectName("danger")
        top.addWidget(self.chain_pill)
        top.addWidget(self.chain_btn)
        top.addWidget(self.stop_all_btn)
        header_layout.addLayout(top)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.doctor_btn = QPushButton("环境自检")
        self.patch_btn = QPushButton("校验补丁")
        self.open_logs_btn = QPushButton("打开日志")
        self.open_configs_btn = QPushButton("打开配置")
        self.update_btn = QPushButton("立即更新")
        self.mumu_btn = QPushButton("更新MuMu")
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
        self.shutdown_check = QCheckBox("全部完成后关机")
        controls.addWidget(self.pre_run_check)
        controls.addWidget(self.schedule_check)
        controls.addWidget(self.schedule_time)
        controls.addWidget(self.shutdown_check)
        header_layout.addLayout(controls)
        root.addWidget(header)

        cards_title = QLabel("项目状态")
        cards_title.setObjectName("sectionTitle")
        root.addWidget(cards_title)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        for key, spec in (config.registry().get("projects", {}) or {}).items():
            card = ProjectCard(key, spec, self._project_color(key), self.start_project, self.stop_project, self.show_project_logs)
            self.cards[key] = card
            cards_row.addWidget(card)
        root.addLayout(cards_row)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        self.tabs = QTabWidget()

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
        clear_btn = QPushButton("清空显示")
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
        log_layout.addLayout(log_controls)
        log_layout.addWidget(self.log_view)
        self.tabs.addTab(log_tab, "实时日志")

        reports_tab = QWidget()
        reports_layout = QVBoxLayout(reports_tab)
        reports_layout.setContentsMargins(10, 10, 10, 10)
        report_controls = QHBoxLayout()
        refresh_reports = QPushButton("刷新")
        refresh_reports.clicked.connect(self.refresh_reports)
        open_report = QPushButton("打开选中报告")
        open_report.clicked.connect(self.open_selected_report)
        report_controls.addWidget(refresh_reports)
        report_controls.addWidget(open_report)
        report_controls.addStretch(1)
        self.report_list = QListWidget()
        reports_layout.addLayout(report_controls)
        reports_layout.addWidget(self.report_list)
        self.tabs.addTab(reports_tab, "错误报告")

        self.registry_edit = QPlainTextEdit()
        self._load_text(self.registry_edit, config.PROJECTS_YAML)
        registry_tab = self._file_tab(self.registry_edit, config.PROJECTS_YAML, "保存项目注册表")
        self.tabs.addTab(registry_tab, "项目注册表")

        self.settings_edit = QPlainTextEdit()
        self._load_text(self.settings_edit, config.SETTINGS_YAML)
        settings_tab = self._file_tab(self.settings_edit, config.SETTINGS_YAML, "保存设置")
        self.tabs.addTab(settings_tab, "设置")

        self.repair_edit = QPlainTextEdit()
        self._load_text(self.repair_edit, config.REPAIR_YAML)
        repair_tab = self._file_tab(self.repair_edit, config.REPAIR_YAML, "保存修复规则")
        self.tabs.addTab(repair_tab, "修复规则")

        doctor_tab = QWidget()
        doctor_layout = QVBoxLayout(doctor_tab)
        doctor_layout.setContentsMargins(10, 10, 10, 10)
        doctor_controls = QHBoxLayout()
        run_doctor = QPushButton("运行自检")
        run_doctor.clicked.connect(self.run_doctor)
        doctor_controls.addWidget(run_doctor)
        doctor_controls.addStretch(1)
        self.doctor_view = QPlainTextEdit()
        self.doctor_view.setReadOnly(True)
        self.doctor_view.setFont(QFont("Consolas", 9))
        doctor_layout.addLayout(doctor_controls)
        doctor_layout.addWidget(self.doctor_view)
        self.tabs.addTab(doctor_tab, "环境自检")

        splitter.addWidget(self.tabs)
        root.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self.chain_btn.clicked.connect(self.start_chain)
        self.stop_all_btn.clicked.connect(self.supervisor.stop_all)
        self.doctor_btn.clicked.connect(self.run_doctor)
        self.patch_btn.clicked.connect(self.verify_patches)
        self.open_logs_btn.clicked.connect(lambda: self._open_path(config.LOGS_DIR))
        self.open_configs_btn.clicked.connect(lambda: self._open_path(config.ROOT / "configs"))
        self.update_btn.clicked.connect(self.run_updates)
        self.mumu_btn.clicked.connect(self.run_mumu_updater)
        self.shutdown_check.toggled.connect(self._toggle_shutdown)

    def _file_tab(self, edit, path, button_text):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        controls = QHBoxLayout()
        save = QPushButton(button_text)
        save.setObjectName("primary")
        save.clicked.connect(lambda: self._save_text(edit, path))
        reload_btn = QPushButton("重新加载")
        reload_btn.clicked.connect(lambda: self._load_text(edit, path))
        open_btn = QPushButton("打开所在目录")
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
        stop_action = QAction("全部停止", self)
        stop_action.triggered.connect(self.supervisor.stop_all)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(show_action)
        menu.addAction(chain_action)
        menu.addAction(stop_action)
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
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def _toggle_shutdown(self, checked):
        self.settings.setdefault("post_actions", {})["after_all"] = "shutdown" if checked else "none"
        config.save_yaml(config.SETTINGS_YAML, self.settings)
        self.supervisor.settings = self.settings
        if not checked:
            self.supervisor.cancel_post_action()

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
        self.supervisor.run_chain()

    def start_project(self, key):
        threading.Thread(target=self.supervisor.start_project, args=(key,), daemon=True, name="afk-start-" + key).start()

    def stop_project(self, key):
        threading.Thread(target=self.supervisor.stop_project, args=(key,), daemon=True, name="afk-stop-" + key).start()

    def show_project_logs(self, key):
        self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(key)))
        self.tabs.setCurrentIndex(0)

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
            if self._matches_filter(event, filter_project):
                self._append_log(event.get("project", "core"), event.get("level", "info"), event.get("message", ""), autoscroll=False)
        if drained and self.autoscroll_check.isChecked():
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        snapshot = self.supervisor.status_snapshot()
        for key, card in self.cards.items():
            card.update_state(snapshot["runs"].get(key))
        chain = snapshot["chain_status"]
        self.chain_pill.setText(CHAIN_TEXT.get(chain, chain))
        color = CHAIN_COLORS.get(chain, CHAIN_COLORS["idle"])
        self.chain_pill.setStyleSheet(
            "background: {0}22; border: 1px solid {0}66; border-radius: 12px; padding: 4px 14px; color: {0}; font-size: 12px; font-weight: 700;".format(color)
        )

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
        self.bus.unsubscribe(self.queue)
        self.supervisor.stop_all()
        self.bus.stop_server()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if (self.settings.get("gui", {}) or {}).get("minimize_to_tray", False):
            event.ignore()
            self.hide()
            self.tray.showMessage("Auto-AFK", "已最小化到托盘", QSystemTrayIcon.Information, 2000)
        else:
            self._quit()


def _escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    if window.start_in_tray:
        window.tray.showMessage("Auto-AFK", "已在托盘启动，自动挂机进行中", QSystemTrayIcon.Information, 3000)
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
