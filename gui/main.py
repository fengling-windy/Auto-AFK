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

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTime, QTimer, Signal
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
    AnimatedButton,
    AnimatedPill,
    AnimatedTabWidget,
    CardBackground,
    ElidedLabel,
    FlowHost,
    HoverGlowCard,
    ParallelLinks,
    StatusDot,
    add_shadow,
    apply_theme,
    current_theme,
    enable_smooth_scroll,
    fade_in_widget,
    fade_in_window,
    links_color,
)
from gui.idle_prompt import (
    DECISION_BACKGROUND,
    DECISION_NONE,
    DECISION_STOP,
    IdlePromptDialog,
    decide_on_timeout,
)
from gui.nav_rail import NavRail
from gui.project_status_page import ProjectStatusPage
from gui.settings_page import SettingsPage
from gui.widget import DesktopWidget
from gui import icons, themes

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


def make_icon(theme=None):
    """应用图标：深/浅主题各一套配色（浅色主题下用深底会像一块黑补丁）。

    颜色走 themes 的设计令牌，和 QSS 用同一套值；没传 theme 时取当前主题。
    """
    token = themes.resolve(theme or current_theme())
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(token["panel"]))
    painter.drawRoundedRect(0, 0, 64, 64, 14, 14)
    painter.setBrush(QColor(token["accent"]))
    painter.drawEllipse(8, 8, 48, 48)
    painter.setPen(QColor(token["on_accent"]))
    painter.setFont(QFont("Arial", 17, QFont.Bold))
    painter.drawText(pix.rect(), Qt.AlignCenter, "AFK")
    painter.end()
    return QIcon(pix)


# apply_spec 的哨兵值：区分「没传 on_gui」和「显式传 None（这个项目没有界面入口）」
_UNSET = object()


class ProjectCard(HoverGlowCard):
    # 按住卡片移动超过这么多像素才算拖拽（避免点按钮/微抖动误触发重排）
    DRAG_THRESHOLD = 8

    # 卡片被拖动、移动、松手；参数是该卡的 key / 全局坐标
    drag_started = Signal(str)
    drag_moved = Signal(str, QPoint)
    drag_finished = Signal(str)

    def __init__(self, key, spec, color, on_start, on_stop, on_logs, on_gui=None,
                 on_parallel=None, on_remove=None):
        super().__init__()
        self.key = key
        self.spec = spec
        self.color = color
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_logs = on_logs
        self.on_gui = on_gui
        self.on_parallel = on_parallel
        self.on_remove = on_remove
        self._parallel = False
        self._parallel_pending = False
        self._press_pos = None
        self._dragging = False
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
        # 用 ElidedLabel 而不是 QLabel：QLabel 的 minimumSizeHint 等于整段文字宽度，
        # 注册表里写个长 display_name（实测 128 字 → 1232px）会把项目卡和整个窗口
        # 的最小宽度一起顶出去。
        self.name_label = ElidedLabel(display_name.split("（")[0], min_width=72)
        self.name_label.setObjectName("cardName")
        if display_name != self.name_label.text():
            self.name_label.setToolTip(display_name)
        head.addWidget(self.dot)
        head.addWidget(self.name_label, 1)
        # 拖动把手（只能靠把手拖动卡片，避免点按钮时误拖）
        self.drag_handle = QLabel()
        self.drag_handle.setObjectName("cardGrip")
        self.drag_handle.setPixmap(icons.make("grip", "#6b7688", 14).pixmap(14, 14))
        self.drag_handle.setCursor(Qt.OpenHandCursor)
        self.drag_handle.setToolTip("按住拖动可调整运行顺序")
        self.drag_handle.installEventFilter(self)
        head.addWidget(self.drag_handle)
        # 删除卡片（只是从总览里藏起来，设置页里能恢复）
        self.close_btn = AnimatedButton("", variant="mini")
        self.close_btn.setObjectName("cardClose")
        self.close_btn.setIcon(icons.make("close", "#7f8aa3", 12))
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setToolTip("从总览里移除这个项目（可在设置页恢复）")
        self.close_btn.clicked.connect(lambda: self.on_remove(self.key) if self.on_remove else None)
        head.addWidget(self.close_btn)
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
        # 并行：点一次进入「待配对」，再点相邻的另一张卡就组成一组
        self.parallel_btn = AnimatedButton("并行", variant="parallel")
        self.parallel_btn.setObjectName("parallel")
        self.parallel_btn.setCheckable(True)
        self.parallel_btn.setToolTip("与相邻的项目同时运行：两张相邻的卡各点一次")
        self.parallel_btn.clicked.connect(
            lambda: self.on_parallel(self.key) if self.on_parallel else None
        )
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
        buttons.addWidget(self.parallel_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    # -- 并行状态 -----------------------------------------------------------
    def set_parallel(self, active, pending=False):
        """active=已在并行组里，pending=点了一次正等相邻的那张。"""
        self._parallel = bool(active)
        self._parallel_pending = bool(pending) and not active
        self.parallel_btn.blockSignals(True)
        self.parallel_btn.setChecked(self._parallel or self._parallel_pending)
        self.parallel_btn.blockSignals(False)
        if self._parallel:
            text, tip = "并行中", "已与相邻项目同时运行，再点一次取消"
        elif self._parallel_pending:
            text, tip = "待配对", "再点相邻的另一张卡的「并行」按钮即可组成一组"
        else:
            text, tip = "并行", "与相邻的项目同时运行：两张相邻的卡各点一次"
        self.parallel_btn.setText(text)
        self.parallel_btn.setToolTip(tip)

    def is_parallel(self):
        return self._parallel

    def is_parallel_pending(self):
        return self._parallel_pending

    # -- 拖动排序 -----------------------------------------------------------
    def eventFilter(self, obj, ev):
        if obj is self.drag_handle:
            if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._press_pos = ev.globalPosition().toPoint()
                self._dragging = False
                return True
            if ev.type() == QEvent.MouseMove and self._press_pos is not None:
                pos = ev.globalPosition().toPoint()
                if not self._dragging:
                    delta = pos - self._press_pos
                    if delta.manhattanLength() >= self.DRAG_THRESHOLD:
                        self._dragging = True
                        self.drag_handle.setCursor(Qt.ClosedHandCursor)
                        self.drag_started.emit(self.key)
                if self._dragging:
                    self.drag_moved.emit(self.key, pos)
                return True
            if ev.type() == QEvent.MouseButtonRelease and self._press_pos is not None:
                was_dragging = self._dragging
                self._press_pos = None
                self._dragging = False
                self.drag_handle.setCursor(Qt.OpenHandCursor)
                if was_dragging:
                    self.drag_finished.emit(self.key)
                return True
        return super().eventFilter(obj, ev)

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
    # 后台任务（更新/自检）结束时把按钮恢复可用。worker 在后台线程跑，
    # 不能直接碰控件，统一用 Signal 排队回 GUI 线程。
    task_finished = Signal(str)
    # 「界面」按钮在后台线程里拉起项目窗口，成功/失败都要写一行日志。
    # 后台线程直接写 log_view 属跨线程操作控件，必须走 Signal。
    gui_log = Signal(str, str, str)

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
        # 总览页里被「移除」掉的项目（只是不显示，注册表里仍在）。
        # 单独存一份到 settings 的 gui.hidden_projects，设置页能把它们放回来——
        # 直接删注册表里的项目太重了，用户常常只是想暂时收起两个。
        self._hidden_projects = [
            str(k) for k in ((self.settings.get("gui") or {}).get("hidden_projects") or [])
        ]
        # 并行组：与 chain.parallel_blocks 同步，卡片之间的连线按它画
        self._parallel_blocks = self._read_parallel_blocks()
        # 点了「并行」但还没配成对的那张卡
        self._parallel_pending = None
        self._drag_key = None
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
        self.task_finished.connect(self._end_task)
        self.gui_log.connect(self._append_log)
        try:
            from afk_core.housekeeping import cleanup_logs

            threading.Thread(
                target=lambda: cleanup_logs(self.bus, config.settings()),
                daemon=True,
                name="afk-housekeeping",
            ).start()
        except Exception as exc:
            # 日志清理跑不起来不该拦住开窗，但要留痕：用户看到 logs/ 一直变大、
            # 报告目录越堆越多时，能从这里查到是不是清理线程没起来。
            logging.getLogger("afk.gui").warning("housekeeping thread failed: %s", exc)

        # 刚启动就自动更新全部项目（pre_run.at_startup，默认开）
        try:
            if self.supervisor.run_startup_updates():
                self._append_log("core", "info", "启动更新：正在后台更新全部项目...")
        except Exception as exc:
            # 启动更新失败以前是静默的：用户以为「已经在更新」，实际一个都没跑。
            self._append_log("core", "warn", "启动更新没能开始：{0}".format(exc))
            logging.getLogger("afk.gui").warning("startup updates failed: %s", exc)

        if self.auto_run_requested:
            self._append_log("core", "info", "--autorun 已启用，2 秒后自动开始一键挂机")
            QTimer.singleShot(2000, self.start_chain)
        elif (self.settings.get("gui", {}) or {}).get("autostart_chain_on_launch"):
            QTimer.singleShot(2000, self.start_chain)

        # 静默启动勾选的项目界面（原生独立打开，只拉进程、不嵌入）
        silent_keys = [
            str(k) for k in ((self.settings.get("gui") or {}).get("silent_open_projects") or [])
            if k in (config.registry().get("projects") or {})
            and (config.registry().get("projects") or {}).get(k, {}).get("gui")
        ]
        if silent_keys:
            self._append_log("core", "info", "启动时打开项目界面: {0}".format(", ".join(silent_keys)))
            QTimer.singleShot(3000, lambda: self._open_silent_pages(silent_keys))

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        # 背景图垫在最底层：单独一个控件而不是在 central 上自绘，避免 QSS 背景
        # 与手绘图混在一起（会出现「一半黑一半图」）。见 CardBackground 注释。
        self.background = CardBackground(central)
        self.background.setGeometry(central.rect())
        self.background.lower()
        self.background.set_fill(themes.resolve(self._theme_name())["window"])
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # -- 左侧导航栏 --------------------------------------------------------
        # 历史：这里曾为每个带 gui 段的项目挂一个 page:XXX 导航项，点进去用
        # QWindow.fromWinId 把项目自己的窗口「抓」进本窗口。那套做法在真机上
        # 问题很多（窗口被抢走后布局错乱、跨进程输入法/焦点异常、关窗后进程
        # 残留），已整体移除。现在只保留「一键挂机」这一套编排入口，项目界面
        # 一律由卡片上的「界面」按钮用原生方式独立打开。
        self.nav = NavRail()
        self.nav.add_item("overview", "总览", icon="home")
        self.nav.add_item("projects", "项目状态", icon="status")
        self.nav.add_item("settings", "设置", icon="settings")
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
        # 副标题列出全部项目名，注册表里名字一长就把窗口最小宽度顶出去
        # （实测 1409px）；用 ElidedLabel 让它按可用宽度裁，完整文本进 tooltip。
        subtitle = ElidedLabel(self._subtitle_text(), min_width=120)
        subtitle.setObjectName("subtitle")
        subtitle.setToolTip(self._subtitle_text())
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
        add_shadow(self.chain_btn, alpha=120, blur=18, dy=3)
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

        # 标题不叫「项目状态」：左侧导航里已经有一页叫「项目状态」（版本/仓库/
        # 参数汇总），这里放的是可操作的项目卡片，重名会让用户以为点错了页。
        cards_title = QLabel("挂机项目")
        cards_title.setObjectName("sectionTitle")
        root.addWidget(cards_title)
        # 用 FlowLayout 而非 QHBoxLayout：5 张卡横排会把窗口最小宽度顶到 1592px，
        # 1366/1440 宽的笔记本上窗口撑出屏幕。流式布局按可用宽度自动折行；
        # 外层 FlowHost 负责把「折成几行就有多高」报给父布局，否则第二行会被
        # 画到下面的页签上面（窄窗口下实测重叠）。
        cards_host = FlowHost(h_spacing=12, v_spacing=12)
        cards_row = cards_host.flow()
        self.cards_host = cards_host
        # 卡片之间画并行连线的覆盖层：挂在 cards_host 上、跟它一样大，
        # 鼠标事件穿透。放在这里而不是卡片上，是因为跨卡片的线会被卡片边界裁掉。
        self.links = ParallelLinks(cards_host)
        self.links.set_color(links_color())
        for key, spec in self._ordered_project_items():
            if key in self._hidden_projects:
                continue
            card = ProjectCard(
                key, spec, self._project_color(key),
                self.start_project, self.stop_project, self.show_project_logs,
                on_gui=self.open_project_page if spec.get("gui") else None,
                on_parallel=self.toggle_parallel,
                on_remove=self.hide_card,
            )
            self.cards[key] = card
            cards_row.addWidget(card)
            card.drag_started.connect(self._on_card_drag_started)
            card.drag_moved.connect(self._on_card_drag_moved)
            card.drag_finished.connect(self._on_card_drag_finished)
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

        # -- 项目状态页 ------------------------------------------------------------
        self.projects_page = ProjectStatusPage(
            registry_getter=lambda: config.registry(),
            color_getter=self._project_color,
            log_fn=self._append_log,
        )
        self.stack.addWidget(self.projects_page)

        # -- 设置页 ----------------------------------------------------------------
        self.settings_page = SettingsPage(log_fn=self._append_log)
        self.settings_page.changed.connect(self._on_settings_changed)
        self.settings_page.theme_changed.connect(self._on_theme_changed)
        self.settings_page.restore_requested.connect(self.restore_card)
        self.settings_page.project_added.connect(self._on_project_added)
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
        """导航栏切换页面（总览 / 项目状态 / 设置）。"""
        page = None
        if key == "overview":
            page = self.stack.widget(0)
        elif key == "projects":
            page = self.projects_page
            page.reload()
        elif key == "settings":
            self.settings_page.reload_from_disk()
            page = self.settings_page
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
        fade_in_widget(page, duration=150)

    def open_project_page(self, key):
        """项目卡片「界面」按钮 → 用原生方式独立打开该项目的界面窗口。

        历史：这里曾切到「项目嵌入页」，用 QWindow.fromWinId + createWindowContainer
        把项目自己的窗口抓进本窗口显示。那套嵌入在真机上有连锁问题（窗口被
        抢走后布局错乱、跨进程焦点/输入法异常、关本窗口后子进程残留），已整体
        移除。现在就是拉一个独立的进程，窗口归它自己管，跟手动双击一样。
        """
        self._open_project_gui(key)

    def _open_project_gui(self, key):
        """后台线程拉起项目的配置界面（不阻塞 UI，不嵌入）。"""
        def worker():
            try:
                ok = self.supervisor.open_project_gui(key)
            except Exception as exc:
                self.gui_log.emit(key, "error", "打开界面失败: {0}".format(exc))
                return
            if ok:
                self.gui_log.emit(key, "info", "已打开 {0} 的界面".format(key))
            else:
                self.gui_log.emit(key, "warn", "无法打开 {0} 的界面：注册表里没有配置 gui 入口".format(key))

        threading.Thread(target=worker, daemon=True, name="afk-gui-" + key).start()

    # -- 卡片顺序 / 归属 -------------------------------------------------------
    def _registry_projects(self):
        return config.registry().get("projects", {}) or {}

    def _ordered_project_items(self):
        """按运行顺序（chain.order）列出 (key, spec)，未列出的垫底。

        注册表里的键顺序是 yaml 的书写顺序，不等于运行顺序——chain.order 才是
        supervisor 排程的依据（apply_chain_order）。总览上卡片的先后必须和它
        一致，否则用户拖完卡片看到的顺序和真正跑的顺序是两套。
        """
        projects = self._registry_projects()
        order = [str(k) for k in (((self.settings.get("chain") or {}).get("order")) or [])]
        rank = {k: i for i, k in enumerate(order)}
        keys = sorted(projects.keys(), key=lambda k: rank.get(k, len(rank)))
        return [(k, projects[k]) for k in keys]

    def _save_chain_order(self, keys, *, log_text=None):
        """把卡片顺序写回 settings.yaml 的 chain.order。"""
        keys = [str(k) for k in keys]

        def mutate(settings):
            chain = settings.setdefault("chain", {})
            if not isinstance(chain, dict):
                chain = {}
                settings["chain"] = chain
            chain["order"] = list(keys)

        self._mutate_settings(mutate)
        if log_text:
            self._append_log("core", "info", log_text)
        self._sync_parallel_state()

    def _apply_card_order(self, keys):
        """按给定 key 顺序重排卡片控件（不重建，保留运行状态与信号）。"""
        host = getattr(self, "cards_host", None)
        cards = getattr(self, "cards", {}) or {}
        if host is None:
            return
        ordered = [cards[k] for k in keys if k in cards]
        ordered += [c for k, c in cards.items() if k not in keys]
        host.set_order(ordered)
        self._sync_parallel_state()
        self._sync_cards_height()

    def hide_card(self, key):
        """卡片上的 × —— 从总览收起（注册表不动，设置页可恢复）。"""
        card = (getattr(self, "cards", {}) or {}).get(key)
        if card is None:
            return
        hidden = [k for k in self._hidden_projects if k != key] + [key]

        def mutate(settings):
            gui_cfg = settings.setdefault("gui", {})
            if not isinstance(gui_cfg, dict):
                gui_cfg = {}
                settings["gui"] = gui_cfg
            gui_cfg["hidden_projects"] = list(hidden)

        self._hidden_projects = hidden
        self._mutate_settings(mutate)
        # 并行组里如果有它，一并移除（不然连线指向一张不存在的卡）
        self._parallel_blocks = [b for b in self._parallel_blocks if key not in b]
        self._write_parallel_blocks()
        flow = card.parentWidget()
        host = getattr(self, "cards_host", None)
        if host is not None:
            host.flow().removeWidget(card)
        elif flow is not None:
            flow.layout().removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        self.cards.pop(key, None)
        if self._parallel_pending == key:
            self._parallel_pending = None
        self._sync_parallel_state()
        self._sync_cards_height()
        page = getattr(self, "settings_page", None)
        if page is not None:
            page.reload_from_disk()
        self._append_log("core", "info", "已从总览移除 {0}（设置页可恢复）".format(key))

    def restore_card(self, key):
        """把隐藏的项目放回总览（设置页「已隐藏的项目」用）。"""
        if key in self._hidden_projects:
            self._hidden_projects = [k for k in self._hidden_projects if k != key]

            def mutate(settings):
                gui_cfg = settings.setdefault("gui", {})
                if not isinstance(gui_cfg, dict):
                    gui_cfg = {}
                    settings["gui"] = gui_cfg
                gui_cfg["hidden_projects"] = list(self._hidden_projects)

            self._mutate_settings(mutate)
        self._reload_registry()

    # -- 并行分组 -------------------------------------------------------------
    def _read_parallel_blocks(self):
        blocks = (self.settings.get("chain") or {}).get("parallel_blocks") or []
        out = []
        for block in blocks if isinstance(blocks, list) else []:
            keys = [str(k) for k in (block or []) if isinstance(k, (str, int))]
            if len(keys) >= 2:
                out.append(keys)
        return out

    def _write_parallel_blocks(self, log_text=None):
        blocks = [list(b) for b in self._parallel_blocks if len(b) >= 2]

        def mutate(settings):
            chain = settings.setdefault("chain", {})
            if not isinstance(chain, dict):
                chain = {}
                settings["chain"] = chain
            chain["parallel_blocks"] = blocks

        self._mutate_settings(mutate)
        if log_text:
            self._append_log("core", "info", log_text)

    def _card_index(self, key):
        keys = [k for k in (getattr(self, "cards", {}) or {}).keys()]
        host = getattr(self, "cards_host", None)
        if host is not None:
            widgets = host.ordered_widgets()
            visible = [w.key for w in widgets if hasattr(w, "key")]
            if key in visible:
                return visible.index(key)
        return keys.index(key) if key in keys else -1

    def visible_card_keys(self):
        """总览上卡片当前的实际顺序（测试与内部逻辑都用它）。"""
        host = getattr(self, "cards_host", None)
        if host is not None:
            return [w.key for w in host.ordered_widgets() if hasattr(w, "key")]
        return list((getattr(self, "cards", {}) or {}).keys())

    def _adjacent_pair(self, first, second):
        """两张卡在**运行顺序与界面顺序**上都必须相邻，才能组成并行组。

        「只能相邻」是用户明确要求：把不相邻的两个项目设成并行等于跳过中间
        那个项目的先后约束，实际执行顺序会变得没法解释。所以两边都要紧挨着。
        """
        visible = self.visible_card_keys()
        if first in visible and second in visible:
            i, j = visible.index(first), visible.index(second)
            return abs(i - j) == 1
        order = [str(k) for k in (((self.settings.get("chain") or {}).get("order")) or [])]
        if first in order and second in order:
            return abs(order.index(first) - order.index(second)) == 1
        return True

    def parallel_groups(self):
        """当前有效的并行组（组员都还显示着，且仍然是相邻的）。

        历史缺陷：只检查组员「还显示着」，不检查是否仍然相邻。用户在总览上把
        一张卡拖到别处之后，原本相邻的并行组被拆开了，界面照样显示「并行中」
        并画着连线，但 supervisor 的 merge_parallel_blocks 会因为「项目之间
        隔着别的项目」把整块作废——界面说同时跑，实际是顺序跑，用户看不出来。
        这里按卡片当前顺序校验连续性，不连续的组直接不算数（连线也跟着消失）。
        """
        visible = self.visible_card_keys()
        visible_set = set(visible)
        groups = []
        for block in self._parallel_blocks:
            members = [k for k in block if k in (getattr(self, "cards", {}) or {})]
            if len(members) < 2 or not all(k in visible_set for k in members):
                continue
            indices = sorted(visible.index(k) for k in members)
            if indices != list(range(indices[0], indices[0] + len(indices))):
                continue
            # 按卡片顺序返回，连线才连相邻的那几张
            groups.append(sorted(members, key=visible.index))
        return groups

    def _prune_parallel_blocks(self):
        """把因为拖动/隐藏而不再相邻的并行组从配置里清掉。

        parallel_groups() 已经会把不相邻的组判为无效（界面不画连线、不显示
        「并行中」），但 chain.parallel_blocks 里还留着那一对。留着会有两个
        后果：一是 supervisor 每次跑链都会告警「并行块已忽略」刷日志；二是
        用户再点其中一张卡的「并行」会命中 toggle_parallel 的「已在某个组里
        → 退出」分支，日志写「取消并行」，可界面上那张卡明明没在并行中。
        """
        blocks = self.parallel_groups()
        kept = {tuple(sorted(g)) for g in blocks}
        before = [list(b) for b in self._parallel_blocks]
        dropped = [b for b in before if tuple(sorted(b)) not in kept]
        if not dropped:
            return
        self._parallel_blocks = [list(g) for g in blocks]
        self._write_parallel_blocks()
        self._append_log(
            "core", "warn",
            "并行组已取消：{0} 里的项目不再相邻".format("、".join("+".join(b) for b in dropped)),
        )

    def toggle_parallel(self, key):
        """卡片「并行」按钮：第一次点记下待配对，第二次点相邻的另一张组成一组。"""
        # 先把已经不相邻的失效组清掉：否则下面「已在某个组里 → 取消」的分支
        # 会把一张界面上根本没显示「并行中」的卡当成在并行组里。
        self._prune_parallel_blocks()
        pending = self._parallel_pending
        # 已在某个组里 → 再点一次退出该组
        for block in list(self._parallel_blocks):
            if key in block:
                block.remove(key)
                if len(block) < 2:
                    self._parallel_blocks = [b for b in self._parallel_blocks if b is not block]
                self._parallel_pending = None
                self._write_parallel_blocks(
                    log_text="取消并行：{0}".format(key)
                )
                self._sync_parallel_state()
                return

        if pending is None or pending == key:
            self._parallel_pending = None if pending == key else key
            self._sync_parallel_state()
            return

        if not self._adjacent_pair(pending, key):
            self._append_log(
                "core", "warn",
                "只能把相邻的两个项目设为并行：{0} 和 {1} 之间还隔着别的项目".format(pending, key),
            )
            self._parallel_pending = None
            self._sync_parallel_state()
            return

        # 与已有组共享端点时合并成一组（A-B 之后再点 C，变成 A-B-C）
        target = None
        for block in self._parallel_blocks:
            if pending in block or key in block:
                target = block
                break
        if target is None:
            self._parallel_blocks.append([pending, key])
        else:
            if pending not in target:
                target.append(pending)
            if key not in target:
                target.append(key)
            # 组内按卡片顺序排一次，连线才连相邻的
            visible = self.visible_card_keys()
            target.sort(key=lambda k: visible.index(k) if k in visible else 999)
        self._parallel_pending = None
        self._write_parallel_blocks(
            log_text="并行：{0} 与 {1} 将同时运行".format(pending, key)
        )
        self._sync_parallel_state()

    def _sync_parallel_state(self):
        """把并行组的视觉状态刷到卡片与连线层上。"""
        groups = self.parallel_groups()
        member_of = {}
        for block in groups:
            for key in block:
                member_of[key] = block
        for key, card in (getattr(self, "cards", {}) or {}).items():
            active = key in member_of
            card.set_parallel(active, pending=(self._parallel_pending == key))
        links = getattr(self, "links", None)
        host = getattr(self, "cards_host", None)
        if links is None or host is None:
            return
        links.setGeometry(host.rect())
        widgets = []
        for block in groups:
            widgets.append([self.cards.get(k) for k in block])
        active = []
        if self._parallel_pending and self._parallel_pending in self.cards:
            active = [self.cards[self._parallel_pending]]
        links.set_groups(widgets, active=active)
        links.raise_()

    # -- 卡片拖动排序 ---------------------------------------------------------
    def _on_card_drag_started(self, key):
        self._drag_key = key
        card = (getattr(self, "cards", {}) or {}).get(key)
        if card is not None:
            card.setWindowOpacity(0.75)
            card.raise_()

    def _on_card_drag_moved(self, key, global_pos):
        """拖动中：按光标位置把这张卡插到最近的那张卡前面/后面。"""
        host = getattr(self, "cards_host", None)
        cards = getattr(self, "cards", {}) or {}
        if host is None:
            return
        pos = host.mapFromGlobal(global_pos)
        widgets = [w for w in host.ordered_widgets() if hasattr(w, "key")]
        moving = cards.get(key)
        if moving is None:
            return
        target_index = 0
        for index, widget in enumerate(widgets):
            if widget is moving:
                continue
            geo = widget.geometry()
            # 落在卡片左半边就插到它前面，右半边插到它后面
            if pos.x() < geo.center().x() or pos.y() < geo.top():
                target_index = index
                break
            target_index = index + 1
        current = host.index_of(moving)
        if current != target_index:
            host.flow().removeWidget(moving)
            # removeWidget 之后序号可能变化，重新算一次插入点
            widgets = [w for w in host.ordered_widgets() if hasattr(w, "key")]
            insert_at = target_index
            if current < target_index:
                insert_at = max(0, target_index - 1)
            host.insert_widget(min(insert_at, len(widgets)), moving)
            host.sync_height(host.width())
            self._sync_parallel_state()

    def _on_card_drag_finished(self, key):
        card = (getattr(self, "cards", {}) or {}).get(key)
        if card is not None:
            card.setWindowOpacity(1.0)
        self._drag_key = None
        keys = self.visible_card_keys()
        # 拖动只改显示顺序时也要落盘：supervisor 的排程顺序就是 chain.order，
        # 不写回去用户会觉得「拖了没生效」。
        self._apply_card_order(keys)
        self._save_chain_order(keys, log_text="运行顺序已更新：{0}".format(" → ".join(keys)))
        # 拖动可能把原本相邻的并行组拆开，配置里那一对要跟着作废
        self._prune_parallel_blocks()


    def _reload_project_colors(self):
        """settings.yaml 的 gui.project_colors 改了之后让卡片/日志配色跟上。

        历史缺陷：project_colors 只在 MainWindow.__init__ 里读一次并 snapshot 成
        self.project_colors。用户在「设置」tab 里改某个项目的颜色（或在 yaml 里加
        gui.project_colors）再保存，卡片顶部色条以及实时日志里该项目的前缀颜色
        全是开窗时那份旧配色，看起来像没保存。
        """
        custom = (self.settings.get("gui") or {}).get("project_colors") or {}
        colors = dict(PROJECT_COLORS)
        colors.update({str(k): str(v) for k, v in custom.items()})
        self.project_colors = colors
        for key, card in (getattr(self, "cards", {}) or {}).items():
            color = self._project_color(key)
            if card.color != color:
                card.apply_spec(card.spec, color)

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
        self._apply_theme(self._theme_name(), persist=False)

    def _theme_name(self):
        """当前主题名（settings.yaml 的 gui.theme，未知值回退默认）。"""
        gui_cfg = self.settings.get("gui") or {}
        return themes.normalize_theme(gui_cfg.get("theme"))

    def _apply_theme(self, name, persist=True):
        """换肤：重刷 QSS、重取按钮配色、重设背景底板与连线颜色。

        控件颜色不是全部走 QSS：AnimatedButton 在 paintEvent 里自绘、卡片色条是
        运行时写死的字符串，所以换主题必须把卡片重建一遍（apply_spec 只刷新文字
        与色条，不动按钮 variant 快照）。其余情况（同一主题、只改背景图）不重建，
        避免每次改设置都把卡片连同运行状态一起重来。
        """
        name = themes.normalize_theme(name)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, name)
        background = getattr(self, "background", None)
        gui_cfg = self.settings.get("gui") or {}
        if background is not None:
            background.set_fill(themes.resolve(name)["window"])
            background.set_image(str(gui_cfg.get("background_image") or "").strip())
            try:
                opacity = int(gui_cfg.get("background_opacity", 100))
            except (TypeError, ValueError):
                opacity = 100
            background.set_opacity(opacity)
        nav = getattr(self, "nav", None)
        if nav is not None and hasattr(nav, "set_icon_color"):
            token = themes.resolve(name)
            nav.set_icon_color(token["nav_text"], accent=token["accent"])
        links = getattr(self, "links", None)
        if links is not None:
            links.set_color(links_color())
        # 标题栏/任务栏图标是位图，换肤后要重画一张，否则浅色主题下还是一块深底
        self.setWindowIcon(make_icon(name))
        changed = getattr(self, "_applied_theme", None) != name
        self._applied_theme = name
        if changed:
            # 卡片里的按钮是 AnimatedButton（自绘），换肤后重建才能拿到新配色。
            self._rebuild_cards()
        else:
            self._sync_parallel_state()

    def _on_theme_changed(self, name):
        """设置页切换主题：立刻换肤（settings 已由设置页写盘并 emit changed）。"""
        self.settings = config.settings()
        self._apply_theme(name)

    def _on_project_added(self, key):
        """设置页新增了挂机项目：重读注册表，让新卡片出现。"""
        self._reload_registry()
        self._append_log("core", "info", "已新增项目 {0}".format(key))

    def _open_silent_pages(self, keys):
        """启动时按设置打开（不嵌入、不隐藏）勾选项目的界面窗口。"""
        for key in keys:
            self._open_project_gui(key)

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
            # config.read_text 会退避重试：别的线程刚做了原子替换时目标文件会被
            # 短暂占用，直接 read_text 会抛 PermissionError，界面就显示
            # 「# 读取失败: …」——用户以为文件坏了，其实只是撞了一下。
            text = config.read_text(Path(path))
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
            # 原子写：设置页的合并落盘、窗口尺寸保存、这里的手工编辑保存都可能
            # 撞在同一份文件上；write_text 先截断再写，另一边正读就会拿到空文件。
            config.write_text_atomic(Path(path), text)
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

    def _make_card(self, key, spec):
        """建一张项目卡并接好所有信号（三处建卡路径共用，避免参数漏传）。"""
        card = ProjectCard(
            key, spec, self._project_color(key),
            self.start_project, self.stop_project, self.show_project_logs,
            on_gui=self.open_project_page if spec.get("gui") else None,
            on_parallel=self.toggle_parallel,
            on_remove=self.hide_card,
        )
        card.drag_started.connect(self._on_card_drag_started)
        card.drag_moved.connect(self._on_card_drag_moved)
        card.drag_finished.connect(self._on_card_drag_finished)
        return card

    def _rebuild_cards(self):
        """按当前注册表 + chain.order + 隐藏列表整体重建卡片区。

        为什么整体重建：卡片里的 AnimatedButton 是在 paintEvent 里自绘的，配色
        是构造时抓的快照（themes.BUTTONS），换主题后不重建就还是旧色。注册表变更
        与「恢复隐藏卡片」也走这条路，避免维护三份增量同步逻辑。
        """
        host = getattr(self, "cards_host", None)
        if host is None:
            return
        flow = host.flow()
        for card in (getattr(self, "cards", {}) or {}).values():
            flow.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.cards = {}
        for key, spec in self._ordered_project_items():
            if key in self._hidden_projects:
                continue
            card = self._make_card(key, spec)
            self.cards[key] = card
            flow.addWidget(card)
        self._sync_parallel_state()
        self._sync_cards_height()

    def _drop_stale_project_keys(self, projects):
        """注册表里已经不存在的项目，从运行顺序、隐藏列表、并行组里清掉。

        历史缺陷：在「项目注册表」页签里删掉一个项目后，`chain.order`、
        `gui.hidden_projects`、`chain.parallel_blocks` 三处仍留着它的键。
        后果：链按 chain.order 排程，走到不存在的项目会命中「跳过未知项目」
        分支（每次跑链都刷一条告警）；「设置 → 已隐藏的项目」里永远挂着一个
        已经删掉的项目，点「放回总览」毫无反应；并行组里那一对也会被
        supervisor 判成「隔着别的项目」整块作废。

        只有真发现残留时才写盘，避免每次重新加载都改一遍 settings.yaml。
        返回被清掉的键（给调用方写日志）。
        """
        alive = set(projects)
        order = [str(k) for k in (((self.settings.get("chain") or {}).get("order")) or [])]
        hidden = [str(k) for k in (self._hidden_projects or [])]
        blocks = [list(b) for b in (self._parallel_blocks or [])]
        stale = sorted(
            (set(order) | set(hidden) | {k for b in blocks for k in b}) - alive
        )
        if not stale:
            return []
        new_order = [k for k in order if k in alive]
        new_hidden = [k for k in hidden if k in alive]
        new_blocks = []
        for block in blocks:
            kept = [k for k in block if k in alive]
            if len(kept) >= 2:
                new_blocks.append(kept)

        def mutate(settings):
            chain_cfg = settings.setdefault("chain", {})
            if not isinstance(chain_cfg, dict):
                chain_cfg = {}
                settings["chain"] = chain_cfg
            chain_cfg["order"] = list(new_order)
            chain_cfg["parallel_blocks"] = [list(b) for b in new_blocks]
            gui_cfg = settings.setdefault("gui", {})
            if not isinstance(gui_cfg, dict):
                gui_cfg = {}
                settings["gui"] = gui_cfg
            gui_cfg["hidden_projects"] = list(new_hidden)

        # 先更新内存里的副本，_mutate_settings 会重读文件并替换 self.settings
        self._hidden_projects = new_hidden
        self._parallel_blocks = new_blocks
        self._mutate_settings(mutate)
        self._append_log(
            "core", "info",
            "注册表里已经没有 {0}，已从运行顺序/隐藏列表/并行组里一并清掉".format("、".join(stale)),
        )
        return stale

    def _reload_registry(self):
        """「项目注册表」保存后让界面跟上新注册表。

        历史缺陷：这里原来什么都没做。改完注册表一保存，文件是对的，但界面、
        supervisor、设置页全都还在用开窗时读到的那一份——用户新增的项目不出现
        在总览页和左侧导航、删掉的项目卡片还在、改过的显示名/启用状态不生效。
        重新读盘后按差异同步两处：项目卡、设置页（含运行顺序）。左侧导航只有
        「总览」「项目状态」「设置」三项，不再随注册表增删。
        """
        registry = config.registry()
        projects = registry.get("projects", {}) or {}
        self.supervisor.registry = registry
        # -- 1. 项目卡：缺的补上，多的撤掉，其余更新文案/颜色 ----------------
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
            if flow is None or key in self._hidden_projects:
                continue
            card = self._make_card(key, spec)
            cards[key] = card
            flow.addWidget(card)
        for key in [k for k in cards if k not in projects]:
            card = cards.pop(key)
            if flow is not None:
                flow.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        # 注册表里删掉的项目，在 chain.order / hidden_projects / parallel_blocks
        # 里的残留键也要一起清（否则设置页的「已隐藏」永远挂着一个幽灵项）
        self._drop_stale_project_keys(projects)
        # 卡片顺序要跟 chain.order（用户拖动改的顺序）一致，并把并行连线重画
        self._apply_card_order(self.visible_card_keys())
        # -- 2. 日志过滤器、设置页、副标题 -----------------------------------
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
            text = self._subtitle_text()
            label.setText(text)
            label.setToolTip(text)
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

        if not self._begin_task("update"):
            # 历史缺陷：连点「立即更新」会并发起多个更新线程（实测连点三次
            # 三个线程同时 clone，同一个 git 工作树被三个进程同时操作，报
            # 「index.lock 存在」；耗时长又看不出在干什么）。现在同一时刻
            # 只允许一个更新任务，按钮在运行期间变灰。
            return

        def worker():
            try:
                settings = config.settings()
                pre = settings.get("pre_run") or {}
                if pre.get("update_emulator", True):
                    MuMuController(self.bus, settings).update_check()
                if pre.get("update_projects", True):
                    Updater(self.bus, settings).update_all_projects()
            finally:
                self.task_finished.emit("update")

        self._append_log("core", "info", "开始手动更新（项目 + 模拟器）...")
        threading.Thread(target=worker, daemon=True, name="afk-manual-update").start()

    def run_mumu_updater(self):
        from afk_core.emulator import MuMuController

        if not self._begin_task("update"):
            return

        def worker():
            try:
                settings = config.settings()
                emu_cfg = settings.setdefault("pre_run", {}).setdefault("emulator", {})
                emu_cfg["updater_mode"] = "gui"
                MuMuController(self.bus, settings).update_check()
            finally:
                self.task_finished.emit("update")

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

    def _log_at_bottom(self, slack=4):
        """日志区当前是不是停在最底部（用户在追最新几行）。"""
        bar = self.log_view.verticalScrollBar()
        return bar.value() >= bar.maximum() - slack

    def _scroll_log_to_bottom(self):
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _append_log(self, project, level, message, autoscroll=True, emit_event=True):
        # 先记下「追加之前」是不是在底部：一旦 appendHtml 把内容顶上去，
        # maximum 就变了，那时再判断永远为假。
        follow = self._log_at_bottom() if autoscroll else False
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
        # 历史缺陷：只要勾着「自动滚动」，每来一行都强制拉到底。用户往上翻着
        # 看历史（或正在复制一段报错）时，链还在跑、日志还在刷，视图被不断
        # 拽回底部，那段历史根本读不了。现在只在「追加前本来就在底部」时才
        # 跟着滚；用户自己翻上去了就让他停在那里，新内容照样进 event_buffer。
        if autoscroll and follow and self.autoscroll_check.isChecked():
            self._scroll_log_to_bottom()
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
            except Exception as exc:
                # 事件总线挂了不能影响界面日志渲染（这里在 GUI 主线程上，
                # 抛出去就是一次未捕获异常），但也不能一声不响——排查问题时
                # 「界面有日志、events.jsonl 里没有」最难查。
                logging.getLogger("afk.gui").warning("emit gui log event failed: %s", exc)

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
            self._scroll_log_to_bottom()

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
        follow = self._log_at_bottom()
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
        if drained and follow and self.autoscroll_check.isChecked():
            self._scroll_log_to_bottom()
        snapshot = self.supervisor.status_snapshot()
        for key, card in self.cards.items():
            card.update_state(snapshot["runs"].get(key))
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
                except Exception as exc:
                    # 只是刷新「今天是否已完成」那行提示，失败不该影响主循环；
                    # 但要留痕，否则用户会看到过期的完成状态却查不到原因。
                    logging.getLogger("afk.gui").warning(
                        "refresh completion label failed: %s", exc
                    )

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
        if not self._begin_task("doctor"):
            return
        self.doctor_view.setPlainText("环境自检运行中...")
        self.tabs.setCurrentIndex(self.tabs.count() - 1)

        def worker():
            try:
                results = doctor.run_checks()
            except Exception as exc:
                results = [{"status": "fail", "name": "doctor", "detail": str(exc)}]
            self.doctor_result.emit(results)
            self.task_finished.emit("doctor")

        threading.Thread(target=worker, daemon=True, name="afk-doctor").start()

    def _begin_task(self, kind):
        """标记一个长任务开始，期间禁用对应按钮并挡住重复触发。

        历史缺陷：更新 / 自检 / MuMu 更新按钮点下去只是起一个后台线程，
        按钮不禁用也不去重。连点三下就起三个线程，同一批 git 工作树被并发
        操作（实测三线程并发 clone/pull 同一目录），用户还看不出它到底在
        干什么、点了有没有反应。
        """
        buttons = {
            "update": ("update_btn", "mumu_btn"),
            "doctor": ("doctor_btn",),
        }.get(kind, ())
        running = getattr(self, "_running_tasks", None)
        if running is None:
            running = self._running_tasks = set()
        if kind in running:
            self._append_log("core", "info", "已有同类任务在运行，忽略这次点击")
            return False
        running.add(kind)
        for name in buttons:
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(False)
        return True

    def _end_task(self, kind):
        running = getattr(self, "_running_tasks", None)
        if running is not None:
            running.discard(kind)
        buttons = {
            "update": ("update_btn", "mumu_btn"),
            "doctor": ("doctor_btn",),
        }.get(kind, ())
        for name in buttons:
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(True)

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
        """把主窗口调回前台（托盘恢复、空闲提示、卡片「界面」按钮都会走这里）。

        历史缺陷：无条件 showNormal()。用户把窗口最大化后最小化到托盘，再点托盘
        恢复，窗口会退回到「最大化之前的小尺寸」，看起来像被缩小了、还要重新
        最大化一次（空闲提示弹出时同样会把最大化窗口打回小窗）。这里只解除
        「最小化」这一位，最大化/全屏原样保留。
        """
        state = self.windowState()
        if state & Qt.WindowMinimized:
            self.setWindowState(state & ~Qt.WindowMinimized)
        self.show()
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
        except Exception as exc:
            # 后台线程里的信号投递失败（窗口正在析构等）不该把线程打挂，
            # 但要留痕：否则「空闲到点没弹提示」永远查不出原因。
            logging.getLogger("afk.gui").warning("idle trigger dispatch failed: %s", exc)

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
        # 背景底板不在布局里（在 central 里 lower 到底层），尺寸得手动跟
        central = self.centralWidget()
        background = getattr(self, "background", None)
        if central is not None and background is not None:
            background.setGeometry(central.rect())
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
        # 并行连线层是浮在卡片区上的独立控件（不在布局里），尺寸得跟着宿主走。
        # 历史缺陷：只有 _sync_parallel_state 会 setGeometry，而它只在点「并行」
        # 或改顺序时调用——用户拖动窗口边缘把窗口拉大/缩小后，连线层还停在
        # 旧尺寸（实测窗口从 1360 拉到 1500 后，host 宽 1276 而连线层仍是 827），
        # 相邻卡片之间的弧线就画在错误的位置、甚至整段画不出来。
        links = getattr(self, "links", None)
        if links is not None:
            links.setGeometry(host.rect())
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
    # 主题在开窗前就要生效：MainWindow 的卡片、按钮、背景底板都是按当前主题
    # 取色构造的，先建窗口再换肤会有一帧旧配色（且 AnimatedButton 的快照已定）。
    _startup_theme = themes.normalize_theme(
        (config.settings().get("gui") or {}).get("theme")
    )
    apply_theme(app, _startup_theme)
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
