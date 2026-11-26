import logging
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

import yaml

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from afk_core import config, doctor, idle, listval, numval, project_ops
from afk_core.events import EventBus
from afk_core.idle import IdleWatcher
from afk_core.repair import RepairEngine
from afk_core.scheduler import Scheduler
from afk_core.supervisor import Supervisor, normalize_chain_tasks

from gui.modern import (
    AnimatedButton,
    AnimatedPill,
    CardBackground,
    ElidedLabel,
    add_shadow,
    apply_theme,
    current_theme,
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
from gui.cards import (
    BUSY_STATUSES,
    CARDS_MIN_HEIGHT,
    STATUS_DETAIL_FALLBACK,
    STATUS_TEXT,
    CardsScrollArea,
    ProjectCard,
    _theme_status_colors,
)
from gui.advanced_panel import AdvancedPanel
from gui.live_log_columns import CORE_KEY, CORE_TITLE, MultiColumnLogBoard
from gui.nav_rail import NavRail
from gui.project_status_page import ProjectConfigPage, ProjectStatusPage
from gui.run_order_page import RunOrderPage
from gui.run_stage import RunStageWidget
from gui.settings_page import SettingsPage
from gui.widget import DesktopWidget
from gui import icons, themes

# 深色主题的状态色（兼容旧引用；真正用的时候请用 _theme_status_colors()，
# 它会跟着当前主题走）。
STATUS_COLORS = themes.STATUS_PALETTE[themes.DEFAULT_THEME]



# 切过滤条件时最多重画多少行日志。event_buffer 上限 5000，全量重画实测
# 422ms（GUI 主线程僵住半秒），2000 行 156ms、1500 行 125ms；取 2000 作为
# 「够翻回去看」与「不卡」的折中。更早的记录仍在 event_buffer 与 events.jsonl。
LOG_RERENDER_LIMIT = 2000

# 每轮轮询最多取多少条事件、花多少毫秒在上面。历史缺陷：上限写死 500 条，
# 而 300ms 一轮只能消化 1667 条/秒——2026-11-11 MaaEnd 刷屏时实测峰值
# 14901 条/秒（`events.jsonl` 按秒统计），订阅队列（上限 5000）几秒就被灌满，
# 之后的新事件直接被丢弃：界面上看到的是几分钟前的日志，用户以为程序卡死。
# 改成「按时间预算收干」，洪峰时也不至于让 _poll 独占 GUI 主线程。
LOG_DRAIN_LIMIT = 4000
LOG_DRAIN_BUDGET_MS = 25.0
# 一轮最多往日志区画多少行。逐条 appendHtml 实测 0.05ms × 行数，批量拼一次
# "<br>".join 后 500 行约 29ms；洪峰期间只保留最近的若干行，其余仍在
# event_buffer 与 events.jsonl 里（切过滤器时能看到），并写一行提示说明跳过了多少。
LOG_RENDER_BATCH_LIMIT = 400


# 项目身份色（兼容旧引用；真正取色请用 _theme_project_colors() / _project_color()，
# 它们会跟着当前主题走）。
PROJECT_COLORS = themes.PROJECT_PALETTE[themes.DEFAULT_THEME]
DEFAULT_PALETTE = themes.PROJECT_FALLBACK_PALETTE[themes.DEFAULT_THEME]

LEVEL_COLORS = {
    "error": themes.status_color(themes.DEFAULT_THEME, "log_error"),
    "warn": themes.status_color(themes.DEFAULT_THEME, "log_warn"),
}

CHAIN_TEXT = {
    "idle": "空闲",
    "running": "运行中",
    "success": "全部完成",
    "failed": "有失败",
    "stopped": "已停止",
}

CHAIN_COLORS = {key: themes.chain_color(themes.DEFAULT_THEME, key) for key in CHAIN_TEXT}


def _theme_chain_colors(theme=None):
    return themes.chain_color_table(theme or current_theme())


def _theme_level_colors(theme=None):
    return themes.level_color_table(theme or current_theme())


def _theme_project_colors(custom=None, theme=None):
    """当前主题下的项目身份色表（卡片顶部色条 + 日志前缀）。

    历史缺陷：PROJECT_COLORS 是一份写死的亮色板，浅色主题的白卡/白底日志区上
    对比度只有 1.4–3.5（BetterGI #ffd54f 1.41、MaaEnd #ffb74d 1.73），色条还在
    但前缀文字基本糊进背景。改成按主题取表。

    注意 gui.project_colors 里出厂就写着那 8 个深色默认色，不能当成「用户自定义」
    直接盖掉主题色板（否则浅色主题永远是亮色）。只有和出厂默认值不同的项才算
    用户真改过，才覆盖。
    """
    name = themes.normalize_theme(theme or current_theme())
    colors = dict(themes.PROJECT_PALETTE.get(name, themes.PROJECT_PALETTE[themes.DEFAULT_THEME]))
    for key, value in (custom or {}).items():
        key, value = str(key), str(value)
        if themes.is_default_project_color(key, value):
            continue
        colors[key] = value
    return colors


def _project_color_for(key, theme=None, custom=None):
    """取单个项目的身份色；没登记过也没自定义时按名字哈希落到本主题备用板。"""
    name = themes.normalize_theme(theme or current_theme())
    override = (custom or {}).get(str(key))
    if override and not themes.is_default_project_color(key, override):
        return str(override)
    color = themes.project_color(name, key)
    if color is not None:
        return color
    return themes.project_fallback_color(name, key)


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


_APP_ICON_CACHE = {}


def make_icon(theme=None):
    """应用图标：深/浅主题各一套配色，并按主题名缓存位图避免重复光栅化。"""
    norm = themes.normalize_theme(theme or current_theme())
    cached = _APP_ICON_CACHE.get(norm)
    if cached is not None:
        return cached
    token = themes.resolve(norm)
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(token["panel"]))
    painter.drawRoundedRect(0, 0, 64, 64, 15, 15)
    painter.setBrush(QColor(token["accent"]))
    painter.drawEllipse(9, 9, 46, 46)
    painter.setPen(QColor(token["on_accent"]))
    painter.setFont(QFont("Inter", 16, QFont.Bold))
    painter.drawText(pix.rect(), Qt.AlignCenter, "AFK")
    painter.end()
    icon = QIcon(pix)
    _APP_ICON_CACHE[norm] = icon
    return icon


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

    # 拖动过程中邻居让位用的补间时长（毫秒）。160ms 是「松手归位」那种一次性
    # 过渡的合适值，但拖动时每次鼠标移动都会重启一轮，160ms 追不上手速，邻居
    # 看起来像「松手才变」；缩到 90ms 才跟手，同时保留了滑动感而非瞬移。
    DRAG_ANIM_MS = 90
    # 响应式断点：< 820px 左侧导航自动收缩为 56px 纯图标栏；< 640px 工具栏切换纵向堆叠
    COMPACT_NAV_WIDTH = 820
    MOBILE_BREAKPOINT = 640

    def __init__(self):
        super().__init__()
        self.settings = config.settings()
        self.auto_run_requested = "--autorun" in sys.argv
        self.start_in_tray = "--tray" in sys.argv
        self.no_widget = "--no-widget" in sys.argv
        custom_colors = (self.settings.get("gui") or {}).get("project_colors") or {}
        # 按开窗时读到的主题取色板（main() 已经在建窗口前 apply_theme 过）
        self._applied_theme = self._active_theme()
        self.project_colors = _theme_project_colors(custom_colors, self._applied_theme)
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
        self._registry_cache = config.registry()
        self._did_intro = False
        self._last_chain_for_completion = None
        self._last_done_today = None
        self._last_chain_busy = None
        self._ts_cache_sec = -1
        self._ts_cache_str = ""
        self._narrow_mode = False
        # 懒加载的次级页面实例（「运行顺序」「项目配置」「设置 + 高级设置」首屏不实例化，
        # 切页或访问对应 property 时才挂载进 stack，降低冷启动耗时与控件树体积）
        self._order_slot = None
        self._order_page_inst = None
        self._projects_slot = None
        self._projects_page_inst = None
        self._settings_slot = None
        self._settings_page_inst = None
        self._advanced_inst = None
        self.event_buffer = deque(maxlen=5000)
        self._skipped_since_notice = 0
        self._skip_notice_at = 0.0
        self._bad_schedule_time = None
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
        # 空闲状态快照缓存（见 _idle_status_cached）：读 settings.yaml 要 4ms，
        # 不能按 300ms 轮询的节奏读。
        self._idle_status_cache = {}
        self._idle_status_at = 0.0
        self._idle_cooldown_deadline = 0.0
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
        self.nav.add_item("order", "运行顺序", icon="order")
        self.nav.add_item("projects", "项目配置", icon="status")
        self.nav.add_item("settings", "设置", icon="settings")
        self.nav.finish()
        self.nav.navigate.connect(self._navigate)
        root_layout.addWidget(self.nav)

        # -- 右侧页面堆栈 --------------------------------------------------------
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, 1)

        overview = QWidget()
        overview.setObjectName("central")
        self._overview_page = overview
        root = QVBoxLayout(overview)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(12)
        self._overview_layout = root

        header = QFrame()
        header.setObjectName("header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 13, 18, 11)
        header_layout.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(12)
        self._header_top_layout = top
        logo = QLabel()
        logo.setPixmap(make_icon().pixmap(36, 36))
        self._header_logo = logo
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("Auto-AFK")
        title.setObjectName("title")
        # 副标题列出全部项目名，注册表里名字一长就把窗口最小宽度顶出去
        # （实测 1409px）；用 ElidedLabel 让它按可用宽度裁，完整文本进 tooltip。
        _subtitle_text = self._subtitle_text()
        subtitle = ElidedLabel(_subtitle_text, min_width=100)
        subtitle.setObjectName("subtitle")
        subtitle.setToolTip(_subtitle_text)
        # 存引用：注册表改过之后副标题要跟着重新生成（项目数量变了）
        self._subtitle_label = subtitle
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(11)
        brand_row.addWidget(logo)
        brand_row.addLayout(title_box, 1)
        top.addLayout(brand_row, 1)

        primary_actions = QHBoxLayout()
        primary_actions.setSpacing(8)
        self.chain_pill = AnimatedPill("空闲", _theme_chain_colors()["idle"])
        self.chain_btn = AnimatedButton("一键挂机", variant="primary")
        self.chain_btn.setObjectName("primary")
        add_shadow(self.chain_btn, alpha=105, blur=16, dy=2)
        self.stop_all_btn = AnimatedButton("全部停止", variant="danger")
        self.stop_all_btn.setObjectName("danger")
        primary_actions.addWidget(self.chain_pill)
        primary_actions.addWidget(self.chain_btn)
        primary_actions.addWidget(self.stop_all_btn)
        top.addLayout(primary_actions)
        header_layout.addLayout(top)

        def _divider():
            line = QFrame()
            line.setObjectName("toolbarDivider")
            line.setFixedHeight(16)
            return line

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self._header_controls_layout = controls
        tools_group = QHBoxLayout()
        tools_group.setSpacing(6)
        self.doctor_btn = AnimatedButton("环境自检")
        self.patch_btn = AnimatedButton("校验补丁")
        self.open_logs_btn = AnimatedButton("打开日志")
        self.open_configs_btn = AnimatedButton("打开配置")
        self.update_btn = AnimatedButton("立即更新")
        self.mumu_btn = AnimatedButton("更新MuMu")
        div1, div2 = _divider(), _divider()
        self._toolbar_dividers = (div1, div2)
        for widget in (
            self.doctor_btn,
            self.patch_btn,
            div1,
            self.open_logs_btn,
            self.open_configs_btn,
            div2,
            self.update_btn,
            self.mumu_btn,
        ):
            tools_group.addWidget(widget)
        tools_group.addStretch(1)
        controls.addLayout(tools_group, 1)

        switches_group = QHBoxLayout()
        switches_group.setSpacing(8)
        self.pre_run_check = QCheckBox("运行前更新")
        pre_cfg = self.settings.get("pre_run", {}) or {}
        self.pre_run_check.setChecked(bool(pre_cfg.get("enabled", True)))
        self.pre_run_check.toggled.connect(self._toggle_pre_run)
        self.schedule_check = QCheckBox("定时启动")
        self.schedule_time = QTimeEdit()
        self.schedule_time.setDisplayFormat("HH:mm")
        schedule_cfg = self.settings.get("schedule", {}) or {}
        self.schedule_check.setChecked(bool(schedule_cfg.get("enabled")))
        self.schedule_time.setTime(self._parse_schedule_time(schedule_cfg.get("time", "04:00")))
        self._sync_schedule_tooltip()
        self.schedule_check.toggled.connect(self._toggle_schedule)
        self.schedule_time.timeChanged.connect(self._toggle_schedule)
        switches_group.addWidget(self.pre_run_check)
        switches_group.addWidget(self.schedule_check)
        switches_group.addWidget(self.schedule_time)
        controls.addLayout(switches_group)
        header_layout.addLayout(controls)
        root.addWidget(header)

        stage_title = QLabel("运行状态")
        stage_title.setObjectName("sectionTitle")
        root.addWidget(stage_title)
        self.run_stage = RunStageWidget()
        self.run_stage.set_gui_probe(self._can_open_gui)
        self.run_stage.stop_requested.connect(self.stop_project)
        self.run_stage.gui_requested.connect(self.open_project_page)
        self.run_stage.logs_requested.connect(self.show_project_logs)
        self.run_stage.open_order_requested.connect(lambda: self._navigate("order"))
        root.addWidget(self.run_stage, 0)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setMinimumHeight(SPLITTER_MIN_HEIGHT)
        self._log_splitter = splitter

        auto_open_logs = bool((self.settings.get("gui") or {}).get("auto_open_running_logs", True))
        self.log_board = MultiColumnLogBoard(auto_open=auto_open_logs)
        self.log_board.set_column_providers(self._project_color, self._backfill_lines_for)
        self.log_board.auto_open_toggled.connect(self._toggle_auto_open_logs)
        self.log_board.rerender_requested.connect(self._rerender_log)
        self._sync_log_catalog()
        self.log_board.open_column(CORE_KEY, CORE_TITLE, self._project_color(CORE_KEY))

        splitter.addWidget(self.log_board)
        root.addWidget(splitter, 1)

        self.stack.addWidget(overview)  # index 0 = 总览

        # -- 路由级懒加载占位槽（首屏只构建总览页） ----------------------------
        self._order_slot = QWidget()
        self._order_slot.setObjectName("central")
        self.stack.addWidget(self._order_slot)  # index 1 = 运行顺序（懒加载）

        self._projects_slot = QWidget()
        self._projects_slot.setObjectName("central")
        self.stack.addWidget(self._projects_slot)  # index 2 = 项目配置（懒加载）

        self._settings_slot = QWidget()
        self._settings_slot.setObjectName("central")
        self.stack.addWidget(self._settings_slot)  # index 3 = 设置（懒加载）

        self.nav.set_active("overview", animate=False)
        self.setCentralWidget(central)

        self._update_run_stage(self.supervisor.status_snapshot())

        self.chain_btn.clicked.connect(self.start_chain)
        self.stop_all_btn.clicked.connect(self.supervisor.stop_all)
        self.doctor_btn.clicked.connect(self.run_doctor)
        self.patch_btn.clicked.connect(self.verify_patches)
        self.open_logs_btn.clicked.connect(lambda: self._open_path(config.LOGS_DIR))
        self.open_configs_btn.clicked.connect(lambda: self._open_path(config.ROOT / "configs"))
        self.update_btn.clicked.connect(self.run_updates)
        self.mumu_btn.clicked.connect(self.run_mumu_updater)

    # -- 路由级懒加载属性访问器（兼容直接取 window.order_page / projects_page / settings_page / advanced） --
    def _ensure_order_page(self):
        if self._order_page_inst is not None:
            return self._order_page_inst
        page = RunOrderPage(
            registry_getter=lambda: config.registry(),
            settings_getter=lambda: config.settings(),
            color_getter=self._project_color,
            log_fn=self._append_log,
            runs_getter=lambda: (self.supervisor.status_snapshot().get("runs") or {}),
        )
        page.order_changed.connect(self._on_order_changed)
        page.start_requested.connect(self.start_project)
        page.stop_requested.connect(self.stop_project)
        page.logs_requested.connect(self.show_project_logs)
        page.gui_requested.connect(self.open_project_page)
        self._order_page_inst = page
        if self._order_slot is not None:
            idx = self.stack.indexOf(self._order_slot)
            if idx >= 0:
                was_current = self.stack.currentWidget() is self._order_slot
                self.stack.removeWidget(self._order_slot)
                self._order_slot.deleteLater()
                self._order_slot = None
                self.stack.insertWidget(idx, page)
                if was_current:
                    self.stack.setCurrentWidget(page)
        return page

    def _ensure_projects_page(self):
        if self._projects_page_inst is not None:
            return self._projects_page_inst
        page = ProjectConfigPage(
            registry_getter=lambda: config.registry(),
            color_getter=self._project_color,
            log_fn=self._append_log,
            bus=self.bus,
        )
        page.projects_changed.connect(self._reload_registry)
        page.open_gui_requested.connect(self.open_project_page)
        page.open_path_requested.connect(self._open_path)
        self._projects_page_inst = page
        if self._projects_slot is not None:
            idx = self.stack.indexOf(self._projects_slot)
            if idx >= 0:
                was_current = self.stack.currentWidget() is self._projects_slot
                self.stack.removeWidget(self._projects_slot)
                self._projects_slot.deleteLater()
                self._projects_slot = None
                self.stack.insertWidget(idx, page)
                if was_current:
                    self.stack.setCurrentWidget(page)
        return page

    def _ensure_advanced(self):
        if self._advanced_inst is None:
            self._advanced_inst = AdvancedPanel(
                save_text=self._save_text,
                load_text=self._load_text,
                open_path=self._open_path,
                run_doctor=self._run_doctor_task,
            )
        return self._advanced_inst

    def _ensure_settings_page(self):
        if self._settings_page_inst is not None:
            return self._settings_page_inst
        adv = self._ensure_advanced()
        page = SettingsPage(log_fn=self._append_log, advanced=adv, bus=self.bus)
        page.changed.connect(self._on_settings_changed)
        page.theme_changed.connect(self._on_theme_changed)
        page.open_path_requested.connect(self._open_path)
        page.reset_window_size_requested.connect(self._reset_window_size)
        self._settings_page_inst = page
        if self._settings_slot is not None:
            idx = self.stack.indexOf(self._settings_slot)
            if idx >= 0:
                was_current = self.stack.currentWidget() is self._settings_slot
                self.stack.removeWidget(self._settings_slot)
                self._settings_slot.deleteLater()
                self._settings_slot = None
                self.stack.insertWidget(idx, page)
                if was_current:
                    self.stack.setCurrentWidget(page)
        return page

    @property
    def order_page(self):
        return self._ensure_order_page()

    @order_page.setter
    def order_page(self, value):
        self._order_page_inst = value

    @property
    def projects_page(self):
        return self._ensure_projects_page()

    @projects_page.setter
    def projects_page(self, value):
        self._projects_page_inst = value

    @property
    def advanced(self):
        self._ensure_settings_page()
        return self._advanced_inst

    @advanced.setter
    def advanced(self, value):
        self._advanced_inst = value

    @property
    def settings_page(self):
        return self._ensure_settings_page()

    @settings_page.setter
    def settings_page(self, value):
        self._settings_page_inst = value

    # 兼容直接访问旧字段的调用点
    @property
    def log_view(self):
        return self.log_board.log_view

    @property
    def cards(self):
        return self._order_page_inst.cards if self._order_page_inst is not None else {}

    @property
    def cards_host(self):
        return self._order_page_inst.cards_host if self._order_page_inst is not None else None

    @property
    def links(self):
        return self._order_page_inst.links if self._order_page_inst is not None else None

    def _navigate(self, key):
        """导航栏切换页面（总览 / 运行顺序 / 项目配置 / 设置）。"""
        page = None
        if key == "overview":
            page = self.stack.widget(0)
        elif key == "order":
            already_built = self._order_page_inst is not None
            page = self._ensure_order_page()
            if already_built:
                page.update_runs(self.supervisor.status_snapshot().get("runs"))
        elif key == "projects":
            already_built = self._projects_page_inst is not None
            page = self._ensure_projects_page()
            if already_built:
                page.reload()
        elif key == "settings":
            already_built = self._settings_page_inst is not None
            page = self._ensure_settings_page()
            if already_built:
                page.reload_from_disk()
        else:
            return
        if page is None:
            return
        settings_page = self._settings_page_inst
        if settings_page is not None and self.stack.currentWidget() is settings_page:
            settings_page.flush_pending()
        self.stack.setCurrentWidget(page)
        self.nav.set_active(key, animate=self.isVisible())
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

    # -- 运行顺序 / 总览舞台 / 多列日志桥接 -----------------------------------
    def _registry_projects(self):
        return (self._registry_cache or config.registry()).get("projects", {}) or {}

    def _base_project_for(self, key):
        text = str(key or CORE_KEY)
        if text == CORE_KEY:
            return CORE_KEY
        entry = self.supervisor._task_entry_for(text) if hasattr(self, "supervisor") else None
        if entry is not None:
            return entry.get("project", text)
        if "#" in text:
            return text.split("#", 1)[0]
        return text

    def _can_open_gui(self, key):
        base = self._base_project_for(key)
        spec = self._registry_projects().get(base) or {}
        return bool(spec.get("gui"))

    def _task_display_info(self, task_id):
        """返回 (短显示名, 配置名)；同时兼容任务 ID（如 BAAH#2）和原始项目键。"""
        projects = self._registry_projects()
        tasks = project_ops.load_tasks(self.settings, {"projects": projects})
        entry = next((t for t in tasks if t["id"] == task_id), None)
        base = entry["project"] if entry is not None else self._base_project_for(task_id)
        spec = projects.get(base) or {}
        short = str(spec.get("display_name", base)).split("（")[0] or base
        cfg = (entry or {}).get("config") or ""
        if not cfg:
            uses_inst = base == "MaaEnd" or any("{instance}" in str(a) for a in (spec.get("args") or []))
            cfg = str(spec.get("instance" if uses_inst else "config_name") or "")
        if task_id != base and "#" in str(task_id):
            short = "{0} #{1}".format(short, str(task_id).split("#", 1)[1])
        return short, cfg

    def _log_col_info(self, key):
        if key == CORE_KEY:
            return CORE_TITLE, self._project_color(CORE_KEY)
        short, cfg = self._task_display_info(key)
        title = "{0} ({1})".format(short, cfg) if cfg else short
        return title, self._project_color(key)

    def _status_pair(self, status):
        palette = _theme_status_colors()
        return STATUS_TEXT.get(status, status), palette.get(status, palette["pending"])

    def _sync_log_catalog(self):
        board = getattr(self, "log_board", None)
        if board is None:
            return
        projects = self._registry_projects()
        tasks = project_ops.load_tasks(self.settings, {"projects": projects})
        items = []
        seen = set()
        for task in tasks:
            tid = task["id"]
            if tid in seen:
                continue
            seen.add(tid)
            items.append((tid, self._log_col_info(tid)[0]))
        for key, spec in projects.items():
            if key in seen:
                continue
            seen.add(key)
            short = str((spec or {}).get("display_name", key)).split("（")[0] or key
            items.append((key, short))
        board.set_catalog(items)

    def _backfill_lines_for(self, key):
        board = getattr(self, "log_board", None)
        if board is None:
            return []
        known_cols = set(board.column_keys()) | {key}
        lines = []
        for event in self.event_buffer:
            if not board.accepts(event):
                continue
            proj = str(event.get("project") or CORE_KEY)
            if key == CORE_KEY:
                if proj != CORE_KEY and proj in known_cols:
                    continue
            elif proj != key:
                continue
            lines.append(self._event_to_html(event))
        return lines[-LOG_RERENDER_LIMIT:]

    def _toggle_auto_open_logs(self, checked):
        def mutate(settings):
            settings.setdefault("gui", {})["auto_open_running_logs"] = bool(checked)

        self._mutate_settings(mutate)
        self._append_log("core", "info", "项目运行时自动开启日志列: {0}".format("开" if checked else "关"))

    def _on_order_changed(self):
        self.settings = config.settings()
        self.supervisor.settings = self.settings
        self._sync_log_catalog()
        self._update_run_stage(self.supervisor.status_snapshot())
        if self._settings_page_inst is not None:
            self._settings_page_inst.reload_from_disk()

    def _reset_window_size(self):
        def mutate(settings):
            gui_cfg = settings.setdefault("gui", {})
            gui_cfg.pop("window_size", None)

        self._mutate_settings(mutate)
        self._geometry_restoring = True
        try:
            self.resize(*_preferred_window_size())
            self._fit_to_screen()
        finally:
            self._geometry_restoring = False
        self._append_log("core", "info", "已重置为默认窗口尺寸")

    def _update_run_stage(self, snapshot):
        stage = getattr(self, "run_stage", None)
        if stage is None:
            return
        projects = self._registry_projects()
        tasks = [t["id"] for t in project_ops.load_tasks(self.settings, {"projects": projects}) if t.get("enabled", True)]
        runs = (snapshot or {}).get("runs") or {}
        queue = (snapshot or {}).get("chain_queue") or []
        chain_status = (snapshot or {}).get("chain_status") or "idle"
        stage.update_stage(
            tasks=tasks,
            runs=runs,
            queue=queue,
            chain_status=chain_status,
            name_of=lambda k: self._task_display_info(k)[0],
            config_of=lambda k: self._task_display_info(k)[1],
            color_of=self._project_color,
            status_of=self._status_pair,
        )

    def visible_card_keys(self):
        if self._order_page_inst is not None:
            return self._order_page_inst.visible_card_keys()
        return [t["id"] for t in project_ops.load_tasks(self.settings, self._registry_cache)]

    def parallel_groups(self):
        if self._order_page_inst is not None:
            return self._order_page_inst.parallel_groups()
        return []

    def toggle_parallel(self, key):
        return self.order_page.toggle_parallel(key)

    def _reload_project_colors(self):
        custom = (self.settings.get("gui") or {}).get("project_colors") or {}
        self.project_colors = _theme_project_colors(custom, self._active_theme())
        if self._order_page_inst is not None:
            runs = (self.supervisor.status_snapshot().get("runs") or {}) if hasattr(self, "supervisor") else {}
            self._order_page_inst.refresh_theme(self._project_color, runs)

    def _on_settings_changed(self):
        self.settings = config.settings()
        self.supervisor.settings = self.settings
        repair_engine = getattr(self, "repair", None)
        if repair_engine is None:
            repair_engine = getattr(self.supervisor, "repair", None)
        if repair_engine is not None:
            repair_engine.reload(self.settings)
        gui_cfg = self.settings.get("gui") or {}
        if self.widget is not None:
            want = bool(gui_cfg.get("widget_enabled", False))
            if self.widget.isVisible() != want:
                self.widget.setVisible(want)
        board = getattr(self, "log_board", None)
        if board is not None:
            want_auto = bool(gui_cfg.get("auto_open_running_logs", True))
            if board.auto_open_check.isChecked() != want_auto:
                board.auto_open_check.blockSignals(True)
                board.auto_open_check.setChecked(want_auto)
                board.auto_open_check.blockSignals(False)
        idle_cfg = self.settings.get("idle") or {}
        for action, want in (
            (getattr(self, "widget_action", None), bool(gui_cfg.get("widget_enabled", False))),
            (getattr(self, "idle_action", None), bool(idle_cfg.get("enabled", False))),
        ):
            if action is None or action.isChecked() == want:
                continue
            action.blockSignals(True)
            action.setChecked(want)
            action.blockSignals(False)
        self._reload_project_colors()
        self._sync_header_settings()
        self._sync_settings_editor()
        self._apply_theme(self._theme_name(), persist=False)
        self._sync_log_catalog()
        self._update_run_stage(self.supervisor.status_snapshot())
        if self._order_page_inst is not None:
            self._order_page_inst.reload_tasks(self.settings, self._registry_cache)

    def _theme_name(self):
        gui_cfg = self.settings.get("gui") or {}
        return themes.normalize_theme(gui_cfg.get("theme"))

    def _active_theme(self):
        return getattr(self, "_current_theme", None) or self._theme_name()

    def _apply_theme(self, name, persist=True):
        name = themes.normalize_theme(name)
        self._current_theme = name
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, name)
        background = getattr(self, "background", None)
        gui_cfg = self.settings.get("gui") or {}
        if background is not None:
            background.set_fill(themes.resolve(name)["window"])
            background.set_image(str(gui_cfg.get("background_image") or "").strip())
            background.set_opacity(
                numval.as_int(
                    gui_cfg.get("background_opacity", 100),
                    100,
                    low=0,
                    high=100,
                    what="gui.background_opacity",
                )
            )
        nav = getattr(self, "nav", None)
        if nav is not None and hasattr(nav, "set_icon_color"):
            token = themes.resolve(name)
            nav.set_icon_color(token["nav_text"], accent=token["accent"])
        self.setWindowIcon(make_icon(name))
        header_logo = getattr(self, "_header_logo", None)
        if header_logo is not None:
            header_logo.setPixmap(make_icon(name).pixmap(36, 36))
        if getattr(self, "tray", None) is not None:
            self.tray.setIcon(make_icon(name))
        custom_colors = (self.settings.get("gui") or {}).get("project_colors") or {}
        self.project_colors = _theme_project_colors(custom_colors, name)
        changed = getattr(self, "_applied_theme", None) != name
        self._applied_theme = name
        if changed:
            self._refresh_button_variants()
            runs = {}
            try:
                runs = (self.supervisor.status_snapshot().get("runs") or {}) if hasattr(self, "supervisor") else {}
            except Exception:
                runs = {}
            if getattr(self, "run_stage", None) is not None:
                self.run_stage.refresh_theme()
                self._update_run_stage(self.supervisor.status_snapshot())
            if getattr(self, "log_board", None) is not None:
                self.log_board.refresh_theme(self._project_color)
                if self.event_buffer:
                    self._rerender_log()
            if self._order_page_inst is not None:
                self._order_page_inst.refresh_theme(self._project_color, runs)
            if getattr(self, "widget", None) is not None:
                self.widget.refresh_theme()
            if self._advanced_inst is not None:
                self._advanced_inst.refresh_theme()
            if self._projects_page_inst is not None:
                self._projects_page_inst.refresh_theme()
            if self._settings_page_inst is not None:
                self._settings_page_inst.refresh_theme()

    def _refresh_button_variants(self):
        for button in self.findChildren(AnimatedButton):
            button.set_variant(button.variant_name)

    def _on_theme_changed(self, name):
        self.settings = config.settings()
        self._apply_theme(name)

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
        """把文件内容读进编辑器（面板搬进 gui/advanced_panel.py，脏标记归它管）。"""
        advanced = self._advanced_inst or self.advanced
        if advanced is None:
            return
        advanced.load_text(edit, path)
        if advanced._settings_edit is not None and edit is advanced._settings_edit:
            advanced.mark_settings_loaded()

    def _sync_settings_editor(self):
        """设置文件在别处被改后，若高级设置已创建且编辑器未脏就跟着刷新。"""
        advanced = self._advanced_inst
        if advanced is not None:
            advanced.sync_settings_editor()

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
                if self._settings_page_inst is not None:
                    self.settings_page.reload_from_disk()
                self._sync_header_settings()
                # 托盘勾选、桌面小组件可见性也要跟着文件走。
                self._on_settings_changed()
                # 刚写进去的就是编辑器里这一份，脏标记清掉，后续外部改动可以再同步
                advanced = self._advanced_inst
                if advanced is not None and advanced._settings_edit is not None and edit is advanced._settings_edit:
                    advanced.mark_settings_loaded()
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

    def _drop_stale_project_keys(self, projects):
        """注册表里已删除的项目，从 chain.order / chain.tasks / chain.parallel_blocks 清掉。"""
        alive = set(projects)
        chain = self.settings.get("chain") or {}
        raw_tasks = chain.get("tasks") if isinstance(chain.get("tasks"), list) else None
        order = listval.as_str_list(chain.get("order"), what="chain.order")
        blocks = listval.as_str_groups(chain.get("parallel_blocks"), what="chain.parallel_blocks")

        dropped_task_ids = set()
        if raw_tasks is not None:
            for item in raw_tasks:
                if isinstance(item, dict) and str(item.get("project") or "") not in alive:
                    dropped_task_ids.add(str(item.get("id") or item.get("project") or ""))
        stale_order = [k for k in order if k in dropped_task_ids or (raw_tasks is None and k not in alive)]
        stale_blocks = [
            k for b in blocks for k in b
            if k in dropped_task_ids or (raw_tasks is None and k not in alive)
        ]
        if not dropped_task_ids and not stale_order and not stale_blocks:
            return []

        def mutate(settings):
            chain_cfg = settings.setdefault("chain", {})
            if not isinstance(chain_cfg, dict):
                chain_cfg = {}
                settings["chain"] = chain_cfg
            if isinstance(chain_cfg.get("tasks"), list):
                chain_cfg["tasks"] = [
                    t for t in chain_cfg["tasks"]
                    if isinstance(t, dict) and str(t.get("project") or "") in alive
                ]
                kept_ids = {t["id"] for t in chain_cfg["tasks"] if t.get("id")}
                chain_cfg["order"] = [k for k in order if k in kept_ids]
                dead = dropped_task_ids
            else:
                chain_cfg["order"] = [k for k in order if k in alive]
                dead = set(stale_order) | set(stale_blocks)
            new_blocks = []
            for block in blocks:
                kept = [k for k in block if k not in dead]
                if len(kept) >= 2:
                    new_blocks.append(kept)
            chain_cfg["parallel_blocks"] = new_blocks

        self._mutate_settings(mutate)
        return sorted(dropped_task_ids | set(stale_order) | set(stale_blocks))

    def _reload_registry(self):
        """注册表变动后刷新运行顺序页、项目配置页、日志目录与副标题。"""
        registry = config.registry()
        projects = registry.get("projects", {}) or {}
        self._registry_cache = registry
        self.supervisor.registry = registry
        self._drop_stale_project_keys(projects)
        self.settings = config.settings()
        self.supervisor.settings = self.settings

        if self._order_page_inst is not None:
            self._order_page_inst.reload_tasks(self.settings, registry)
        if self._settings_page_inst is not None:
            self._settings_page_inst.reload_from_disk()
        if self._projects_page_inst is not None:
            self._projects_page_inst.reload()

        self._sync_log_catalog()
        self._update_run_stage(self.supervisor.status_snapshot())
        label = getattr(self, "_subtitle_label", None)
        if label is not None:
            text = self._subtitle_text(registry)
            label.setText(text)
            label.setToolTip(text)
        self._append_log("core", "info", "项目注册表已重新加载：{0} 个项目".format(len(projects)))

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
            target = self._parse_schedule_time(schedule_cfg.get("time", "04:00"))
            if time_edit.time() != target:
                time_edit.blockSignals(True)
                time_edit.setTime(target)
                time_edit.blockSignals(False)
            self._sync_schedule_tooltip()

    def _parse_schedule_time(self, raw):
        """把 `schedule.time` 解析成 QTime；写坏了退回 04:00，但留下线索。

        历史缺陷：写成 "abc" / "25:99" / 空值时只能退回 04:00，界面上就是一个
        正常的 04:00 —— 用户以为自己那条定时生效了，等到「八点没起来」时早忘了
        改过什么。所以坏值要同时留给日志（warning）与控件 tooltip，并且把原始
        文本记在 `self._bad_schedule_time` 里，恢复成合法值后再清掉。
        """
        try:
            hour, minute = [int(x) for x in str(raw).split(":")[:2]]
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("out of range: {0!r}".format(raw))
            parsed = QTime(hour, minute)
        except Exception as exc:
            logging.getLogger("afk.gui").warning("invalid schedule.time %r: %s", raw, exc)
            self._bad_schedule_time = str(raw)
            return QTime(4, 0)
        self._bad_schedule_time = None
        return parsed

    def _sync_schedule_tooltip(self):
        """按 `_bad_schedule_time` 给时间控件挂/摘提示（两处调用点共用）。"""
        time_edit = getattr(self, "schedule_time", None)
        if time_edit is None:
            return
        bad = getattr(self, "_bad_schedule_time", None)
        if bad is not None:
            time_edit.setToolTip(
                "settings.yaml 里 schedule.time 写的是 {0!r}，不是 HH:MM，"
                "已按 04:00 处理".format(bad)
            )
        else:
            time_edit.setToolTip("")

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
        # 落盘的是界面上的值，写坏了也被写成了合法的 HH:MM，坏值标记与提示
        # 必须跟着清掉，否则文件已经修好了、控件还在提示「写的是 abc」。
        self._bad_schedule_time = None
        self._sync_schedule_tooltip()
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
        self._registry_cache = self.supervisor.registry
        self.settings = self.supervisor.settings
        self.supervisor.run_chain()

    def start_chain_background(self):
        self.supervisor.settings = config.settings()
        self.supervisor.registry = config.registry()
        self._registry_cache = self.supervisor.registry
        self.settings = self.supervisor.settings
        self.supervisor.start_chain(background_only=True)

    def start_project(self, key):
        threading.Thread(target=self.supervisor.start_project, args=(key,), daemon=True, name="afk-start-" + key).start()

    def stop_project(self, key):
        threading.Thread(target=self.supervisor.stop_project, args=(key,), daemon=True, name="afk-stop-" + key).start()

    def show_project_logs(self, key):
        self._navigate("overview")
        title, color = self._log_col_info(key)
        self.log_board.open_column(
            key,
            title,
            color,
            backfill_lines=self._backfill_lines_for(key),
            focus=True,
        )

    def _open_project_logs(self, key):
        """项目页「打开日志」：打开该项目本次运行自己的日志文件。

        取文件的顺序是「本地运行对象 → 状态快照 → 磁盘上最新的同名文件 →
        日志根目录」。历史缺陷：`ProjectRun.to_dict()` 没有输出 `out_log`，
        而这里只读快照的 `run["out_log"]`，于是永远取不到，每次都退到
        `glob("{key}_*.out.log")[-1]`——多次运行后碰巧也是最新那份，但语义
        并不是「你点的这张卡这一次运行」；项目从没跑过、或日志被清理过时，
        还会退成打开整个 logs 目录。
        """
        out_log = None
        # 1. 本地运行对象：最准，连刚启动还没落盘的那次也能定位。
        run = (getattr(self.supervisor, "runs", None) or {}).get(key)
        candidate = getattr(run, "out_log", None) if run is not None else None
        if candidate is not None:
            try:
                if Path(candidate).exists():
                    out_log = Path(candidate)
            except OSError as exc:
                logging.getLogger("afk.gui").debug("check run out_log failed: %s", exc)
        # 2. 状态快照（跨进程/从 state.json 恢复出来的运行）。
        if out_log is None:
            try:
                entry = (self.supervisor.status_snapshot().get("runs") or {}).get(key) or {}
                snapshot_path = entry.get("out_log")
                if snapshot_path and Path(snapshot_path).exists():
                    out_log = Path(snapshot_path)
            except Exception as exc:
                logging.getLogger("afk.gui").debug("read snapshot out_log failed: %s", exc)
        # 3. 磁盘上最新的同名日志（老版本留下的运行没有 out_log 记录）。
        if out_log is None:
            project_dir = config.PROJECT_LOGS
            if project_dir.exists():
                matches = sorted(project_dir.glob("{0}_*.out.log".format(key)))
                if matches:
                    out_log = matches[-1]
        self._open_path(out_log if out_log is not None else config.LOGS_DIR)

    def _clear_log(self):
        self.log_board.clear_all()
        self.event_buffer.clear()

    def _project_color(self, project):
        colors = getattr(self, "project_colors", None) or {}
        key = str(project)
        if key in colors:
            return colors[key]
        base = self._base_project_for(key)
        if base in colors:
            return colors[base]
        custom = (self.settings.get("gui") or {}).get("project_colors") or {}
        return _project_color_for(base, self._active_theme(), custom)

    def _format_ts(self, ts=None):
        raw = time.time() if ts is None else float(ts)
        sec = int(raw)
        if sec != self._ts_cache_sec:
            self._ts_cache_sec = sec
            self._ts_cache_str = time.strftime("%H:%M:%S", time.localtime(sec))
        return self._ts_cache_str

    def _log_html(self, project, level, message, ts=None):
        project_color = self._project_color(project)
        level_colors = _theme_level_colors()
        if level == "error":
            message_color = level_colors["error"]
        elif level == "warn":
            message_color = level_colors["warn"]
        else:
            message_color = project_color
        ts_str = ts if isinstance(ts, str) and ts else self._format_ts(ts)
        return '<span style="color:{0}">[{1}] [{2}]</span> <span style="color:{3}">{4}</span>'.format(
            project_color,
            ts_str,
            _escape_html(str(project)),
            message_color,
            _escape_html(str(message)),
        )

    def _event_to_html(self, event):
        return self._log_html(
            event.get("project", "core"),
            event.get("level", "info"),
            event.get("message", ""),
            ts=event.get("ts"),
        )

    def _append_log(self, project, level, message, autoscroll=True, emit_event=True):
        html = self._log_html(project, level, message)
        if getattr(self, "log_board", None) is not None:
            self.log_board.route_event_html(project, html)
        if emit_event:
            try:
                self.bus.emit(
                    {
                        "project": str(project),
                        "kind": "gui",
                        "level": str(level),
                        "message": str(message),
                        "origin": "gui",
                    }
                )
            except Exception as exc:
                logging.getLogger("afk.gui").warning("emit gui log event failed: %s", exc)

    def _rerender_log(self):
        if getattr(self, "log_board", None) is not None:
            self.log_board.rerender_all(
                self.event_buffer,
                self._event_to_html,
                limit=LOG_RERENDER_LIMIT,
            )

    def _subtitle_text(self, registry=None):
        if registry is None:
            registry = getattr(self, "_registry_cache", None) or config.registry()
        names = []
        for key, spec in (registry.get("projects", {}) or {}).items():
            label = str(spec.get("display_name", key))
            names.append(label.split("（")[0].strip())
        if not names:
            return "一键挂机 · 统一编排"
        return "一键挂机 · {0} 统一编排".format(" + ".join(names))

    def _poll(self):
        board = getattr(self, "log_board", None)
        drained = 0
        skipped = 0
        rendered_count = 0
        by_project = {}
        deadline = time.monotonic() + LOG_DRAIN_BUDGET_MS / 1000.0
        while drained < LOG_DRAIN_LIMIT:
            try:
                event = self.queue.get_nowait()
            except Exception:
                break
            drained += 1
            self.event_buffer.append(event)
            if event.get("kind") == "post_action" and (event.get("data") or {}).get("action") == "exit":
                by_project.setdefault(CORE_KEY, []).append(
                    self._log_html("core", "info", "结束后操作：关闭 Auto-AFK")
                )
                QTimer.singleShot(800, self._quit)
                continue
            if event.get("origin") == "gui":
                continue
            if board is not None and not board.accepts(event):
                continue
            if rendered_count >= LOG_RENDER_BATCH_LIMIT:
                skipped += 1
                continue
            proj = str(event.get("project") or CORE_KEY)
            by_project.setdefault(proj, []).append(self._event_to_html(event))
            rendered_count += 1
            if drained % 128 == 0 and time.monotonic() > deadline:
                break
        if skipped:
            self._skipped_since_notice += skipped
            now = time.monotonic()
            if now - self._skip_notice_at >= 2.0:
                by_project.setdefault(CORE_KEY, []).append(
                    self._log_html(
                        "core",
                        "warn",
                        "日志刷得太快，最近 {0} 行没有画到界面上"
                        "（完整记录见 logs/events.jsonl）".format(self._skipped_since_notice),
                    )
                )
                self._skipped_since_notice = 0
                self._skip_notice_at = now
        if board is not None and by_project:
            board.append_batch(by_project)
        snapshot = self.supervisor.status_snapshot()
        runs = snapshot.get("runs") or {}
        if board is not None:
            board.sync_running_projects(
                runs,
                self._log_col_info,
                self._status_pair,
                backfill_fn=self._backfill_lines_for,
            )
        self._update_run_stage(snapshot)
        if self._order_page_inst is not None:
            self._order_page_inst.update_runs(runs)
        chain = snapshot["chain_status"]
        text = CHAIN_TEXT.get(chain, chain)
        chain_colors = _theme_chain_colors()
        color = chain_colors.get(chain, chain_colors["idle"])
        self.chain_pill.set_state(text, color)
        self._sync_chain_controls(snapshot)
        done_today = self._is_done_today()
        if self.widget is not None and self.widget.isVisible():
            idle_status = self._idle_status_cached()
            self.widget.tick(
                snapshot,
                idle_status,
                idle_status.get("threshold_sec") or 0,
                completed_today=done_today,
            )
        if chain != self._last_chain_for_completion or done_today != self._last_done_today:
            self._last_chain_for_completion = chain
            self._last_done_today = done_today
            if self._settings_page_inst is not None:
                try:
                    self._settings_page_inst._refresh_completion_label()
                except Exception as exc:
                    logging.getLogger("afk.gui").warning(
                        "refresh completion label failed: %s", exc
                    )

    def _sync_chain_controls(self, snapshot=None):
        """链在跑时把「一键挂机」置灰；状态未变时直接短路跳过属性写入。"""
        if snapshot is None:
            try:
                snapshot = self.supervisor.status_snapshot()
            except Exception as exc:
                logging.getLogger("afk.gui").debug("status snapshot failed: %s", exc)
                snapshot = {}
        chain = str((snapshot or {}).get("chain_status") or "idle")
        busy = chain == "running"
        if busy == self._last_chain_busy:
            return
        self._last_chain_busy = busy
        if busy:
            if self.chain_btn.isEnabled():
                self.chain_btn.setEnabled(False)
            if self.chain_btn.text() != "挂机中…":
                self.chain_btn.setText("挂机中…")
            self.chain_btn.setToolTip("挂机链正在运行；要停止请用「全部停止」")
        else:
            if not self.chain_btn.isEnabled():
                self.chain_btn.setEnabled(True)
            if self.chain_btn.text() != "一键挂机":
                self.chain_btn.setText("一键挂机")
            self.chain_btn.setToolTip("按当前运行顺序把所有项目跑一遍")

    def _on_change(self):
        pass

    # -- 错误报告（面板搬进 gui/advanced_panel.py，按需实例化后转发） ---------
    def refresh_reports(self):
        self.advanced.refresh_reports()

    def open_selected_report(self):
        if self._advanced_inst is not None:
            self._advanced_inst.open_selected_report()

    def run_doctor(self):
        """总览页头部「环境自检」按钮：切到设置 → 高级设置并展开「环境自检」区块。"""
        self._navigate("settings")
        self.settings_page.select_section("advanced")
        self.advanced.expand("doctor")
        self._run_doctor_task()

    def _run_doctor_task(self):
        """真正的自检任务（头部按钮与高级设置区块里的按钮共用）。"""
        if not self._begin_task("doctor"):
            return
        advanced = self.advanced
        if advanced is not None:
            advanced.expand("doctor")
            advanced.doctor_view.setPlainText("环境自检运行中...")

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
        """链或任一项目正在跑时，空闲检测不弹提示框。

        失败时按「不忙」放行是有意的：宁可多弹一次提示，也不能因为一次异常
        就永远不提醒。但必须留痕——否则「挂机跑着突然弹出空闲提示」这种最
        让人费解的现象，日志里一个字都没有。
        """
        try:
            return bool(self.supervisor.chain_running())
        except Exception as exc:
            logging.getLogger("afk.gui").warning("check chain busy failed: %s", exc)
            return False

    def _idle_status_cached(self):
        """空闲状态快照（限速重算）。

        `idle_watcher.status()` 每次都要读一遍 settings.yaml（get_settings 就是
        config.settings()，实测 4.3ms）。桌面小组件开着时 _poll 每 300ms 拿一次，
        光这一项就吃掉 1.4% 的主线程。

        这些字段的变化速度完全不同：空闲秒数按秒变，阈值/开关要用户去设置页改。
        所以空闲秒数每轮刷新，配置相关字段按 2 秒重算一次——用户在设置页改完
        最多两秒就能看到，而读盘次数降到原来的 1/6。
        """
        now = time.monotonic()
        snapshot = self._idle_status_cache
        if snapshot and now - self._idle_status_at < 2.0:
            try:
                idle_now = idle.idle_seconds()
            except Exception:
                idle_now = float(snapshot.get("idle_sec") or 0)
            fresh = dict(snapshot)
            fresh["idle_sec"] = round(idle_now, 1)
            fresh["idle_text"] = idle.format_idle(idle_now)
            fresh["cooldown_left_sec"] = max(0, int(self._idle_cooldown_deadline - time.time()))
            fresh["watching"] = self.idle_watcher.running()
            return fresh
        try:
            snapshot = self.idle_watcher.status()
        except Exception as exc:
            logging.getLogger("afk.gui").debug("idle status failed: %s", exc)
            snapshot = {}
        self._idle_status_cache = dict(snapshot)
        self._idle_status_at = now
        self._idle_cooldown_deadline = time.time() + float(snapshot.get("cooldown_left_sec") or 0)
        return snapshot

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
        except Exception as exc:
            # 判不出来时按「今天还没完成」放行（宁可多跑一轮，也不要让用户以为
            # 自动挂机坏了），但要留痕：这条静默的话，现象是「开关明明开着，
            # 却还是重复挂了一遍」，没有任何线索可查。
            logging.getLogger("afk.gui").warning("check done today failed: %s", exc)
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
        if self._settings_page_inst is not None:
            self._settings_page_inst.reload_from_disk()
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
        if self._settings_page_inst is not None:
            self._settings_page_inst.reload_from_disk()
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
        advanced = self._advanced_inst
        view = advanced.doctor_view if advanced is not None else getattr(self, "doctor_view", None)
        if view is not None:
            view.setPlainText("\n".join(lines))

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
        # 设置页的数值框走 400ms 防抖，关窗口时同样要补一次落盘（仅已加载时处理）
        settings_page = self._settings_page_inst
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
        if self._did_intro:
            return
        self._did_intro = True
        fade_in_window(self, duration=280)
        if getattr(self, "run_stage", None) is not None:
            fade_in_widget(self.run_stage, duration=260, delay=100)

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

    def _sync_responsive_layout(self):
        """按窗口当前宽度切换桌面端 / 紧凑平板端 / 极窄移动端布局。

        1. < 820px 时左侧导航自动收缩为 56px 纯图标栏，释放 140px 横向空间；
        2. < 640px 时顶栏主操作区与工具栏改为纵向堆叠，收紧内容边距并隐藏竖向分隔线，
           保证在窄屏/分屏/移动端尺寸下所有按钮与开关完整可见、不横向溢出。
        """
        width = self.width()
        if width <= 0:
            return
        nav = getattr(self, "nav", None)
        if nav is not None and hasattr(nav, "set_compact"):
            nav.set_compact(width < self.COMPACT_NAV_WIDTH)

        narrow = width < self.MOBILE_BREAKPOINT
        if narrow != self._narrow_mode:
            self._narrow_mode = narrow
            overview_layout = getattr(self, "_overview_layout", None)
            if overview_layout is not None:
                if narrow:
                    overview_layout.setContentsMargins(10, 8, 10, 8)
                    overview_layout.setSpacing(8)
                else:
                    overview_layout.setContentsMargins(16, 12, 16, 10)
                    overview_layout.setSpacing(12)
            direction = QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight
            for box in (
                getattr(self, "_header_top_layout", None),
                getattr(self, "_header_controls_layout", None),
            ):
                if box is not None:
                    box.setDirection(direction)
            for div in getattr(self, "_toolbar_dividers", ()):
                div.setVisible(not narrow)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        central = self.centralWidget()
        background = getattr(self, "background", None)
        if central is not None and background is not None:
            background.setGeometry(central.rect())
        self._sync_responsive_layout()
        self._on_window_resized()

    def _busy_project_keys(self):
        """还没跑到终态的项目键，按名字排序返回。

        关窗口会走 `_quit()` → `supervisor.stop_all()`，正在挂的项目会被直接
        杀掉（实测：伪造一个 running 的 run，`window.close()` 之后
        `run.alive` 从 True 变 False）。点右上角 × 时用户往往只是想把界面收起来，
        所以要先把这份清单拿出来问一句。
        """
        runs = getattr(getattr(self, "supervisor", None), "runs", None) or {}
        busy = []
        for key, run in runs.items():
            if run is None or getattr(run, "terminal", False):
                continue
            if getattr(run, "alive", False) or getattr(run, "status", "") in ("pending", "running", "hang"):
                busy.append(str(key))
        return sorted(busy)

    def _busy_project_label(self, key):
        return self._task_display_info(key)[0]

    def _confirm_quit_with_busy(self, keys):
        """有项目在跑时问一句；纯逻辑抽出来，冒烟可以直接替换掉这个函数。"""
        chain_busy = False
        if not keys:
            # 点了「一键挂机」但项目还没真正起来（正在更新/起模拟器阶段），
            # runs 里是空的，关窗口会把整条链静默中止，同样要问一句。
            try:
                chain_busy = bool(self.supervisor.chain_running())
            except Exception as exc:
                logging.getLogger("afk.gui").debug("chain_running probe failed: %s", exc)
            if not chain_busy:
                return True
        if keys:
            names = "、".join(self._busy_project_label(k) for k in keys)
            detail = "还有 {0} 个项目在运行：\n\n{1}".format(len(keys), names)
        else:
            detail = "挂机链正在运行（项目还没有真正启动）"
        choice = QMessageBox.question(
            self,
            "退出 Auto-AFK",
            "{0}\n\n退出会把它们一起停掉，确定退出吗？".format(detail),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return choice == QMessageBox.Yes

    def closeEvent(self, event):
        if (self.settings.get("gui", {}) or {}).get("minimize_to_tray", False):
            event.ignore()
            self.hide()
            self.tray.showMessage("Auto-AFK", "已最小化到托盘", QSystemTrayIcon.Information, 2000)
            return
        # 历史缺陷：关窗口无条件 _quit()，正在挂的项目被一声不响地杀掉，
        # 尤其是 minimize_to_tray 关掉之后，用户以为只是关个界面。
        busy = self._busy_project_keys()
        if not self._confirm_quit_with_busy(busy):
            event.ignore()
            if busy:
                self._append_log(
                    "core", "info",
                    "退出已取消：{0} 个项目仍在运行".format(len(busy)),
                )
            else:
                self._append_log("core", "info", "退出已取消：挂机链仍在运行")
            return
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
