"""设置页：顶部子导航分组（常规与外观 / 自动化与任务链 / 更新与模拟器 / 修复与通知 / 日志与维护 / 高级设置）。

所有修改即时（或 400ms 防抖后）写回 settings.yaml 并通过 changed 信号通知主窗口刷新运行态。
"""

import json
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from afk_core import autostart, config, listval, numval
from gui import icons, themes
from gui.modern import (
    AnimatedButton,
    HoverGlowCard,
    SubNavBar,
    current_theme,
    enable_smooth_scroll,
)

POST_ACTION_LABELS = [
    ("none", "无操作"),
    ("shutdown", "关机"),
    ("sleep", "睡眠"),
    ("hibernate", "休眠"),
    ("exit", "关闭程序"),
]

AUTOSTART_MODES = [
    (autostart.MODE_TRAY, autostart.MODE_LABELS[autostart.MODE_TRAY]),
    (autostart.MODE_ADMIN, autostart.MODE_LABELS[autostart.MODE_ADMIN]),
]

WEEK_DAYS = [
    ("mon", "周一"),
    ("tue", "周二"),
    ("wed", "周三"),
    ("thu", "周四"),
    ("fri", "周五"),
    ("sat", "周六"),
    ("sun", "周日"),
]

EMU_UPDATER_MODES = [
    ("none", "不更新 (none)"),
    ("gui", "打开更新器界面 (gui)"),
    ("cli", "后台检查 (cli)"),
]

NOTIFY_PROVIDERS = [
    "serverchan",
    "pushplus",
    "bark",
    "telegram",
    "discord",
    "dingtalk",
    "wecom",
    "feishu",
    "qmsg",
    "smtp",
]


def _as_int(value, default, low=None, high=None, what=""):
    return numval.as_int(value, default, low=low, high=high, what=what)


def _parse_hhmm(raw):
    try:
        hour, minute = [int(x) for x in str(raw).split(":")[:2]]
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return QTime(hour, minute)
    except Exception:
        pass
    return QTime(4, 0)


class _SectionCard(HoverGlowCard):
    def __init__(self, title, hint="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 15, 18, 15)
        outer.setSpacing(8)
        cap = QLabel(title)
        cap.setObjectName("sectionTitleCard")
        outer.addWidget(cap)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("hintLabel")
            hint_label.setWordWrap(True)
            outer.addWidget(hint_label)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)


class SettingsPage(QWidget):
    """分标签页展示、编辑并防抖落盘所有 Auto-AFK 配置。"""

    changed = Signal()
    theme_changed = Signal(str)
    open_path_requested = Signal(object)
    reset_window_size_requested = Signal()
    _notify_test_done = Signal(bool, str)
    _cleanup_done = Signal(str)

    def __init__(self, log_fn=None, registry=None, advanced=None, bus=None, parent=None):
        super().__init__(parent)
        self._log = log_fn or (lambda *a, **k: None)
        self.advanced = advanced
        self._bus = bus
        self._registry_injected = registry is not None
        self._registry = registry if registry is not None else config.registry()
        self._settings_snapshot = config.settings()
        self._loading = True

        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.setInterval(400)
        self._pending_timer.timeout.connect(self._flush_pending_save)
        self._pending_saves = []
        self._tab_layouts = {}
        self._responsive_rows = []
        self._stacked_mode = False

        self._notify_test_done.connect(self._on_notify_test_done)
        self._cleanup_done.connect(self._on_cleanup_done)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(10)

        self.sub_nav = SubNavBar()
        self.sub_nav.current_changed.connect(self.select_section)
        outer.addWidget(self.sub_nav)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)

        self._build_tabs()
        self._populate_from_settings(self._settings_snapshot)
        self.sub_nav.set_current("general", emit=False)
        self.pages.setCurrentIndex(0)
        self._loading = False

    def _make_tab(self, key, label, icon_name):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        enable_smooth_scroll(scroll)
        inner = QWidget()
        inner.setObjectName("central")
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(2, 2, 8, 8)
        vbox.setSpacing(10)
        scroll.setWidget(inner)
        idx = self.pages.addWidget(scroll)
        self._tab_layouts[key] = (idx, vbox)
        self.sub_nav.add_tab(
            key,
            label,
            icon=icons.make(icon_name, themes.icon_color(current_theme(), subtle=True), 14),
        )
        return vbox

    def _build_tabs(self):
        # =====================================================================
        # 1. 常规与外观
        # =====================================================================
        gen = self._make_tab("general", "常规与外观", "sliders")

        self.appearance_card = _SectionCard(
            "外观",
            "主题决定整套界面配色；背景图只作用在主窗口底板上，卡片与日志区保持可读性。",
        )
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_cap = QLabel("主题")
        theme_cap.setObjectName("hintLabel")
        self.theme_combo = QComboBox()
        for name in ("dark", "light"):
            self.theme_combo.addItem(themes.THEME_LABELS.get(name, name), name)
        self.theme_combo.currentIndexChanged.connect(self._save_appearance)
        theme_row.addWidget(theme_cap)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch(1)
        self.appearance_card.body.addLayout(theme_row)

        bg_row = QHBoxLayout()
        bg_row.setSpacing(8)
        bg_cap = QLabel("背景图")
        bg_cap.setObjectName("hintLabel")
        self.bg_edit = QLineEdit()
        self.bg_edit.setPlaceholderText("留空表示不用背景图（可填相对仓库根的路径）")
        self.bg_edit.editingFinished.connect(self._save_appearance)
        bg_pick = AnimatedButton("选择…", variant="mini")
        bg_pick.clicked.connect(self._pick_background)
        bg_clear = AnimatedButton("清除", variant="mini")
        bg_clear.clicked.connect(self._clear_background)
        bg_row.addWidget(bg_cap)
        bg_row.addWidget(self.bg_edit, 1)
        bg_row.addWidget(bg_pick)
        bg_row.addWidget(bg_clear)
        self.appearance_card.body.addLayout(bg_row)

        op_row = QHBoxLayout()
        op_row.setSpacing(8)
        op_cap = QLabel("背景不透明度")
        op_cap.setObjectName("hintLabel")
        self.bg_opacity = QSpinBox()
        self.bg_opacity.setRange(0, 100)
        self.bg_opacity.setSuffix(" %")
        self.bg_opacity.valueChanged.connect(
            lambda _v: self._queue_save("appearance", self._save_appearance)
        )
        op_row.addWidget(op_cap)
        op_row.addWidget(self.bg_opacity)
        op_row.addStretch(1)
        self.appearance_card.body.addLayout(op_row)
        gen.addWidget(self.appearance_card)

        self.window_card = _SectionCard(
            "窗口、托盘与小组件",
            "控制窗口关闭行为、启动联动、实时日志列自动展开与桌面小组件。",
        )
        self.minimize_to_tray_check = QCheckBox("关闭窗口时最小化到系统托盘（不退出挂机）")
        self.minimize_to_tray_check.toggled.connect(self._save_window_gui)
        self.autostart_chain_check = QCheckBox("Auto-AFK 启动后自动开始一键挂机")
        self.autostart_chain_check.toggled.connect(self._save_window_gui)
        self.widget_check = QCheckBox("显示桌面悬浮小组件")
        self.widget_check.toggled.connect(self._save_window_gui)
        self.auto_open_logs_check = QCheckBox("项目运行时自动打开对应实时日志列")
        self.auto_open_logs_check.toggled.connect(self._save_window_gui)
        for chk in (
            self.minimize_to_tray_check,
            self.autostart_chain_check,
            self.widget_check,
            self.auto_open_logs_check,
        ):
            self.window_card.body.addWidget(chk)

        win_btn_row = QHBoxLayout()
        win_btn_row.setSpacing(8)
        self.reset_win_btn = AnimatedButton("重置窗口默认尺寸", variant="mini")
        self.reset_win_btn.clicked.connect(self.reset_window_size_requested.emit)
        win_btn_row.addWidget(self.reset_win_btn)
        win_btn_row.addStretch(1)
        self.window_card.body.addLayout(win_btn_row)
        gen.addWidget(self.window_card)

        self.autostart_card = _SectionCard(
            "开机启动",
            "写当前用户注册表 Run 键（HKCU），不需要管理员权限；关闭开关即删除该项。",
        )
        auto_row = QHBoxLayout()
        auto_row.setSpacing(8)
        self.autostart_check = QCheckBox("开机启动")
        self.autostart_check.toggled.connect(self._save_autostart)
        self.autostart_mode = QComboBox()
        for value, label in AUTOSTART_MODES:
            self.autostart_mode.addItem(label, value)
        self.autostart_mode.currentIndexChanged.connect(self._save_autostart)
        auto_row.addWidget(self.autostart_check)
        auto_row.addWidget(self.autostart_mode)
        auto_row.addStretch(1)
        self.autostart_card.body.addLayout(auto_row)
        self.autostart_hint = QLabel("")
        self.autostart_hint.setObjectName("hintLabel")
        self.autostart_hint.setWordWrap(True)
        self.autostart_card.body.addWidget(self.autostart_hint)
        gen.addWidget(self.autostart_card)
        gen.addStretch(1)

        # =====================================================================
        # 2. 自动化与任务链
        # =====================================================================
        auto_tab = self._make_tab("automation", "自动化与任务链", "play")

        self.schedule_card = _SectionCard(
            "定时启动",
            "到达设定时间且星期匹配时自动触发「一键挂机」，同一天只触发一次。",
        )
        sched_row = QHBoxLayout()
        sched_row.setSpacing(10)
        self.sched_enabled_check = QCheckBox("启用定时启动")
        self.sched_enabled_check.toggled.connect(self._save_schedule)
        self.sched_time_edit = QTimeEdit()
        self.sched_time_edit.setDisplayFormat("HH:mm")
        self.sched_time_edit.timeChanged.connect(
            lambda _t: self._queue_save("schedule", self._save_schedule)
        )
        sched_row.addWidget(self.sched_enabled_check)
        sched_row.addWidget(QLabel("触发时间:"))
        sched_row.addWidget(self.sched_time_edit)
        sched_row.addStretch(1)
        self.schedule_card.body.addLayout(sched_row)

        days_row = QHBoxLayout()
        days_row.setSpacing(8)
        self.sched_day_checks = {}
        for day_key, day_label in WEEK_DAYS:
            chk = QCheckBox(day_label)
            chk.toggled.connect(self._save_schedule)
            self.sched_day_checks[day_key] = chk
            days_row.addWidget(chk)
        days_row.addStretch(1)
        self.schedule_card.body.addLayout(days_row)
        auto_tab.addWidget(self.schedule_card)

        self.idle_card = _SectionCard(
            "空闲自动挂机",
            "电脑连续无鼠标/键盘输入达到设定分钟数后，弹出提示框；倒计时结束仍未选择则自动运行全部任务。",
        )
        idle_row = QHBoxLayout()
        idle_row.setSpacing(8)
        self.idle_check = QCheckBox("启用")
        self.idle_check.toggled.connect(self._save_idle)
        self.idle_minutes = QSpinBox()
        self.idle_minutes.setRange(1, 1440)
        self.idle_minutes.setSuffix(" 分钟无操作后提示")
        self.idle_minutes.valueChanged.connect(lambda _v: self._queue_save("idle", self._save_idle))
        self.idle_prompt = QSpinBox()
        self.idle_prompt.setRange(5, 600)
        self.idle_prompt.setSuffix(" 秒后自动执行")
        self.idle_prompt.valueChanged.connect(lambda _v: self._queue_save("idle", self._save_idle))
        self.idle_cooldown = QSpinBox()
        self.idle_cooldown.setRange(0, 1440)
        self.idle_cooldown.setSuffix(" 分钟冷却")
        self.idle_cooldown.valueChanged.connect(lambda _v: self._queue_save("idle", self._save_idle))
        idle_row.addWidget(self.idle_check)
        idle_row.addWidget(self.idle_minutes)
        idle_row.addWidget(self.idle_prompt)
        idle_row.addWidget(self.idle_cooldown)
        idle_row.addStretch(1)
        self.idle_card.body.addLayout(idle_row)

        self.idle_skip_check = QCheckBox("今天已正常完成则不再自动执行")
        self.idle_skip_check.toggled.connect(self._save_idle)
        self.idle_card.body.addWidget(self.idle_skip_check)

        self.idle_hint = QLabel("")
        self.idle_hint.setObjectName("hintLabel")
        self.idle_hint.setWordWrap(True)
        self.idle_card.body.addWidget(self.idle_hint)

        done_row = QHBoxLayout()
        done_row.setSpacing(8)
        self.completion_label = QLabel("")
        self.completion_label.setObjectName("hintLabel")
        self.clear_completion_btn = AnimatedButton("今天还没完成（恢复自动挂机）", variant="mini")
        self.clear_completion_btn.clicked.connect(self._clear_completion)
        done_row.addWidget(self.completion_label, 1)
        done_row.addWidget(self.clear_completion_btn)
        self.idle_card.body.addLayout(done_row)
        auto_tab.addWidget(self.idle_card)

        self.chain_card = _SectionCard(
            "任务链容错与全局超时",
            "控制单个任务失败后是否继续执行后续任务、是否在本轮结尾重试以及全局无输出卡死超时。",
        )
        self.chain_continue_check = QCheckBox("单个任务失败后继续运行后续任务 (continue_on_failure)")
        self.chain_continue_check.toggled.connect(self._save_chain_policy)
        self.chain_retry_end_check = QCheckBox("所有任务走完后重试本轮失败的任务 (retry_failed_at_end)")
        self.chain_retry_end_check.toggled.connect(self._save_chain_policy)
        self.chain_card.body.addWidget(self.chain_continue_check)
        self.chain_card.body.addWidget(self.chain_retry_end_check)

        chain_row = QHBoxLayout()
        chain_row.setSpacing(10)
        self.chain_max_attempts = QSpinBox()
        self.chain_max_attempts.setRange(1, 10)
        self.chain_max_attempts.setPrefix("单任务最多尝试 ")
        self.chain_max_attempts.setSuffix(" 次")
        self.chain_max_attempts.valueChanged.connect(
            lambda _v: self._queue_save("chain_policy", self._save_chain_policy)
        )
        self.hang_timeout_spin = QSpinBox()
        self.hang_timeout_spin.setRange(60, 14400)
        self.hang_timeout_spin.setPrefix("全局默认卡死超时 ")
        self.hang_timeout_spin.setSuffix(" 秒")
        self.hang_timeout_spin.valueChanged.connect(
            lambda _v: self._queue_save("chain_policy", self._save_chain_policy)
        )
        chain_row.addWidget(self.chain_max_attempts)
        chain_row.addWidget(self.hang_timeout_spin)
        chain_row.addStretch(1)
        self.chain_card.body.addLayout(chain_row)
        auto_tab.addWidget(self.chain_card)

        self.post_card = _SectionCard(
            "所有运行结束后的操作",
            "「一键挂机」全部成功结束并经过延迟后执行；手动停止或存在失败任务时不会触发。",
        )
        post_row = QHBoxLayout()
        post_row.setSpacing(8)
        self.post_combo = QComboBox()
        for value, label in POST_ACTION_LABELS:
            self.post_combo.addItem(label, value)
        self.post_combo.currentIndexChanged.connect(self._save_post_action)
        self.post_delay = QSpinBox()
        self.post_delay.setRange(0, 86400)
        self.post_delay.setSuffix(" 秒后执行")
        self.post_delay.valueChanged.connect(
            lambda _v: self._queue_save("post_delay", self._save_post_action)
        )
        post_row.addWidget(QLabel("操作:"))
        post_row.addWidget(self.post_combo)
        post_row.addSpacing(8)
        post_row.addWidget(self.post_delay)
        post_row.addStretch(1)
        self.post_card.body.addLayout(post_row)
        auto_tab.addWidget(self.post_card)
        auto_tab.addStretch(1)

        # =====================================================================
        # 3. 更新与模拟器
        # =====================================================================
        upd_tab = self._make_tab("update_emulator", "更新与模拟器", "refresh")

        self.pre_run_card = _SectionCard(
            "运行前与启动更新",
            "控制启动 Auto-AFK 时或每次「一键挂机」前是否自动拉取项目代码及检查 MuMu 更新。",
        )
        self.pre_run_enabled_check = QCheckBox("一键挂机开始前执行运行前更新 (pre_run.enabled)")
        self.pre_run_enabled_check.toggled.connect(self._save_pre_run)
        self.pre_run_startup_check = QCheckBox("程序启动后在后台更新全部已启用项目 (at_startup)")
        self.pre_run_startup_check.toggled.connect(self._save_pre_run)
        self.pre_run_projects_check = QCheckBox("更新挂机项目仓库与依赖 (update_projects)")
        self.pre_run_projects_check.toggled.connect(self._save_pre_run)
        self.pre_run_emulator_check = QCheckBox("更新 MuMu 模拟器 (update_emulator)")
        self.pre_run_emulator_check.toggled.connect(self._save_pre_run)
        for chk in (
            self.pre_run_enabled_check,
            self.pre_run_startup_check,
            self.pre_run_projects_check,
            self.pre_run_emulator_check,
        ):
            self.pre_run_card.body.addWidget(chk)

        conc_row = QHBoxLayout()
        conc_row.setSpacing(8)
        conc_cap = QLabel("项目并行更新并发数")
        conc_cap.setObjectName("hintLabel")
        self.pre_run_concurrency = QSpinBox()
        self.pre_run_concurrency.setRange(0, 16)
        self.pre_run_concurrency.setSpecialValueText("不限（全部并发）")
        self.pre_run_concurrency.setSuffix(" 个")
        self.pre_run_concurrency.valueChanged.connect(
            lambda _v: self._queue_save("pre_run", self._save_pre_run)
        )
        conc_row.addWidget(conc_cap)
        conc_row.addWidget(self.pre_run_concurrency)
        conc_row.addStretch(1)
        self.pre_run_card.body.addLayout(conc_row)
        upd_tab.addWidget(self.pre_run_card)

        self.emulator_card = _SectionCard(
            "MuMu 模拟器托管",
            "配置依赖模拟器项目（如 BAAH）的实例索引、ADB 端口、窗口行为与可执行文件路径。",
        )
        emu_checks_row = QHBoxLayout()
        emu_checks_row.setSpacing(12)
        self.emu_enabled_check = QCheckBox("启用模拟器")
        self.emu_manage_check = QCheckBox("由 Auto-AFK 托管启停")
        self.emu_close_after_check = QCheckBox("任务结束后关闭")
        self.emu_minimize_check = QCheckBox("启动后最小化窗口")
        for chk in (
            self.emu_enabled_check,
            self.emu_manage_check,
            self.emu_close_after_check,
            self.emu_minimize_check,
        ):
            chk.toggled.connect(self._save_emulator)
            emu_checks_row.addWidget(chk)
        emu_checks_row.addStretch(1)
        self.emulator_card.body.addLayout(emu_checks_row)

        emu_num_row = QHBoxLayout()
        emu_num_row.setSpacing(8)
        self.emu_vmindex_spin = QSpinBox()
        self.emu_vmindex_spin.setRange(0, 32)
        self.emu_vmindex_spin.setPrefix("实例 vmindex: ")
        self.emu_adb_port_spin = QSpinBox()
        self.emu_adb_port_spin.setRange(1024, 65535)
        self.emu_adb_port_spin.setPrefix("ADB 端口: ")
        self.emu_ready_timeout_spin = QSpinBox()
        self.emu_ready_timeout_spin.setRange(30, 900)
        self.emu_ready_timeout_spin.setPrefix("就绪超时 ")
        self.emu_ready_timeout_spin.setSuffix(" 秒")
        for spin in (self.emu_vmindex_spin, self.emu_adb_port_spin, self.emu_ready_timeout_spin):
            spin.valueChanged.connect(lambda _v: self._queue_save("emulator", self._save_emulator))
            emu_num_row.addWidget(spin)
        self.emu_updater_combo = QComboBox()
        for val, lbl in EMU_UPDATER_MODES:
            self.emu_updater_combo.addItem(lbl, val)
        self.emu_updater_combo.currentIndexChanged.connect(self._save_emulator)
        emu_num_row.addWidget(self.emu_updater_combo)
        emu_num_row.addStretch(1)
        self.emulator_card.body.addLayout(emu_num_row)

        self.emu_main_edit, emu_main_row = self._make_exe_row("主程序 main_exe", "如 E:\\MuMuPlayer\\nx_main\\MuMuNxMain.exe")
        self.emu_device_edit, emu_dev_row = self._make_exe_row("设备进程 device_exe", "如 MuMuNxDevice.exe")
        self.emu_manager_edit, emu_mgr_row = self._make_exe_row("命令行 MuMuManager", "如 MuMuManager.exe")
        for row in (emu_main_row, emu_dev_row, emu_mgr_row):
            self.emulator_card.body.addLayout(row)
        upd_tab.addWidget(self.emulator_card)
        upd_tab.addStretch(1)

        # =====================================================================
        # 4. 修复与通知
        # =====================================================================
        rn_tab = self._make_tab("repair_notify", "修复与通知", "shield")

        self.repair_card = _SectionCard(
            "自动修复引擎",
            "挂机项目识别到已知错误特征时，按 repair_rules.yaml 自动执行修复与重跑。",
        )
        self.repair_enabled_check = QCheckBox("启用自动修复")
        self.repair_enabled_check.toggled.connect(self._save_repair)
        self.repair_unsafe_check = QCheckBox("允许自动执行非安全修复步骤 (auto_unsafe，如清理缓存/重建环境)")
        self.repair_unsafe_check.toggled.connect(self._save_repair)
        self.repair_card.body.addWidget(self.repair_enabled_check)
        self.repair_card.body.addWidget(self.repair_unsafe_check)

        repair_row = QHBoxLayout()
        repair_row.setSpacing(8)
        self.repair_rounds_spin = QSpinBox()
        self.repair_rounds_spin.setRange(0, 10)
        self.repair_rounds_spin.setPrefix("最多修复 ")
        self.repair_rounds_spin.setSuffix(" 轮")
        self.repair_rounds_spin.valueChanged.connect(lambda _v: self._queue_save("repair", self._save_repair))
        self.repair_delay_spin = QSpinBox()
        self.repair_delay_spin.setRange(0, 300)
        self.repair_delay_spin.setPrefix("重试等待 ")
        self.repair_delay_spin.setSuffix(" 秒")
        self.repair_delay_spin.valueChanged.connect(lambda _v: self._queue_save("repair", self._save_repair))
        repair_row.addWidget(self.repair_rounds_spin)
        repair_row.addWidget(self.repair_delay_spin)
        repair_row.addStretch(1)
        self.repair_card.body.addLayout(repair_row)
        rn_tab.addWidget(self.repair_card)

        self.notify_card = _SectionCard(
            "通知推送 (onepush / 系统 Toast)",
            "挂机完成或发生严重失败时推送提醒。onepush 参数填标准 JSON 对象（如 {\"key\": \"...\"}）。",
        )
        notify_top = QHBoxLayout()
        notify_top.setSpacing(10)
        self.notify_enabled_check = QCheckBox("启用通知")
        self.notify_enabled_check.toggled.connect(self._save_notifications)
        self.notify_toast_check = QCheckBox("Windows 系统弹窗通知")
        self.notify_toast_check.toggled.connect(self._save_notifications)
        self.notify_dedupe_spin = QSpinBox()
        self.notify_dedupe_spin.setRange(0, 3600)
        self.notify_dedupe_spin.setPrefix("同事件去重 ")
        self.notify_dedupe_spin.setSuffix(" 秒")
        self.notify_dedupe_spin.valueChanged.connect(
            lambda _v: self._queue_save("notifications", self._save_notifications)
        )
        notify_top.addWidget(self.notify_enabled_check)
        notify_top.addWidget(self.notify_toast_check)
        notify_top.addWidget(self.notify_dedupe_spin)
        notify_top.addStretch(1)
        self.notify_card.body.addLayout(notify_top)

        prov_row = QHBoxLayout()
        prov_row.setSpacing(8)
        prov_cap = QLabel("推送通道 (provider)")
        prov_cap.setObjectName("hintLabel")
        self.notify_provider_combo = QComboBox()
        self.notify_provider_combo.setEditable(True)
        for prov in NOTIFY_PROVIDERS:
            self.notify_provider_combo.addItem(prov, prov)
        self.notify_provider_combo.currentTextChanged.connect(
            lambda _t: self._queue_save("notifications", self._save_notifications)
        )
        prov_row.addWidget(prov_cap)
        prov_row.addWidget(self.notify_provider_combo)
        prov_row.addStretch(1)
        self.notify_card.body.addLayout(prov_row)

        params_cap = QLabel("推送参数 JSON (notifications.params)")
        params_cap.setObjectName("hintLabel")
        self.notify_card.body.addWidget(params_cap)
        self.notify_params_edit = QPlainTextEdit()
        self.notify_params_edit.setFixedHeight(96)
        mono = QFont("Cascadia Mono", 9)
        mono.setStyleHint(QFont.Monospace)
        self.notify_params_edit.setFont(mono)
        self.notify_params_edit.setPlaceholderText("{}")
        self.notify_card.body.addWidget(self.notify_params_edit)

        notify_actions = QHBoxLayout()
        notify_actions.setSpacing(8)
        self.notify_save_params_btn = AnimatedButton("保存推送参数", variant="primary")
        self.notify_save_params_btn.setObjectName("primary")
        self.notify_save_params_btn.clicked.connect(self._save_notify_params)
        self.notify_test_btn = AnimatedButton("发送测试通知")
        self.notify_test_btn.clicked.connect(self._on_notify_test_clicked)
        self.notify_status_label = QLabel("")
        self.notify_status_label.setObjectName("hintLabel")
        notify_actions.addWidget(self.notify_save_params_btn)
        notify_actions.addWidget(self.notify_test_btn)
        notify_actions.addWidget(self.notify_status_label, 1)
        self.notify_card.body.addLayout(notify_actions)
        rn_tab.addWidget(self.notify_card)
        rn_tab.addStretch(1)

        # =====================================================================
        # 5. 日志与维护
        # =====================================================================
        maint_tab = self._make_tab("maintenance", "日志与维护", "file")

        self.housekeeping_card = _SectionCard(
            "日志与错误报告自动清理",
            "启动 Auto-AFK 时自动清理超过保留天数的项目运行日志与错误报告，并控制 events.jsonl 最大体积。",
        )
        self.hk_enabled_check = QCheckBox("启用启动自动清理")
        self.hk_enabled_check.toggled.connect(self._save_housekeeping)
        self.housekeeping_card.body.addWidget(self.hk_enabled_check)

        hk_row = QHBoxLayout()
        hk_row.setSpacing(8)
        self.hk_log_days_spin = QSpinBox()
        self.hk_log_days_spin.setRange(1, 365)
        self.hk_log_days_spin.setPrefix("运行日志保留 ")
        self.hk_log_days_spin.setSuffix(" 天")
        self.hk_report_days_spin = QSpinBox()
        self.hk_report_days_spin.setRange(1, 365)
        self.hk_report_days_spin.setPrefix("错误报告保留 ")
        self.hk_report_days_spin.setSuffix(" 天")
        self.hk_events_mb_spin = QSpinBox()
        self.hk_events_mb_spin.setRange(5, 1024)
        self.hk_events_mb_spin.setPrefix("事件流上限 ")
        self.hk_events_mb_spin.setSuffix(" MB")
        self.report_dedupe_spin = QSpinBox()
        self.report_dedupe_spin.setRange(0, 168)
        self.report_dedupe_spin.setPrefix("同类报告去重 ")
        self.report_dedupe_spin.setSuffix(" 小时")
        for spin in (
            self.hk_log_days_spin,
            self.hk_report_days_spin,
            self.hk_events_mb_spin,
            self.report_dedupe_spin,
        ):
            spin.valueChanged.connect(lambda _v: self._queue_save("housekeeping", self._save_housekeeping))
            hk_row.addWidget(spin)
        hk_row.addStretch(1)
        self.housekeeping_card.body.addLayout(hk_row)
        maint_tab.addWidget(self.housekeeping_card)

        self.maint_actions_card = _SectionCard(
            "快捷维护",
            "手动立即运行一次日志清理，或快速打开日志与用户配置目录。",
        )
        maint_row = QHBoxLayout()
        maint_row.setSpacing(8)
        self.cleanup_now_btn = AnimatedButton("立即清理日志", variant="primary")
        self.cleanup_now_btn.setObjectName("primary")
        self.cleanup_now_btn.clicked.connect(self._on_cleanup_now_clicked)
        self.open_logs_dir_btn = AnimatedButton("打开日志目录")
        self.open_logs_dir_btn.clicked.connect(lambda: self.open_path_requested.emit(config.LOGS_DIR))
        self.open_configs_dir_btn = AnimatedButton("打开配置目录")
        self.open_configs_dir_btn.clicked.connect(lambda: self.open_path_requested.emit(config.ROOT / "configs"))
        self.maint_status_label = QLabel("")
        self.maint_status_label.setObjectName("hintLabel")
        maint_row.addWidget(self.cleanup_now_btn)
        maint_row.addWidget(self.open_logs_dir_btn)
        maint_row.addWidget(self.open_configs_dir_btn)
        maint_row.addWidget(self.maint_status_label, 1)
        self.maint_actions_card.body.addLayout(maint_row)
        maint_tab.addWidget(self.maint_actions_card)

        self.stats_card = _SectionCard(
            "历史运行耗时统计",
            "来自 logs/durations.jsonl，帮助评估各项目合适的卡死超时 (hang_timeout)。",
        )
        stats_head = QHBoxLayout()
        stats_head.setSpacing(8)
        self.stats_refresh_btn = AnimatedButton("刷新统计", variant="mini")
        self.stats_refresh_btn.clicked.connect(self._refresh_stats)
        stats_head.addWidget(self.stats_refresh_btn)
        stats_head.addStretch(1)
        self.stats_card.body.addLayout(stats_head)
        self.stats_view = QPlainTextEdit()
        self.stats_view.setReadOnly(True)
        self.stats_view.setMinimumHeight(180)
        self.stats_view.setFont(mono)
        enable_smooth_scroll(self.stats_view)
        self.stats_card.body.addWidget(self.stats_view)
        maint_tab.addWidget(self.stats_card)
        maint_tab.addStretch(1)

        # =====================================================================
        # 6. 高级设置
        # =====================================================================
        adv_tab = self._make_tab("advanced", "高级设置", "tools")
        if self.advanced is not None:
            adv_tab.addWidget(self.advanced)
        else:
            empty_adv = QLabel("未挂载高级设置面板")
            empty_adv.setObjectName("hintLabel")
            adv_tab.addWidget(empty_adv)
        adv_tab.addStretch(1)

        self._responsive_rows = (
            bg_row,
            auto_row,
            sched_row,
            days_row,
            idle_row,
            done_row,
            chain_row,
            post_row,
            emu_checks_row,
            emu_num_row,
            emu_main_row,
            emu_dev_row,
            emu_mgr_row,
            notify_top,
            hk_row,
            maint_row,
        )

    def _make_exe_row(self, label, placeholder):
        row = QHBoxLayout()
        row.setSpacing(8)
        cap = QLabel(label)
        cap.setObjectName("hintLabel")
        cap.setMinimumWidth(120)
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.editingFinished.connect(self._save_emulator)
        pick = AnimatedButton("选择…", variant="mini")
        pick.clicked.connect(lambda _=False, e=edit: self._pick_exe(e))
        row.addWidget(cap)
        row.addWidget(edit, 1)
        row.addWidget(pick)
        return edit, row

    def _pick_exe(self, edit):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择可执行文件", str(config.ROOT), "可执行文件 (*.exe);;所有文件 (*)"
        )
        if not path:
            return
        edit.setText(path)
        self._save_emulator()

    # -- 导航与响应式 ---------------------------------------------------------
    def select_section(self, key):
        entry = self._tab_layouts.get(key)
        if entry is None:
            return
        idx, _box = entry
        self.pages.setCurrentIndex(idx)
        self.sub_nav.set_current(key, emit=False)
        if key == "maintenance":
            self._refresh_stats()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() > 0 and self.width() < 560
        if narrow != self._stacked_mode:
            self._stacked_mode = narrow
            direction = QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight
            for row in self._responsive_rows:
                row.setDirection(direction)

    def refresh_theme(self):
        theme = current_theme()
        icon_map = {
            "general": "sliders",
            "automation": "play",
            "update_emulator": "refresh",
            "repair_notify": "shield",
            "maintenance": "file",
            "advanced": "tools",
        }
        col = themes.icon_color(theme, subtle=True)
        for key, icon_name in icon_map.items():
            self.sub_nav.set_tab_icon(key, icons.make(icon_name, col, 14))
        for btn in self.findChildren(AnimatedButton):
            btn.set_variant(btn.variant_name)

    # -- 数据填充 -------------------------------------------------------------
    def _populate_from_settings(self, settings):
        self._load_appearance(settings)
        gui_cfg = settings.get("gui") or {}
        self.minimize_to_tray_check.setChecked(bool(gui_cfg.get("minimize_to_tray", False)))
        self.autostart_chain_check.setChecked(bool(gui_cfg.get("autostart_chain_on_launch", False)))
        self.widget_check.setChecked(bool(gui_cfg.get("widget_enabled", False)))
        self.auto_open_logs_check.setChecked(bool(gui_cfg.get("auto_open_running_logs", True)))

        auto_info = autostart.status()
        mode = str(auto_info.get("mode") or autostart.MODE_TRAY)
        mode_idx = self.autostart_mode.findData(mode)
        self.autostart_mode.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
        self.autostart_check.setChecked(bool(auto_info.get("enabled")))
        self._refresh_autostart_hint(auto_info)

        sched_cfg = settings.get("schedule") or {}
        self.sched_enabled_check.setChecked(bool(sched_cfg.get("enabled", False)))
        self.sched_time_edit.setTime(_parse_hhmm(sched_cfg.get("time", "04:00")))
        active_days = set(
            listval.as_str_list(sched_cfg.get("days"), what="schedule.days")
            or [d for d, _ in WEEK_DAYS]
        )
        for day_key, chk in self.sched_day_checks.items():
            chk.setChecked(day_key in active_days)

        idle_cfg = settings.get("idle") or {}
        self.idle_check.setChecked(bool(idle_cfg.get("enabled", False)))
        self.idle_minutes.setValue(_as_int(idle_cfg.get("minutes", 30), 30, 1, 1440, what="idle.minutes"))
        self.idle_prompt.setValue(_as_int(idle_cfg.get("prompt_sec", 30), 30, 5, 600, what="idle.prompt_sec"))
        self.idle_cooldown.setValue(_as_int(idle_cfg.get("cooldown_min", 30), 30, 0, 1440, what="idle.cooldown_min"))
        self.idle_skip_check.setChecked(bool(idle_cfg.get("skip_if_completed_today", True)))
        self._refresh_idle_hint()
        self._refresh_completion_label()

        chain_cfg = settings.get("chain") or {}
        self.chain_continue_check.setChecked(bool(chain_cfg.get("continue_on_failure", True)))
        self.chain_retry_end_check.setChecked(bool(chain_cfg.get("retry_failed_at_end", True)))
        self.chain_max_attempts.setValue(
            _as_int(chain_cfg.get("max_attempts_per_project", 3), 3, 1, 10, what="chain.max_attempts_per_project")
        )
        self.hang_timeout_spin.setValue(
            _as_int(settings.get("hang_timeout_sec", 1800), 1800, 60, 14400, what="hang_timeout_sec")
        )

        post_cfg = settings.get("post_actions") or {}
        p_idx = self.post_combo.findData(str(post_cfg.get("after_all", "none")))
        self.post_combo.setCurrentIndex(p_idx if p_idx >= 0 else 0)
        self.post_delay.setValue(
            _as_int(post_cfg.get("delay_sec", 60), 60, 0, 86400, what="post_actions.delay_sec")
        )

        pre_cfg = settings.get("pre_run") or {}
        self.pre_run_enabled_check.setChecked(bool(pre_cfg.get("enabled", True)))
        self.pre_run_startup_check.setChecked(bool(pre_cfg.get("at_startup", True)))
        self.pre_run_projects_check.setChecked(bool(pre_cfg.get("update_projects", True)))
        self.pre_run_emulator_check.setChecked(bool(pre_cfg.get("update_emulator", True)))
        self.pre_run_concurrency.setValue(
            _as_int(pre_cfg.get("update_concurrency", 0), 0, 0, 16, what="pre_run.update_concurrency")
        )

        emu_cfg = pre_cfg.get("emulator") or {}
        self.emu_enabled_check.setChecked(bool(emu_cfg.get("enabled", True)))
        self.emu_manage_check.setChecked(bool(emu_cfg.get("manage", True)))
        self.emu_close_after_check.setChecked(bool(emu_cfg.get("close_after_run", True)))
        self.emu_minimize_check.setChecked(bool(emu_cfg.get("minimize_window", True)))
        self.emu_vmindex_spin.setValue(_as_int(emu_cfg.get("vmindex", 0), 0, 0, 32, what="emulator.vmindex"))
        self.emu_adb_port_spin.setValue(_as_int(emu_cfg.get("adb_port", 16384), 16384, 1024, 65535, what="emulator.adb_port"))
        self.emu_ready_timeout_spin.setValue(
            _as_int(emu_cfg.get("ready_timeout_sec", 240), 240, 30, 900, what="emulator.ready_timeout_sec")
        )
        u_idx = self.emu_updater_combo.findData(str(emu_cfg.get("updater_mode", "none")))
        self.emu_updater_combo.setCurrentIndex(u_idx if u_idx >= 0 else 0)
        self.emu_main_edit.setText(str(emu_cfg.get("main_exe") or ""))
        self.emu_device_edit.setText(str(emu_cfg.get("device_exe") or ""))
        self.emu_manager_edit.setText(str(emu_cfg.get("manager_exe") or ""))

        rep_cfg = settings.get("repair") or {}
        self.repair_enabled_check.setChecked(bool(rep_cfg.get("enabled", True)))
        self.repair_unsafe_check.setChecked(bool(rep_cfg.get("auto_unsafe", False)))
        self.repair_rounds_spin.setValue(_as_int(rep_cfg.get("max_rounds", 2), 2, 0, 10, what="repair.max_rounds"))
        self.repair_delay_spin.setValue(_as_int(rep_cfg.get("retry_delay_sec", 5), 5, 0, 300, what="repair.retry_delay_sec"))

        notif_cfg = settings.get("notifications") or {}
        self.notify_enabled_check.setChecked(bool(notif_cfg.get("enabled", False)))
        self.notify_toast_check.setChecked(bool(notif_cfg.get("toast", True)))
        self.notify_dedupe_spin.setValue(
            _as_int(notif_cfg.get("dedupe_sec", 30), 30, 0, 3600, what="notifications.dedupe_sec")
        )
        provider = str(notif_cfg.get("provider") or "serverchan")
        self.notify_provider_combo.setEditText(provider)
        params = notif_cfg.get("params") or {}
        try:
            self.notify_params_edit.setPlainText(json.dumps(params, ensure_ascii=False, indent=2))
        except Exception:
            self.notify_params_edit.setPlainText("{}")

        hk_cfg = settings.get("housekeeping") or {}
        self.hk_enabled_check.setChecked(bool(hk_cfg.get("enabled", True)))
        self.hk_log_days_spin.setValue(_as_int(hk_cfg.get("log_days", 14), 14, 1, 365, what="housekeeping.log_days"))
        self.hk_report_days_spin.setValue(_as_int(hk_cfg.get("report_days", 30), 30, 1, 365, what="housekeeping.report_days"))
        self.hk_events_mb_spin.setValue(_as_int(hk_cfg.get("events_max_mb", 50), 50, 5, 1024, what="housekeeping.events_max_mb"))
        self.report_dedupe_spin.setValue(
            _as_int(settings.get("report_dedupe_hours", 20), 20, 0, 168, what="report_dedupe_hours")
        )
        self._refresh_stats()

    # -- 防抖保存公共引擎 -----------------------------------------------------
    def _mutate_settings(self, mutate):
        settings = config.settings()
        mutate(settings)
        config.save_yaml(config.SETTINGS_YAML, settings)
        self.changed.emit()
        return settings

    def _queue_save(self, key, writer):
        if self._loading:
            return
        for index, (existing, _writer) in enumerate(self._pending_saves):
            if existing == key:
                self._pending_saves[index] = (key, writer)
                break
        else:
            self._pending_saves.append((key, writer))
        self._pending_timer.start()

    def _flush_pending_save(self):
        pending = self._pending_saves
        self._pending_saves = []
        for _key, writer in pending:
            try:
                writer()
            except Exception as exc:
                self._log("core", "error", "保存设置失败: {0}".format(exc))

    def flush_pending(self):
        if self._pending_saves:
            self._pending_timer.stop()
            self._flush_pending_save()

    # -- 各分组保存回调 -------------------------------------------------------
    def _load_appearance(self, settings=None):
        if settings is None:
            settings = config.settings()
        gui_cfg = settings.get("gui") or {}
        theme = themes.normalize_theme(gui_cfg.get("theme"))
        idx = self.theme_combo.findData(theme)
        if idx < 0:
            idx = self.theme_combo.findData(themes.DEFAULT_THEME)
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(max(0, idx))
        self.theme_combo.blockSignals(False)
        self.bg_edit.setText(str(gui_cfg.get("background_image") or ""))
        opacity = _as_int(gui_cfg.get("background_opacity", 100), 100, 0, 100, what="gui.background_opacity")
        self.bg_opacity.blockSignals(True)
        self.bg_opacity.setValue(opacity)
        self.bg_opacity.blockSignals(False)

    def _save_appearance(self, *_):
        if self._loading:
            return
        theme = str(self.theme_combo.currentData() or themes.DEFAULT_THEME)
        image = self.bg_edit.text().strip()
        opacity = int(self.bg_opacity.value())

        def mutate(settings):
            gui_cfg = settings.setdefault("gui", {})
            gui_cfg["theme"] = theme
            gui_cfg["background_image"] = image
            gui_cfg["background_opacity"] = opacity

        self._mutate_settings(mutate)
        self.theme_changed.emit(theme)
        self._log(
            "core", "info",
            "外观已保存：主题 {0}，背景图 {1}（不透明度 {2}%）".format(
                themes.THEME_LABELS.get(theme, theme), image or "无", opacity
            ),
        )

    def _pick_background(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图", str(config.ROOT), "图片 (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self.bg_edit.setText(path)
            self._save_appearance()

    def _clear_background(self):
        self.bg_edit.setText("")
        self._save_appearance()

    def _save_window_gui(self, *_):
        if self._loading:
            return
        m2t = bool(self.minimize_to_tray_check.isChecked())
        chain_launch = bool(self.autostart_chain_check.isChecked())
        widget_on = bool(self.widget_check.isChecked())
        auto_logs = bool(self.auto_open_logs_check.isChecked())

        def mutate(settings):
            gui_cfg = settings.setdefault("gui", {})
            gui_cfg["minimize_to_tray"] = m2t
            gui_cfg["autostart_chain_on_launch"] = chain_launch
            gui_cfg["widget_enabled"] = widget_on
            gui_cfg["auto_open_running_logs"] = auto_logs

        self._mutate_settings(mutate)

    def _save_autostart(self, *_):
        if self._loading:
            return
        enabled = bool(self.autostart_check.isChecked())
        mode = str(self.autostart_mode.currentData() or autostart.MODE_TRAY)
        result = autostart.apply(enabled, mode)
        if not result.get("ok"):
            self._log("core", "error", "开机启动设置失败: {0}".format(result.get("error", "")))
        else:
            self._log(
                "core", "info",
                "开机启动 {0}（{1}）".format("已启用" if enabled else "已关闭", autostart.MODE_LABELS.get(mode, mode)),
            )

        def mutate(settings):
            idle_cfg = settings.setdefault("idle", {})
            idle_cfg["autostart"] = enabled
            idle_cfg["autostart_mode"] = mode

        self._mutate_settings(mutate)
        self._refresh_autostart_hint()

    def _save_schedule(self, *_):
        if self._loading:
            return
        enabled = bool(self.sched_enabled_check.isChecked())
        when = self.sched_time_edit.time().toString("HH:mm")
        days = [k for k, _lbl in WEEK_DAYS if self.sched_day_checks[k].isChecked()]

        def mutate(settings):
            sched = settings.setdefault("schedule", {})
            sched["enabled"] = enabled
            sched["time"] = when
            sched["days"] = days

        self._mutate_settings(mutate)
        self._log("core", "info", "定时启动: {0} {1}（{2}天）".format("开" if enabled else "关", when, len(days)))

    def _save_idle(self, *_):
        if self._loading:
            return
        enabled = bool(self.idle_check.isChecked())
        minutes = int(self.idle_minutes.value())
        prompt_sec = int(self.idle_prompt.value())
        cooldown = int(self.idle_cooldown.value())
        skip_done = bool(self.idle_skip_check.isChecked())

        def mutate(settings):
            idle_cfg = settings.setdefault("idle", {})
            idle_cfg["enabled"] = enabled
            idle_cfg["minutes"] = minutes
            idle_cfg["prompt_sec"] = prompt_sec
            idle_cfg["cooldown_min"] = cooldown
            idle_cfg["skip_if_completed_today"] = skip_done

        self._mutate_settings(mutate)
        self._refresh_idle_hint()
        self._log(
            "core", "info",
            "空闲自动挂机 {0}：{1} 分钟无操作，提示 {2} 秒，冷却 {3} 分钟".format(
                "开" if enabled else "关", minutes, prompt_sec, cooldown
            ),
        )

    def _save_chain_policy(self, *_):
        if self._loading:
            return
        cont = bool(self.chain_continue_check.isChecked())
        retry_end = bool(self.chain_retry_end_check.isChecked())
        max_att = int(self.chain_max_attempts.value())
        hang_sec = int(self.hang_timeout_spin.value())

        def mutate(settings):
            chain = settings.setdefault("chain", {})
            chain["continue_on_failure"] = cont
            chain["retry_failed_at_end"] = retry_end
            chain["max_attempts_per_project"] = max_att
            settings["hang_timeout_sec"] = hang_sec

        self._mutate_settings(mutate)

    def _save_post_action(self, *_):
        if self._loading:
            return
        action = self.post_combo.currentData() or "none"
        delay = int(self.post_delay.value())

        def mutate(settings):
            post = settings.setdefault("post_actions", {})
            post["after_all"] = action
            post["delay_sec"] = delay

        self._mutate_settings(mutate)
        self._log("core", "info", "结束后操作: {0}（延迟 {1} 秒）".format(action, delay))

    def _save_pre_run(self, *_):
        if self._loading:
            return
        enabled = bool(self.pre_run_enabled_check.isChecked())
        at_startup = bool(self.pre_run_startup_check.isChecked())
        upd_proj = bool(self.pre_run_projects_check.isChecked())
        upd_emu = bool(self.pre_run_emulator_check.isChecked())
        conc = int(self.pre_run_concurrency.value())

        def mutate(settings):
            pre = settings.setdefault("pre_run", {})
            pre["enabled"] = enabled
            pre["at_startup"] = at_startup
            pre["update_projects"] = upd_proj
            pre["update_emulator"] = upd_emu
            pre["update_concurrency"] = conc

        self._mutate_settings(mutate)

    def _save_emulator(self, *_):
        if self._loading:
            return
        enabled = bool(self.emu_enabled_check.isChecked())
        manage = bool(self.emu_manage_check.isChecked())
        close_after = bool(self.emu_close_after_check.isChecked())
        minimize = bool(self.emu_minimize_check.isChecked())
        vmindex = int(self.emu_vmindex_spin.value())
        adb_port = int(self.emu_adb_port_spin.value())
        ready_sec = int(self.emu_ready_timeout_spin.value())
        upd_mode = str(self.emu_updater_combo.currentData() or "none")
        main_exe = self.emu_main_edit.text().strip()
        dev_exe = self.emu_device_edit.text().strip()
        mgr_exe = self.emu_manager_edit.text().strip()

        def mutate(settings):
            emu = settings.setdefault("pre_run", {}).setdefault("emulator", {})
            emu["enabled"] = enabled
            emu["manage"] = manage
            emu["close_after_run"] = close_after
            emu["minimize_window"] = minimize
            emu["vmindex"] = vmindex
            emu["adb_port"] = adb_port
            emu["ready_timeout_sec"] = ready_sec
            emu["updater_mode"] = upd_mode
            if main_exe:
                emu["main_exe"] = main_exe
            if dev_exe:
                emu["device_exe"] = dev_exe
            if mgr_exe:
                emu["manager_exe"] = mgr_exe

        self._mutate_settings(mutate)

    def _save_repair(self, *_):
        if self._loading:
            return
        enabled = bool(self.repair_enabled_check.isChecked())
        unsafe = bool(self.repair_unsafe_check.isChecked())
        rounds = int(self.repair_rounds_spin.value())
        delay = int(self.repair_delay_spin.value())

        def mutate(settings):
            rep = settings.setdefault("repair", {})
            rep["enabled"] = enabled
            rep["auto_unsafe"] = unsafe
            rep["max_rounds"] = rounds
            rep["retry_delay_sec"] = delay

        self._mutate_settings(mutate)

    def _save_notifications(self, *_):
        if self._loading:
            return
        enabled = bool(self.notify_enabled_check.isChecked())
        toast = bool(self.notify_toast_check.isChecked())
        dedupe = int(self.notify_dedupe_spin.value())
        provider = self.notify_provider_combo.currentText().strip()

        def mutate(settings):
            n = settings.setdefault("notifications", {})
            n["enabled"] = enabled
            n["toast"] = toast
            n["dedupe_sec"] = dedupe
            n["provider"] = provider

        self._mutate_settings(mutate)

    def _save_notify_params(self):
        raw = self.notify_params_edit.toPlainText().strip() or "{}"
        try:
            parsed = json.loads(raw)
        except Exception as exc:
            self.notify_status_label.setText("JSON 格式错误: {0}".format(exc))
            return False
        if not isinstance(parsed, dict):
            self.notify_status_label.setText("推送参数必须是 JSON 对象（{...}）")
            return False

        def mutate(settings):
            n = settings.setdefault("notifications", {})
            n["enabled"] = bool(self.notify_enabled_check.isChecked())
            n["toast"] = bool(self.notify_toast_check.isChecked())
            n["dedupe_sec"] = int(self.notify_dedupe_spin.value())
            n["provider"] = self.notify_provider_combo.currentText().strip()
            n["params"] = parsed

        self._mutate_settings(mutate)
        self.notify_status_label.setText("推送参数已保存")
        self._log("core", "info", "通知推送参数已保存")
        return True

    def _on_notify_test_clicked(self):
        if not self._save_notify_params():
            return
        self.notify_test_btn.setEnabled(False)
        self.notify_status_label.setText("正在发送测试通知…")

        def worker():
            try:
                from afk_core import notify
                from afk_core.events import EventBus

                bus = self._bus or EventBus()
                cfg = config.settings()
                # 测试按钮即点即测，临时允许 enabled=True 发送一次
                test_cfg = dict(cfg)
                notif = dict(cfg.get("notifications") or {})
                notif["enabled"] = True
                test_cfg["notifications"] = notif
                sent = notify.Notifier(bus, test_cfg).send(
                    "Auto-AFK 测试通知", "这是一条来自设置页的测试推送"
                )
                msg = "测试通知已发送" if sent else "未启用 Toast 且未配置有效推送通道"
                self._notify_test_done.emit(bool(sent), msg)
            except Exception as exc:
                self._notify_test_done.emit(False, "发送失败: {0}".format(exc))

        threading.Thread(target=worker, daemon=True, name="afk-notify-test").start()

    def _on_notify_test_done(self, ok, msg):
        self.notify_test_btn.setEnabled(True)
        self.notify_status_label.setText(msg)
        self._log("core", "info" if ok else "warn", msg)

    def _save_housekeeping(self, *_):
        if self._loading:
            return
        enabled = bool(self.hk_enabled_check.isChecked())
        log_days = int(self.hk_log_days_spin.value())
        rep_days = int(self.hk_report_days_spin.value())
        events_mb = int(self.hk_events_mb_spin.value())
        dedupe_h = int(self.report_dedupe_spin.value())

        def mutate(settings):
            hk = settings.setdefault("housekeeping", {})
            hk["enabled"] = enabled
            hk["log_days"] = log_days
            hk["report_days"] = rep_days
            hk["events_max_mb"] = events_mb
            settings["report_dedupe_hours"] = dedupe_h

        self._mutate_settings(mutate)

    def _on_cleanup_now_clicked(self):
        self.cleanup_now_btn.setEnabled(False)
        self.maint_status_label.setText("正在清理过期日志与报告…")

        def worker():
            try:
                from afk_core import housekeeping

                cfg = config.settings()
                # 手动立即清理不受 housekeeping.enabled=false 限制
                run_cfg = dict(cfg)
                hk = dict(cfg.get("housekeeping") or {})
                hk["enabled"] = True
                run_cfg["housekeeping"] = hk
                res = housekeeping.cleanup_logs(self._bus, run_cfg) or {}
                self._cleanup_done.emit(
                    "清理完成：移除过期日志/报告 {0} 项".format(res.get("removed", 0))
                )
            except Exception as exc:
                self._cleanup_done.emit("清理失败: {0}".format(exc))

        threading.Thread(target=worker, daemon=True, name="afk-cleanup-now").start()

    def _on_cleanup_done(self, msg):
        self.cleanup_now_btn.setEnabled(True)
        self.maint_status_label.setText(msg)
        self._log("core", "info", msg)

    def _refresh_stats(self):
        try:
            from afk_core import report

            stats = report.duration_stats()
        except Exception as exc:
            self.stats_view.setPlainText("读取统计失败：{0}".format(exc))
            return
        if not stats:
            self.stats_view.setPlainText("暂无历史运行记录（logs/durations.jsonl 为空）。")
            return
        lines = []
        for proj, info in stats.items():
            lines.append(
                "{0:<26} 运行 {runs:>3} 次 | 平均 {avg_sec:>6.1f}s | 最长 {max_sec:>6.1f}s | 上次 {last_sec:>6.1f}s ({last_status})".format(
                    proj, **info
                )
            )
        self.stats_view.setPlainText("\n".join(lines))

    # -- 状态提示与外部重载 ---------------------------------------------------
    def _refresh_completion_label(self):
        try:
            from afk_core import completion

            info = completion.completion_info()
            if info.get("completed_today"):
                self.completion_label.setText("今日状态：已完成（空闲时不再自动执行）")
                self.clear_completion_btn.setEnabled(True)
            else:
                self.completion_label.setText("今日状态：未完成")
                self.clear_completion_btn.setEnabled(False)
        except Exception as exc:
            self.completion_label.setText("今日状态：读取失败 {0}".format(exc))

    def _clear_completion(self):
        try:
            from afk_core import completion

            completion.clear()
            self._log("core", "info", "已清除今日完成标记，空闲时会再次自动挂机")
        except Exception as exc:
            self._log("core", "error", "清除今日完成标记失败: {0}".format(exc))
        self._refresh_completion_label()

    def _refresh_idle_hint(self):
        try:
            from afk_core import idle as idle_mod

            support = "支持" if idle_mod.supported() else "当前系统不支持空闲检测"
            self.idle_hint.setText(
                "空闲检测：{0}；阈值最小 60 秒。保存后立即生效，无需重启。".format(support)
            )
        except Exception as exc:
            self.idle_hint.setText("空闲检测不可用: {0}".format(exc))

    def _refresh_autostart_hint(self, info=None):
        if info is None:
            info = autostart.status()
        if not info.get("supported"):
            self.autostart_hint.setText("当前系统不支持写入注册表自启项。")
            return
        if info.get("enabled"):
            self.autostart_hint.setText(
                "已注册：{0}\n{1}".format(info.get("command", ""), info.get("run_key", ""))
            )
        else:
            self.autostart_hint.setText("当前未注册开机启动（{0}）。".format(info.get("run_key", "")))

    def reload_from_disk(self):
        """只读一次 settings.yaml 刷新全部分组控件。"""
        self.flush_pending()
        self._loading = True
        try:
            settings = config.settings()
            self._settings_snapshot = settings
            self._populate_from_settings(settings)
            advanced = self.advanced
            if advanced is not None:
                advanced.sync_settings_editor()
                if advanced.expanded_name() == "reports":
                    advanced.maybe_refresh_reports()
        finally:
            self._loading = False
