import logging
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

from PySide6.QtCore import QSize, Qt, QTime, QTimer, Signal
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
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from afk_core import config, doctor, idle, report
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
    FlowHost,
    HoverGlowCard,
    StatusDot,
    add_shadow,
    apply_palette,
    enable_smooth_scroll,
    fade_in_widget,
    fade_in_window,
)
from gui.idle_prompt import (
    DECISION_BACKGROUND,
    DECISION_NONE,
    DECISION_STOP,
    IdlePromptDialog,
    decide_on_timeout,
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

# 没有 pid/exit/message 可显示时的详情行文案。必须按状态给，不能统一写
# 「运行中…」，否则排队中的卡片会显示成「等待 / 运行中...」自相矛盾。
STATUS_DETAIL_FALLBACK = {
    "running": "运行中…",
    "success": "已完成",
    "success_unverified": "已完成（未校验）",
    "pending": "等待启动",
    "failed": "已失败",
    "stopped": "已停止",
    "hang": "无响应",
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

# 首选窗口尺寸。1360x900 在 1920x1080 / 125% 缩放（可用 1536x824）、
# 1366x768、1280x720 上都放不下，底部按钮会掉到屏幕外，所以要按实际可用
# 区域收敛，而不是写死。
PREFERRED_WINDOW_SIZE = (1360, 900)
CARDS_MIN_HEIGHT = 120
# 页签区（实时日志等）与卡片区共用一个 QSplitter。卡片折行需要更高时，只能
# 从页签区借空间；算「卡片区还剩多少余量」时按这个最小值计，而不是按它当前
# 占的高度，否则它会把余量吃光、卡片区永远只放得下一行（用户看不到后两个项目）。
# 取 120 而不是 QSplitter 自己算出的 181：181 会让 1366x768 差 30px 放不下第二
# 行卡片，而日志区压到 120 仍能显示页签栏 + 过滤行 + 两行日志，比藏掉两张卡强。
SPLITTER_MIN_HEIGHT = 120


def _preferred_window_size(screen=None):
    """按屏幕可用区域算出不超过可用范围的首选窗口尺寸。"""
    from PySide6.QtGui import QGuiApplication

    width, height = PREFERRED_WINDOW_SIZE
    if screen is None:
        try:
            screen = QGuiApplication.primaryScreen()
        except Exception:
            screen = None
    if screen is None:
        return width, height
    try:
        avail = screen.availableGeometry()
    except Exception:
        return width, height
    if avail.width() <= 0 or avail.height() <= 0:
        return width, height
    # 留 2% 余量，避免窗口贴边（任务栏/Dock 吃掉几个像素就出现滚动条）
    limit_w = max(640, int(avail.width() * 0.98))
    limit_h = max(480, int(avail.height() * 0.98))
    return min(width, limit_w), min(height, limit_h)


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


# apply_spec 的哨兵值：区分「没传 on_gui」和「显式传 None（这个项目没有界面入口）」
_UNSET = object()


class ProjectCard(HoverGlowCard):
    def __init__(self, key, spec, color, on_start, on_stop, on_logs, on_gui=None):
        super().__init__()
        self.key = key
        self.spec = spec
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
        self.accent = accent

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
        # 点击时再看一眼 self.on_gui：注册表里的 gui 段可能在运行中被改掉，
        # apply_spec 会把 on_gui 置空，这里不能把回调固定死在构造时的值上。
        def _gui_clicked():
            if self.on_gui is None:
                return
            # 防抖：点击后短暂禁用，避免双击拉起两个实例
            self.gui_btn.setEnabled(False)
            self.on_gui(self.key)
            QTimer.singleShot(1500, lambda: self.gui_btn.setEnabled(True))

        self.gui_btn.clicked.connect(_gui_clicked)
        self.gui_btn.setVisible(self.on_gui is not None)
        self.start_btn.clicked.connect(lambda: self.on_start(self.key))
        self.stop_btn.clicked.connect(lambda: self.on_stop(self.key))
        self.logs_btn.clicked.connect(lambda: self.on_logs(self.key))
        buttons.addWidget(self.gui_btn)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.logs_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def apply_spec(self, spec, color=None, on_gui=_UNSET):
        """注册表里这个项目的显示名/颜色/gui 入口改了之后刷新卡片。

        历史缺陷：_reload_registry 只补新增卡片、删多余卡片，已存在的卡片一律
        沿用构造时的 spec——用户在「项目注册表」里改了 display_name 再保存，
        左侧导航和副标题都变了，项目卡上的名字还是旧的，看起来像没保存成功。
        颜色同理（settings.yaml 的 gui.project_colors 改完要跟着变）；
        「界面」按钮也一样，gui 段被删掉后按钮还留着，点下去照样去拉起一个
        已经没有界面入口的进程。
        """
        self.spec = spec or {}
        display_name = str(self.spec.get("display_name", self.key))
        short = display_name.split("（")[0]
        if self.name_label.text() != short:
            self.name_label.setText(short)
        self.name_label.setToolTip(display_name if display_name != short else "")
        if on_gui is not _UNSET:
            self.on_gui = on_gui
        self.gui_btn.setVisible(self.on_gui is not None)
        if color and color != self.color:
            self.color = color
            self.accent.setStyleSheet("background: {0}; border-radius: 2px;".format(color))

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
        # 兜底文案必须跟状态走：历史缺陷是无条件写「运行中...」，于是排队中的
        # 卡片（status=pending、还没有 pid/exit/message）也显示成「等待 / 运行中...」，
        # 状态行和详情行互相矛盾，用户以为项目已经在跑。
        self.detail_label.setText(" | ".join(parts) if parts else STATUS_DETAIL_FALLBACK.get(status, ""))
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
        # 默认尺寸按屏幕可用区域收敛：写死 1360x900 在 1366x768 的笔记本上
        # 会矮出屏幕 132px（窗口底边压到任务栏以下，底部按钮点不到），
        # 1280x720/1920x1080+缩放 125% 的机器上同样越界。
        self._initial_geometry_applied = False
        # 恢复上次窗口尺寸、以及 _fit_to_screen 夹紧的 resizing 期间不要写盘
        self._geometry_restoring = False
        self.resize(*_preferred_window_size())
        self.setWindowIcon(make_icon())

        self.bus = EventBus()
        self.bus.start_server()
        self.repair = RepairEngine(self.bus, self.settings)
        self.supervisor = Supervisor(self.bus, settings=self.settings, repair=self.repair, on_change=self._on_change)
        self.cards = {}
        self._log_line_count = 0
        self._did_intro = False
        self._last_chain_for_completion = None
        self._last_done_today = None
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
        # 拖动时合并落盘：moveEvent 按鼠标采样率触发，逐帧写 yaml 会有几十次磁盘写入。
        self._widget_pos_timer = QTimer(self)
        self._widget_pos_timer.setSingleShot(True)
        self._widget_pos_timer.setInterval(400)
        self._widget_pos_timer.timeout.connect(self._flush_widget_pos)
        if not self.no_widget:
            self._build_widget()

        # 定时启动的时间框：按住小时/分钟的箭头连点、或滚轮拖动时，
        # QTimeEdit 每变一格就发一次 timeChanged。原本直连 _toggle_schedule，
        # 实测拖动 30 格就是 30 次整份写 settings.yaml、30 行实时日志，
        # 把用户要看的运行日志冲掉。改成合并到「停下 400ms 后」落盘一次
        # （与设置页数值框、桌面小组件拖动同一套思路）。
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setSingleShot(True)
        self._schedule_timer.setInterval(400)
        self._schedule_timer.timeout.connect(self._flush_schedule)

        # 窗口尺寸同样合并落盘：拖边框时 resizeEvent 每帧都触发，逐帧写 yaml
        # 会有几十次磁盘写入（与小组件拖动、数值框防抖同一套思路）。
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(400)
        self._geometry_timer.timeout.connect(self._flush_window_geometry)

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
        subtitle = QLabel(self._subtitle_text())
        subtitle.setObjectName("subtitle")
        # 存引用：注册表改过之后副标题要跟着重新生成（项目数量变了）
        self._subtitle_label = subtitle
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
        # 用 FlowLayout 而非 QHBoxLayout：5 张卡横排会把窗口最小宽度顶到 1592px，
        # 1366/1440 宽的笔记本上窗口撑出屏幕。流式布局按可用宽度自动折行；
        # 外层 FlowHost 负责把「折成几行就有多高」报给父布局，否则第二行会被
        # 画到下面的页签上面（窄窗口下实测重叠）。
        cards_host = FlowHost(h_spacing=12, v_spacing=12)
        cards_row = cards_host.flow()
        self.cards_host = cards_host
        for key, spec in (config.registry().get("projects", {}) or {}).items():
            card = ProjectCard(
                key, spec, self._project_color(key),
                self.start_project, self.stop_project, self.show_project_logs,
                on_gui=self.open_project_page if spec.get("gui") else None,
            )
            self.cards[key] = card
            cards_row.addWidget(card)
        # 卡片区可滚动：卡片是等宽定宽的，窗口窄时折成两行、矮屏上就没地方放了。
        # 直接把它塞进 QVBoxLayout 会把主窗口最小高度顶到 821px——1366x768 的
        # 笔记本（可用 728）上窗口底部按钮直接掉出屏幕。套一层滚动区后最小高度
        # 只剩一行的量，空间够时仍然完整显示不出现滚动条。
        cards_scroll = CardsScrollArea()
        cards_scroll.setObjectName("cardsScroll")
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setFrameShape(QFrame.NoFrame)
        cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cards_scroll.setMinimumHeight(CARDS_MIN_HEIGHT)
        cards_scroll.setWidget(cards_host)
        enable_smooth_scroll(cards_scroll)
        self.cards_scroll = cards_scroll
        root.addWidget(cards_scroll, 0)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setMinimumHeight(SPLITTER_MIN_HEIGHT)
        self._log_splitter = splitter
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
        # 记录引用：切到本页时按需刷新（列表不预先加载——启动时扫盘会拖慢开窗，
        # 但只在点「刷新」时加载又会让用户打开本页看到空列表，误以为没有报告）。
        self._reports_tab = reports_tab
        self._reports_loaded = False
        # 上次扫盘时报告目录的 mtime：目录没变就不重复扫（每次 200 个目录约 6ms，
        # 但每次切页都扫一遍没有意义）；变了说明有新报告/被清理，必须重扫。
        self._reports_mtime = None
        self.report_list.itemDoubleClicked.connect(lambda _item: self.open_selected_report())
        self.tabs.addTab(reports_tab, "错误报告")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.registry_edit = QPlainTextEdit()
        self._load_text(self.registry_edit, config.PROJECTS_YAML)
        enable_smooth_scroll(self.registry_edit)
        registry_tab = self._file_tab(self.registry_edit, config.PROJECTS_YAML, "保存项目注册表")
        self.tabs.addTab(registry_tab, "项目注册表")

        self.settings_edit = QPlainTextEdit()
        self._load_text(self.settings_edit, config.SETTINGS_YAML)
        enable_smooth_scroll(self.settings_edit)
        # 编辑器只在开窗时读过一次盘。用户在别处改设置（头部开关 / 设置页 / 托盘）
        # 后回到本页按「保存设置」，会把界面里这份过期文本整份写回，刚改的配置
        # 就这么被覆盖没了。用「脏标记」区分：用户动过就绝不覆盖，没动过就跟随磁盘。
        self._settings_edit_dirty = False
        self._settings_edit_loading = False
        self.settings_edit.textChanged.connect(self._mark_settings_edit_dirty)
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
                open_logs_fn=self._open_project_logs,
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
        # 离开设置页时把防抖中的改动写掉：数值框改成「停止变动 400ms 后落盘」，
        # 用户改完立刻切页/关窗口，未到期的改动不能丢。
        settings_page = getattr(self, "settings_page", None)
        if settings_page is not None and self.stack.currentWidget() is settings_page:
            settings_page.flush_pending()
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

    def _reload_project_colors(self):
        """settings.yaml 的 gui.project_colors 改了之后让卡片/嵌入页配色跟上。

        历史缺陷：project_colors 只在 MainWindow.__init__ 里读一次并 snapshot 成
        self.project_colors。用户在「设置」tab 里改某个项目的颜色（或在 yaml 里加
        gui.project_colors）再保存，卡片顶部色条、项目嵌入页的指示灯、
        以及实时日志里该项目的前缀颜色全是开窗时那份旧配色，看起来像没保存。
        """
        custom = (self.settings.get("gui") or {}).get("project_colors") or {}
        colors = dict(PROJECT_COLORS)
        colors.update({str(k): str(v) for k, v in custom.items()})
        self.project_colors = colors
        for key, card in (getattr(self, "cards", {}) or {}).items():
            color = self._project_color(key)
            if card.color != color:
                card.apply_spec(card.spec, color)
        for key, page in (getattr(self, "project_pages", {}) or {}).items():
            color = self._project_color(key)
            if page.color != color:
                page.apply_spec(page.spec, color)

    def _on_settings_changed(self):
        self.settings = config.settings()
        self.supervisor.settings = self.settings
        # 修复引擎也持有 settings 与 repair_rules：之前只同步了 supervisor，
        # 结果在「设置」里关掉 repair.enabled、或改 max_rounds，挂机时照样按
        # 旧配置重试（引擎手上的还是开窗时那份 dict）。
        repair_engine = getattr(self, "repair", None)
        if repair_engine is None:
            repair_engine = getattr(self.supervisor, "repair", None)
        if repair_engine is not None:
            repair_engine.reload(self.settings)
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
        self._reload_project_colors()
        self._sync_settings_editor()

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
            text = Path(path).read_text(encoding="utf-8")
        except Exception as exc:
            edit.setPlainText("# 读取失败: {0}".format(exc))
            return
        # 程序化回填不能算「用户编辑」，否则脏标记会一直为真，磁盘改动再也同步不过来
        if edit is getattr(self, "settings_edit", None):
            self._settings_edit_loading = True
            try:
                edit.setPlainText(text)
            finally:
                self._settings_edit_loading = False
                self._settings_edit_dirty = False
            return
        edit.setPlainText(text)

    def _mark_settings_edit_dirty(self):
        """「设置」tab 的 yaml 编辑器被用户改动过（程序回填不算）。"""
        if getattr(self, "_settings_edit_loading", False):
            return
        self._settings_edit_dirty = True

    def _sync_settings_editor(self):
        """设置文件在别处被改后，若编辑器没被用户动过就跟着刷新。

        历史缺陷：settings_edit 只在开窗时读一次盘，用户在头部开关或设置页里
        改了配置，再回到「设置」tab 点「保存设置」，会把过期文本整份写回，
        刚改的配置被静默覆盖回旧值（实测 pre_run.enabled 改 False 后又被写回
        True）。用户动过的编辑器不覆盖，避免吃掉他正在写的内容。
        """
        edit = getattr(self, "settings_edit", None)
        if edit is None or getattr(self, "_settings_edit_dirty", False):
            return
        self._load_text(edit, config.SETTINGS_YAML)

    def _save_text(self, edit, path):
        text = edit.toPlainText()
        if str(path).lower().endswith((".yaml", ".yml")):
            try:
                parsed = yaml.safe_load(text)
            except Exception as exc:
                QMessageBox.critical(self, "YAML 校验失败", "配置未保存，请先修正语法错误：\n\n{0}".format(exc))
                return
            # 语法通过还不够：顶层必须是映射。写成 `- a` 或一整句文字都能被
            # yaml 解析成功，但存进去之后所有 settings().get(...) 立刻崩，程序
            # 打不开也改不回来（只能手工编辑文件）。
            if parsed is not None and not isinstance(parsed, dict):
                QMessageBox.critical(
                    self,
                    "YAML 校验失败",
                    "配置未保存：顶层必须是「键: 值」映射，当前读到的是{0}。".format(
                        "列表" if isinstance(parsed, list) else "单个值"
                    ),
                )
                return
            # 空内容同样要拦：把整份配置改成注释（或在「读取失败」提示上直接按
            # 保存）都会让 safe_load 得到 None，写进去之后 settings() 立刻变成空
            # 映射——运行顺序、空闲挂机、开机自启、配色全部回到默认值，界面还
            # 一本正经地显示「已保存」，用户根本不知道配置被清空了。
            if not parsed:
                QMessageBox.critical(
                    self,
                    "YAML 校验失败",
                    "配置未保存：内容为空（或只有注释），保存后整份配置会被清空，"
                    "所有设置都会回到默认值。确实要清空请直接编辑文件。",
                )
                return
        try:
            Path(path).write_text(text, encoding="utf-8")
            self._append_log("core", "info", "已保存 {0}".format(path))
            saved = Path(path)
            if saved == Path(config.SETTINGS_YAML):
                self.settings = config.settings()
                self.supervisor.settings = self.settings
                if getattr(self, "settings_page", None) is not None:
                    self.settings_page.reload_from_disk()
                self._sync_header_settings()
                # 托盘勾选、桌面小组件可见性也要跟着文件走。
                # 历史缺陷：这里原本只同步了头部三个控件，用户在「设置」tab 里把
                # idle.enabled 改成 false 或 gui.widget_enabled 改成 true 并保存，
                # 托盘菜单仍显示旧勾选、小组件既不出现也不消失。
                self._on_settings_changed()
                # 刚写进去的就是编辑器里这一份，脏标记清掉，后续外部改动可以再同步
                if edit is getattr(self, "settings_edit", None):
                    self._settings_edit_dirty = False
            elif saved == Path(config.PROJECTS_YAML):
                self._reload_registry()
            elif saved == Path(config.REPAIR_YAML):
                # 「修复规则」保存后同样要让运行中的引擎看到新规则，否则只能重启
                repair_engine = getattr(self, "repair", None)
                if repair_engine is None:
                    repair_engine = getattr(self.supervisor, "repair", None)
                if repair_engine is not None:
                    repair_engine.reload()
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def _reload_registry(self):
        """「项目注册表」保存后让界面跟上新注册表。

        历史缺陷：这里原来什么都没做。改完注册表一保存，文件是对的，但界面、
        supervisor、设置页全都还在用开窗时读到的那一份——用户新增的项目不出现
        在总览页和左侧导航、删掉的项目卡片还在、改过的显示名/启用状态不生效。
        重新读盘后按差异同步三处：项目卡、左侧导航入口、设置页（含运行顺序）。
        """
        registry = config.registry()
        projects = registry.get("projects", {}) or {}
        self.supervisor.registry = registry
        # -- 1. 导航栏：新增的加进来，删掉的移走，改名/启用状态跟随 --------
        nav = getattr(self, "nav", None)
        for key, spec in projects.items():
            nav_key = "page:" + key
            label = str(spec.get("display_name", key)).split("（")[0]
            if nav is not None and nav.item_for_key(nav_key) is None:
                nav.add_item(nav_key, label)
            elif nav is not None:
                item = nav.item_for_key(nav_key)
                if item is not None and item.text() != label:
                    item.setText(label)
        if nav is not None:
            for nav_key in nav.item_keys():
                if not nav_key.startswith("page:"):
                    continue
                key = nav_key[5:]
                if key not in projects or not (projects[key] or {}).get("gui"):
                    nav.remove_item(nav_key)
        self._nav_page_keys = [
            (key, "page:" + key) for key, spec in projects.items() if spec.get("gui")
        ]
        # -- 2. 项目卡：缺的补上，多的撤掉，其余更新文案/颜色 ----------------
        cards = getattr(self, "cards", {})
        host = getattr(self, "cards_host", None)
        flow = host.flow() if host is not None else None
        for key, spec in projects.items():
            if key in cards:
                # 已存在的卡片也要跟新显示名：否则改完 display_name 保存后导航和
                # 副标题都变了，卡片标题还是旧的。
                cards[key].apply_spec(
                    spec,
                    self._project_color(key),
                    self.open_project_page if spec.get("gui") else None,
                )
                continue
            if flow is None:
                continue
            card = ProjectCard(
                key, spec, self._project_color(key),
                self.start_project, self.stop_project, self.show_project_logs,
                on_gui=self.open_project_page if spec.get("gui") else None,
            )
            cards[key] = card
            flow.addWidget(card)
        for key in [k for k in cards if k not in projects]:
            card = cards.pop(key)
            if flow is not None:
                flow.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        # -- 3. 项目嵌入页：新增带 gui 的补页，删掉的移除 ---------------------
        pages = getattr(self, "project_pages", {})
        for key, spec in projects.items():
            if not spec.get("gui"):
                continue
            if key in pages:
                pages[key].apply_spec(spec)
                continue
            page = ProjectPage(
                key, spec, self._project_color(key),
                self.supervisor, self._append_log,
                open_logs_fn=self._open_project_logs,
            )
            pages[key] = page
            self.stack.addWidget(page)
        # 正开着的那个项目页即将被删：QStackedWidget 移除当前页会自动切到相邻
        # 下标——实测从「三月七小助手」页删掉该项目后，界面直接变成「BAAH」的页，
        # 而左侧导航没有任何高亮项、页头还停在原处，用户看到的是另一个项目的
        # 界面且不知道自己什么时候切过去的。这里显式退回总览页。
        viewing_removed = False
        current_widget = self.stack.currentWidget()
        doomed = [k for k in pages if k not in projects or not (projects[k] or {}).get("gui")]
        for key in doomed:
            if pages[key] is current_widget:
                viewing_removed = True
        if viewing_removed:
            # 先切走再删，别让 QStackedWidget 自己挑相邻页
            self.stack.setCurrentWidget(self.stack.widget(0))
            if nav is not None:
                nav.set_active("overview", animate=False)
        for key in doomed:
            page = pages.pop(key)
            try:
                page.shutdown()
            except Exception:
                pass
            self.stack.removeWidget(page)
            page.setParent(None)
            page.deleteLater()
        # -- 4. 日志过滤器、设置页、副标题 -----------------------------------
        combo = getattr(self, "filter_combo", None)
        if combo is not None:
            wanted = list(projects.keys()) + ["core"]
            current = combo.currentData() or ""
            existing = [combo.itemData(i) for i in range(combo.count())]
            if existing != ["", *wanted]:
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("全部项目", "")
                for key in wanted:
                    combo.addItem(key, key)
                idx = combo.findData(current)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.blockSignals(False)
                # 重建下拉框时是 blockSignals 的，currentIndexChanged 不会触发
                # _rerender_log。历史缺陷：删掉当前过滤着的项目后，下拉框已回到
                # 「全部项目」，日志区却还是那份按旧项目过滤过的内容（其它项目的
                # 历史日志一行都不显示），两处不一致。这里补一次重绘。
                self._rerender_log()
        settings_page = getattr(self, "settings_page", None)
        if settings_page is not None:
            settings_page.reload_from_disk()
        label = getattr(self, "_subtitle_label", None)
        if label is not None:
            label.setText(self._subtitle_text())
        self._sync_cards_height()
        self._append_log(
            "core", "info", "项目注册表已重新加载：{0} 个项目".format(len(projects))
        )

    def _sync_header_settings(self):
        """把「设置」tab 里手改 yaml 的结果同步回头部控件。

        历史缺陷：在「设置」tab 直接改 settings.yaml 并保存后，self.settings 会被
        重读，但总览页头部的「运行前更新 / 定时启动 / 时间」三个控件只在构造时
        读过一次，界面显示与文件内容不一致——用户会以为改动没生效而去重复操作。
        阻塞信号避免 setChecked 反过来触发一次写盘。
        """
        pre_cfg = self.settings.get("pre_run") or {}
        schedule_cfg = self.settings.get("schedule") or {}
        pairs = [
            (getattr(self, "pre_run_check", None), bool(pre_cfg.get("enabled", True))),
            (getattr(self, "schedule_check", None), bool(schedule_cfg.get("enabled"))),
        ]
        for widget, want in pairs:
            if widget is None or widget.isChecked() == want:
                continue
            widget.blockSignals(True)
            widget.setChecked(want)
            widget.blockSignals(False)
        time_edit = getattr(self, "schedule_time", None)
        if time_edit is not None:
            try:
                hour, minute = [int(x) for x in str(schedule_cfg.get("time", "04:00")).split(":")[:2]]
            except (TypeError, ValueError):
                hour, minute = 4, 0
            target = QTime(hour, minute)
            if time_edit.time() != target:
                time_edit.blockSignals(True)
                time_edit.setTime(target)
                time_edit.blockSignals(False)

    def _toggle_schedule(self, *_):
        self._schedule_timer.start()

    def _flush_schedule(self):
        """把定时启动开关与时间落盘（合并防抖后的真正写盘动作）。

        历史缺陷：这里直接改 `self.settings`（开窗时读进内存的那份）再整份写回。
        只要期间有别的地方改过 settings.yaml（CLI、另一进程、用户手改文件），
        这次保存就会把那些改动一起抹掉。改成先重读磁盘再改（与设置页同一套）。
        """
        enabled = bool(self.schedule_check.isChecked())
        when = self.schedule_time.time().toString("HH:mm")

        def mutate(settings):
            schedule = settings.setdefault("schedule", {})
            schedule["enabled"] = enabled
            schedule["time"] = when

        self._mutate_settings(mutate)
        self._append_log("core", "info", "定时启动: {0} {1}".format("开" if enabled else "关", when))

    def _toggle_pre_run(self, checked):
        def mutate(settings):
            settings.setdefault("pre_run", {})["enabled"] = bool(checked)

        # 同样先重读磁盘：直接改 self.settings 再整份写回会覆盖掉期间的外部改动
        # （实测把 idle.minutes 改成 77 后点一下这个开关，文件又变回 30）。
        self._mutate_settings(mutate)
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

    def _open_project_logs(self, key):
        """项目页「打开日志」：打开该项目的日志目录（含 .out.log/.err.log）。

        先找本项目最近一次运行的具体日志文件，没有就退到日志根目录。
        """
        out_log = None
        try:
            run = (self.supervisor.status_snapshot().get("runs") or {}).get(key) or {}
            candidate = run.get("out_log")
            if candidate and Path(candidate).exists():
                out_log = Path(candidate)
        except Exception:
            out_log = None
        if out_log is None:
            project_dir = config.PROJECT_LOGS
            if project_dir.exists():
                matches = sorted(project_dir.glob("{0}_*.out.log".format(key)))
                if matches:
                    out_log = matches[-1]
        self._open_path(out_log if out_log is not None else config.LOGS_DIR)

    def _clear_log(self):
        """清空实时日志显示。

        历史缺陷：只清空了 QPlainTextEdit，event_buffer 里的历史仍留着——用户点
        「清空显示」后一旦切换过滤下拉框就会触发 _rerender_log，把刚清掉的日志
        全部重新画回来。清空显示就该是清空。
        """
        self.log_view.clear()
        self.event_buffer.clear()
        self._log_line_count = 0

    def _project_color(self, project):
        colors = getattr(self, "project_colors", PROJECT_COLORS)
        if project in colors:
            return colors[project]
        if project in PROJECT_COLORS:
            return PROJECT_COLORS[project]
        index = sum(ord(ch) for ch in str(project)) % len(DEFAULT_PALETTE)
        return DEFAULT_PALETTE[index]

    def _append_log(self, project, level, message, autoscroll=True, emit_event=True):
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
        # 让 GUI 自身的动作也进入事件流（events.jsonl / 报告 / 远程订阅都能看到）。
        # 否则用户开关设置、空闲决策、手动更新等关键动作只留在界面里，
        # 排查问题时「日志里什么都没发生」——这正是之前空闲故障最难看的地方。
        # 渲染来自事件流的事件时用 emit_event=False，避免把收到的再发一遍造成回声。
        if emit_event:
            try:
                self.bus.emit(
                    {
                        "project": str(project),
                        "kind": "gui",
                        "level": str(level),
                        "message": str(message),
                        # origin=gui 让 _poll 认出「这条是本进程自己刚画过的」：
                        # 事件会经订阅队列回环一次，再渲染一遍就出现两行同样的日志。
                        "origin": "gui",
                    }
                )
            except Exception:
                pass

    def _matches_filter(self, event, filter_project):
        if not filter_project:
            return True
        return event.get("project") == filter_project

    def _rerender_log(self):
        self.log_view.clear()
        filter_project = self.filter_combo.currentData() or ""
        for event in self.event_buffer:
            if self._matches_filter(event, filter_project):
                self._append_log(event.get("project", "core"), event.get("level", "info"),
                                 event.get("message", ""), autoscroll=False, emit_event=False)
        self._log_line_count = len(self.event_buffer)
        if self.autoscroll_check.isChecked():
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _subtitle_text(self):
        """副标题里的项目名单从注册表算出来。

        历史缺陷：写死成「三月七小助手 + BAAH + MaaEnd」，而注册表有 5 个项目，
        绝区零一龙 和 BetterGI 一直被漏掉，副标题与实际能挂的项目不符。
        """
        names = []
        for key, spec in (config.registry().get("projects", {}) or {}).items():
            label = str(spec.get("display_name", key))
            names.append(label.split("（")[0].strip())
        if not names:
            return "一键挂机 · 统一编排"
        return "一键挂机 · {0} 统一编排".format(" + ".join(names))

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
            # origin=gui 的事件是本进程自己刚画到日志区的，订阅队列回流时不能再画一遍，
            # 否则界面里同一行日志出现两次（用户看到「日志重复」）。仍留在 event_buffer
            # 里，切过滤下拉框重建视图时它会正常出现一次。
            if event.get("origin") == "gui":
                continue
            if self._matches_filter(event, filter_project):
                self._append_log(event.get("project", "core"), event.get("level", "info"), event.get("message", ""), autoscroll=False, emit_event=False)
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
        # 链刚成功结束时刷新设置页的「今日状态」（避免用户看到过期信息）。
        # 但不能只看链状态：标记文件也可能由 CLI 或另一个 Auto-AFK 进程写入，
        # 本进程的 chain_status 一直是 idle，标签就永远停在「未完成」。所以再
        # 比一次「今日是否已完成」的布尔值（读的是 mtime 缓存，几乎零开销）。
        done_today = self._is_done_today()
        if chain != self._last_chain_for_completion or done_today != self._last_done_today:
            self._last_chain_for_completion = chain
            self._last_done_today = done_today
            if getattr(self, "settings_page", None) is not None:
                try:
                    self.settings_page._refresh_completion_label()
                except Exception:
                    pass

    def _on_change(self):
        pass

    def _reports_dir_mtime(self):
        """错误报告目录的修改时间，用来判断「列表是否需要重扫」。"""
        try:
            return config.REPORTS_DIR.stat().st_mtime_ns
        except OSError:
            return None

    def _on_tab_changed(self, index):
        """切到「错误报告」页时刷新列表。

        历史缺陷一：报告列表只在点「刷新」时填充，打开程序切到本页永远是空列表，
        用户会以为没有错误报告（实际磁盘上有 44 份）。
        历史缺陷二：改成「只在第一次进入时加载」之后走向另一个极端——开窗之后
        才产生的报告（链跑挂了正是这时候生成的）在本进程里永远看不到，切过来
        仍然是「还没有错误报告」，与磁盘状态相反。现在按报告目录的 mtime 判断：
        目录没变就直接复用列表（一次 stat，微秒级），变了才重扫。
        """
        if self.tabs.widget(index) is not getattr(self, "_reports_tab", None):
            return
        if self._reports_loaded and self._reports_dir_mtime() == self._reports_mtime:
            return
        self.refresh_reports()

    def refresh_reports(self):
        """重扫报告目录并重建列表；尽量保留用户当前选中的报告。"""
        selected = None
        current = self.report_list.currentItem()
        if current is not None:
            selected = current.data(Qt.UserRole)
        self.report_list.clear()
        for item in report.list_reports(limit=200):
            entry = "{0}  [{1}] {2}".format(item["name"], item.get("project", ""), item.get("reason", ""))
            list_item = QListWidgetItem(entry)
            list_item.setData(Qt.UserRole, item["dir"])
            self.report_list.addItem(list_item)
        self._reports_loaded = True
        self._reports_mtime = self._reports_dir_mtime()
        if self.report_list.count() == 0:
            placeholder = QListWidgetItem("（还没有错误报告：项目异常退出时才会生成）")
            placeholder.setFlags(Qt.NoItemFlags)
            placeholder.setData(Qt.UserRole, None)
            self.report_list.addItem(placeholder)
        elif selected:
            for row in range(self.report_list.count()):
                if self.report_list.item(row).data(Qt.UserRole) == selected:
                    self.report_list.setCurrentRow(row)
                    break

    def open_selected_report(self):
        item = self.report_list.currentItem()
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        self._open_path(path)

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
        saved_pos = gui_cfg.get("widget_pos")
        # 恢复位置本身也会触发 moveEvent；用标志位避免把「回填初始位置」
        # 当成用户拖动去落盘。
        self._widget_restoring = True
        try:
            self.widget.restore_position(saved_pos)
        finally:
            self._widget_restoring = False
        # 存的坐标可能已经不在任何屏上（拔掉副屏 / 改分辨率 / 改缩放比例），
        # restore_position 会回落到右上角。此时把纠正后的位置写回去，让配置
        # 自愈——否则每次启动都要重新判一次，用户也看不出为什么位置"不记住"。
        try:
            actual = self.widget.current_position()
            stored = []
            if isinstance(saved_pos, (list, tuple)) and len(saved_pos) == 2:
                try:
                    stored = [int(saved_pos[0]), int(saved_pos[1])]
                except (TypeError, ValueError):
                    stored = []
            if stored != actual:
                # 先重读磁盘再改：这份 settings 是开窗时读进内存的，整份写回会
                # 抹掉期间由 CLI / 另一进程 / 手改文件写入的内容。
                def mutate(settings, _actual=actual):
                    settings.setdefault("gui", {})["widget_pos"] = list(_actual)

                self.settings = self._mutate_settings(mutate)
        except Exception as exc:
            logging.getLogger("afk.gui").warning("failed to heal widget position: %s", exc)
        if bool(gui_cfg.get("widget_enabled", False)):
            self.widget.show()
        self.widget.tick(self.supervisor.status_snapshot(), self.idle_watcher.status(),
                         self.idle_watcher.threshold_seconds())

    def _on_widget_moved(self, pos):
        """记录小组件位置。

        拖动过程中 moveEvent 会按鼠标采样率触发（实测一次拖动 60 帧 → 60 次回调），
        每次都整体重写 settings.yaml 会造成高频磁盘写入。这里只更新内存值，
        再用单次定时器把落盘合并到「松手后 400ms」一次。
        """
        gui_cfg = self.settings.setdefault("gui", {})
        target = [int(pos[0]), int(pos[1])]
        if list(gui_cfg.get("widget_pos") or []) == target:
            return
        gui_cfg["widget_pos"] = target
        if getattr(self, "_widget_restoring", False):
            return
        self._widget_pos_timer.start()

    def _flush_widget_pos(self):
        """把小组件位置落盘。

        同样先重读磁盘：这份 settings 是开窗时读进内存的，期间 CLI / 另一进程 /
        用户手改文件写过的内容会在这行整份写回时被抹掉（实测把 idle.minutes
        改成 77 之后拖一下小组件，文件又变回 30）。
        """
        self._widget_pos_timer.stop()
        target = list((self.settings.get("gui") or {}).get("widget_pos") or [])
        if len(target) != 2:
            return

        def mutate(settings):
            settings.setdefault("gui", {})["widget_pos"] = [int(target[0]), int(target[1])]

        try:
            self._mutate_settings(mutate)
        except Exception as exc:
            logging.getLogger("afk.gui").warning("failed to persist widget position: %s", exc)

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
        # 必须启动倒计时：历史缺陷是这里只 exec() 不 start_countdown()，
        # 定时器从未跑起来，提示框界面上写着「30 秒后未选择将自动运行」，
        # 实际会一直卡在那里等人点按钮（用户离开电脑就永远挂不上机）。
        dialog.start_countdown()
        dialog.exec()
        decision = dialog.decision
        if decision == DECISION_NONE:
            # 兜底：exec() 若异常返回（窗口被系统强制关闭、Qt 异常等），
            # decision 会停在 NONE。这里明确按「倒计时结束」的语义处理，
            # 而不是让 NONE 隐式落进「全部任务」分支——行为一致但意图清晰。
            decision = decide_on_timeout()
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
        self._sync_settings_editor()
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
        # 退出前把还没落盘的小组件位置写掉（拖动后立刻关程序否则会丢）
        timer = getattr(self, "_widget_pos_timer", None)
        if timer is not None and timer.isActive():
            self._flush_widget_pos()
        # 定时启动的防抖写盘同理：改完时间立刻关窗口不能丢
        sched_timer = getattr(self, "_schedule_timer", None)
        if sched_timer is not None and sched_timer.isActive():
            try:
                self._flush_schedule()
            except Exception as exc:
                logging.getLogger("afk.gui").warning("flush schedule on quit failed: %s", exc)
        # 设置页的数值框走 400ms 防抖，关窗口时同样要补一次落盘
        settings_page = getattr(self, "settings_page", None)
        if settings_page is not None:
            try:
                settings_page.flush_pending()
            except Exception as exc:
                logging.getLogger("afk.gui").warning("flush settings on quit failed: %s", exc)
        # 窗口尺寸同理：拖完边框立刻关窗口，防抖里的那次写盘就丢了
        geo_timer = getattr(self, "_geometry_timer", None)
        if geo_timer is not None and geo_timer.isActive():
            try:
                self._flush_window_geometry()
            except Exception as exc:
                logging.getLogger("afk.gui").warning("flush window size on quit failed: %s", exc)
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
        if not self._initial_geometry_applied:
            self._initial_geometry_applied = True
            self._fit_to_screen()
        self._sync_cards_height()
        if self._did_intro:
            return
        self._did_intro = True
        # 入场动画：窗口整体淡入，各卡片错峰级联淡入
        fade_in_window(self, duration=280)
        for i, card in enumerate(self.cards.values()):
            fade_in_widget(card, duration=300, delay=120 + i * 70)

    def _fit_to_screen(self):
        """首次显示时保证窗口完整落在屏幕可用区域内。

        历史缺陷：窗口尺寸写死 1360x900 且从不校正位置。在 1366x768 的笔记本上
        底边会掉出屏幕 132px（底部按钮点不到），1920x1080 + 125% 缩放的机器上
        同样越界；而小屏上窗口还会被系统贴在左上角之外。
        """
        try:
            screen = self.screen()
        except Exception:
            screen = None
        if screen is None:
            return
        try:
            avail = screen.availableGeometry()
        except Exception:
            return
        if avail.width() <= 0 or avail.height() <= 0:
            return
        # 首选尺寸要按「窗口实际所在的那块屏」重算：多显示器上主屏和当前屏
        # 可用区域可能差很多，只在 __init__ 用 primaryScreen 算一次会算错。
        # 还要扣掉标题栏/边框：resize() 设的是客户区，frameGeometry() 才是用户在
        # 屏幕上看到的那个矩形。1366x768 上边框约 30px，不扣就会「客户区刚好放得下、
        # 窗口底边还是掉出屏幕」——历史缺陷正是这么漏掉的。
        frame = self.frameGeometry()
        extra_w = max(0, frame.width() - self.width())
        extra_h = max(0, frame.height() - self.height())
        avail_w = max(0, avail.width() - extra_w)
        avail_h = max(0, avail.height() - extra_h)
        limit_w = max(640, int(avail_w * 0.98))
        limit_h = max(480, int(avail_h * 0.98))
        saved = self._saved_window_size()
        if saved is not None:
            # 用户上次调过的尺寸优先，只做「不能超出屏幕」的夹紧。
            width = min(saved[0], limit_w)
            height = min(saved[1], limit_h)
        else:
            want_w, want_h = _preferred_window_size(screen)
            width = min(max(want_w, self.width()), limit_w)
            height = min(max(want_h, self.height()), limit_h)
        if (width, height) != (self.width(), self.height()):
            # 这次 resize 是程序自己做的（夹到屏幕内），不该被当成用户调尺寸
            # 记进 settings.yaml，否则屏幕切换一次就把用户原来的偏好覆盖掉了。
            self._geometry_restoring = True
            try:
                self.resize(width, height)
            finally:
                self._geometry_restoring = False
        # resize 之后重新取一次 frameGeometry：尺寸变了，边框后的实际矩形也变了，
        # 用旧的算居中会偏出屏幕。
        frame = self.frameGeometry()
        x = avail.left() + max(0, (avail.width() - frame.width()) // 2)
        y = avail.top() + max(0, (avail.height() - frame.height()) // 2)
        # 居中之后仍要夹一次：窗口比可用区域还大时 max(0, …) 会把窗口贴到左上角，
        # 但底边照样越界，只能靠位置往上顶到可见范围内。
        x = min(x, avail.left() + max(0, avail.width() - frame.width()))
        y = min(y, avail.top() + max(0, avail.height() - frame.height()))
        self.move(x, y)

    def _saved_window_size(self):
        """上次退出时的窗口尺寸（settings.yaml 的 gui.window_size）。

        历史缺陷：窗口尺寸从不落盘，每次启动都回到首选值。用户把窗口拖成自己
        习惯的大小（比如旁边开着模拟器，只占左半屏），关掉再开又变回 1360x900，
        得重新调一遍。
        """
        gui_cfg = self.settings.get("gui") or {}
        raw = gui_cfg.get("window_size")
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None
        try:
            width, height = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            return None
        # 太小说明是坏数据/被别的程序写坏，按默认尺寸处理，别把窗口开成一个洞。
        if width < 640 or height < 480:
            return None
        return width, height

    def _on_window_resized(self):
        """尺寸变化后合并落盘（拖动边框时 resizeEvent 会连续触发）。"""
        if not self._initial_geometry_applied or self._geometry_restoring:
            return
        self._geometry_timer.start()

    def _flush_window_geometry(self):
        """把当前窗口尺寸写进 settings.yaml。

        只记客户区尺寸（self.width()/height()），不记位置：多显示器/改分辨率后
        记住的坐标很容易落到屏幕外，位置每次按当前屏幕居中更稳（桌面小组件
        的坐标有 _is_on_some_screen 校验，窗口不必再走一遍那套）。
        最大化/全屏时不记，否则下次启动会先闪一下最大化尺寸再回到普通尺寸。
        """
        self._geometry_timer.stop()
        if self.isMaximized() or self.isFullScreen() or self.isMinimized():
            return
        width, height = self.width(), self.height()
        if width < 640 or height < 480:
            return
        # 先重读磁盘再改：这份 settings 是开窗时读进内存的，期间 CLI / 另一进程 /
        # 用户手改文件都会写 settings.yaml，整份写回内存那份等于把它们一起抹掉。
        settings = config.settings()
        gui_cfg = settings.setdefault("gui", {})
        current = gui_cfg.get("window_size")
        if list(current or []) == [width, height]:
            self.settings = settings
            return
        gui_cfg["window_size"] = [width, height]
        try:
            config.save_yaml(config.SETTINGS_YAML, settings)
            self.settings = settings
        except Exception as exc:
            logging.getLogger("afk.gui").warning("failed to persist window size: %s", exc)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._sync_cards_height()
        self._on_window_resized()

    def _sync_cards_height(self):
        """按窗口当前宽度重算项目卡区域应有的高度（折成几行就有多高）。

        必须在窗口真正 resize 之后算：布局求解阶段宽度还不可知，让它去猜
        就会把 5 张卡当成竖排，把窗口最小高度顶到 1165（见 FlowHost 注释）。

        这里只把「折行后需要的高度」报给滚动区当 sizeHint，不设固定高度：
        空间够时滚动区长到刚好放下（看起来和没有滚动区一样），不够时布局把
        它压到最小高度并出现滚动条。写成 setFixedHeight 会和窗口高度互相
        反馈——钉高滚动区 → 窗口最小高度变大 → 布局把窗口撑高 → 再钉更高。
        """
        host = getattr(self, "cards_host", None)
        if host is None:
            return
        width = host.width()
        if width <= 0:
            central = self.centralWidget()
            width = (central.width() if central is not None else self.width()) - 24
        host.sync_height(width)
        scroll = getattr(self, "cards_scroll", None)
        if scroll is None:
            return
        # 单行高度与行间距交给滚动区，让它在空间不够时把高度吸附到整行，
        # 避免出现「第二行卡片被切在按钮中间」的半行。
        scroll.set_row_metrics(host.single_row_height(), host.flow().v_spacing())
        # 最小高度至少一整行：CARDS_MIN_HEIGHT(120) 比一行卡片还矮，窗口被压到
        # 最小时卡片会停在「未运行」那一行，四个按钮全被切掉（截图见
        # logs/_ui_r14/min_all.png）。这里设最小高度而不是固定高度——固定高度
        # 会和窗口高度互相反馈（钉高 → 窗口最小高度变大 → 布局撑高 → 再钉更高）。
        row_height = host.single_row_height()
        if row_height > 0:
            floor = max(CARDS_MIN_HEIGHT, row_height)
            if scroll.minimumHeight() != floor:
                scroll.setMinimumHeight(floor)
        desired = host.rows_height_for(width)
        if desired <= 0:
            return
        # 空间不够时把可用高度吸附到「能完整放下的整行数」：卡片高度一致，
        # 半行会把按钮切在中间，看起来像界面画坏了。多余的空间让给下面的
        # 日志区，滚动条则明确提示下面还有卡片。
        available = self._cards_available_height()
        if available > 0:
            desired = min(desired, scroll.snapped_height(available))
        if scroll.content_height() != desired:
            scroll.set_content_height(desired)

    def _cards_available_height(self):
        """本页面里除卡片区之外的开销之外，还能留给卡片区多少像素。

        用当前已求解的几何逐项累加（resizeEvent 里布局已经算过一轮），
        而不是拿常量去猜——头部高度会随副标题换行变化。

        日志页签所在的 QSplitter 是可伸展的那一个：算余量时按它可以被压到的
        最小高度计（SPLITTER_MIN_HEIGHT），否则它当前占的高度会把余量吃掉，
        卡片区永远被判成「只放得下一行」。
        """
        overview = self.stack.widget(0) if hasattr(self, "stack") else None
        layout = overview.layout() if overview is not None else None
        scroll = getattr(self, "cards_scroll", None)
        if layout is None or scroll is None:
            return 0
        splitter = self._log_splitter
        margins = layout.contentsMargins()
        used = margins.top() + margins.bottom()
        spacing = max(0, layout.spacing())
        countable = 0
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is scroll:
                continue
            countable += 1
            if widget is None:
                used += item.minimumSize().height()
            elif widget is splitter:
                used += SPLITTER_MIN_HEIGHT
            else:
                used += max(widget.height(), widget.minimumSizeHint().height())
        used += spacing * max(0, countable)
        return max(0, overview.height() - used)

    def closeEvent(self, event):
        if (self.settings.get("gui", {}) or {}).get("minimize_to_tray", False):
            event.ignore()
            self.hide()
            self.tray.showMessage("Auto-AFK", "已最小化到托盘", QSystemTrayIcon.Information, 2000)
        else:
            self._quit()


class CardsScrollArea(QScrollArea):
    """项目卡区域的滚动容器。

    卡片等宽定宽，窗口变窄会折行、变多行就更高。把它直接塞进 QVBoxLayout
    会让它按内容高度报 sizeHint，主窗口最小高度随之涨到 821px —— 1366x768
    笔记本（可用 728）上窗口底部按钮直接掉出屏幕。

    做成滚动区后最小高度只剩一行的量；但 QScrollArea 的默认 sizeHint 偏小，
    空间充足时它也只想占 160px（大屏上白白出现滚动条）。所以这里自己报
    「内容需要的高度」作为 sizeHint，布局空间够就完整显示，不够才压小滚动。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._content_height = 0
        self._row_height = 0
        self._row_spacing = 0

    def set_row_metrics(self, row_height, row_spacing):
        """记下单行卡片高度与行间距，用来把可用高度吸附到整行。"""
        row_height = int(row_height)
        row_spacing = int(row_spacing)
        if (row_height, row_spacing) == (self._row_height, self._row_spacing):
            return
        self._row_height = row_height
        self._row_spacing = row_spacing
        self.updateGeometry()

    def snapped_height(self, available):
        """把可用高度吸附到「能完整显示的整行数」上。

        历史表现：1338px 宽时卡片折成 3+2，滚动区被压到 396px，第二行卡片
        正好被切在按钮中间（只露出半个卡），看起来像界面画坏了。这里把高度
        取成整行的倍数，多余的空间让给下面的日志页签。
        """
        available = int(available)
        if available <= 0 or self._row_height <= 0:
            return max(CARDS_MIN_HEIGHT, available)
        step = self._row_height + self._row_spacing
        rows = max(1, (available + self._row_spacing) // step)
        snapped = rows * step - self._row_spacing
        if snapped > available:
            rows = max(1, rows - 1)
            snapped = rows * step - self._row_spacing
        return max(CARDS_MIN_HEIGHT, snapped)

    def rows_for(self, height):
        """给定高度能完整放下几行（不足一行按 1 行算）。"""
        if self._row_height <= 0:
            return 0
        step = self._row_height + self._row_spacing
        if height < self._row_height:
            return 0
        return max(1, (int(height) + self._row_spacing) // step)

    def set_content_height(self, height):
        height = int(height)
        if height == self._content_height:
            return
        self._content_height = height
        self.updateGeometry()

    def content_height(self):
        return self._content_height

    def sizeHint(self):
        hint = super().sizeHint()
        if self._content_height <= 0:
            return hint
        return QSize(hint.width(), self._content_height)

    def snap_height_for(self, available):
        """测试用：给定可用高度时滚动区最终会占的高度。"""
        return self.snapped_height(available)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        # 知道了单行高度以后，最小高度至少得放得下一整行：CARDS_MIN_HEIGHT 只有
        # 120px，比一行卡片（168px）还矮，窗口被压到最小时卡片会被切在「未运行」
        # 那一行，四个按钮全看不见（截图见 logs/_ui_r14/min_all.png）。
        # 留出整行的高度，宁可少放一行也不要把卡片切一半。
        floor = CARDS_MIN_HEIGHT
        if self._row_height > 0:
            floor = max(floor, self._row_height)
        height = max(hint.height(), floor)
        return QSize(hint.width(), height)


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
