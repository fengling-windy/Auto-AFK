"""项目配置页：顶部项目标签导航 + 安装支持项目 + 单项目环境检测/重装/删除/界面入口/参数编辑。"""

import copy
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from afk_core import config, doctor, project_ops
from gui import icons, themes
from gui.modern import (
    AnimatedButton,
    ElidedLabel,
    HoverGlowCard,
    SubNavBar,
    current_theme,
    enable_smooth_scroll,
)

# 这些键不重复展示：display_name 已经在标题上、args 太长单独一行、
# 其余的 patterns/env/gui 之类属于实现细节，注册表页里能看到全文。
HIDDEN_KEYS = {
    "display_name",
    "base",
    "enabled",
    "args",
    "success_patterns",
    "error_patterns",
    "links",
    "sync_files",
    "env",
    "gui",
    "reserved",
}

# 参数名给人看的中文标签；没有的就直接用键名。
KEY_LABELS = {
    "launch_type": "启动方式",
    "path": "目录",
    "python": "解释器",
    "exe": "可执行文件",
    "config_dir": "配置目录",
    "config_name": "配置文件",
    "instance": "实例",
    "retry": "重试次数",
    "hang_timeout": "卡死超时(秒)",
    "require_admin": "需要管理员",
    "require_finish": "要求完成标记",
    "min_runtime_sec": "最短运行(秒)",
    "repo": "仓库",
    "version": "版本",
    "description": "说明",
    "extra_pythonpath": "额外 PYTHONPATH",
    "finish_patterns": "完成标记",
    "warn_patterns": "告警标记",
    "log_files": "外部日志",
    "manage_emulator": "接管模拟器",
    "report_on_warn": "告警即算失败",
    "stop_on_error": "出错就停止",
    "stop_on_error_delay_sec": "出错停止延迟(秒)",
    "stop_on_finish": "完成后停止进程",
    "update_submodules": "更新子模块",
    "update_hooks": "更新步骤",
    "window_absent_sec": "窗口消失(秒)",
    "window_finish": "等窗口结束",
    "window_keywords": "窗口标题",
}


def read_version(project_dir):
    """读子项目的 git 版本号（describe 优先，退化到短 hash）。"""
    path = Path(project_dir)
    if not path.is_dir():
        return ""
    for args in (
        ["git", "describe", "--tags", "--always", "--dirty"],
        ["git", "rev-parse", "--short", "HEAD"],
    ):
        try:
            out = subprocess.run(
                args,
                cwd=str(path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        text = (out.stdout or "").strip()
        if out.returncode == 0 and text:
            return text
    return ""


def _dot_icon(color, size=10):
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pix)


def _repo_url(repo):
    text = str(repo or "").strip()
    if not text:
        return ""
    if text.startswith("http"):
        return text
    return "https://github.com/" + text.strip("/")


class _ProjectBlock(QFrame):
    """一个项目的汇总块（兼容保留）。"""

    def __init__(self, key, spec, color, parent=None):
        super().__init__(parent)
        self.key = key
        self.spec = spec or {}
        self.setObjectName("card")
        self._caps = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        bar = QFrame()
        bar.setFixedSize(4, 18)
        bar.setStyleSheet("background: {0}; border-radius: 2px;".format(color))
        head.addWidget(bar)
        name = str(self.spec.get("display_name", key))
        title = ElidedLabel(name, min_width=80, max_hint_width=520)
        title.setObjectName("sectionTitleCard")
        title.setToolTip(name)
        head.addWidget(title, 1)
        self.state_label = QLabel("")
        self.state_label.setObjectName("hintLabel")
        self.state_label.setMinimumWidth(48)
        head.addWidget(self.state_label)
        self.repo_btn = AnimatedButton("打开仓库", variant="mini")
        self.repo_btn.setIcon(icons.make("file", themes.icon_color(current_theme(), subtle=True), 12))
        self.repo_btn.setVisible(bool(self.spec.get("repo")))
        self.repo_btn.clicked.connect(self._open_repo)
        head.addWidget(self.repo_btn)
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.grid = grid
        self._rows = 0

        args = self.spec.get("args") or []
        if args:
            self._add_row("启动参数", " ".join(str(a) for a in args))
        for key_name, value in self.spec.items():
            if key_name in HIDDEN_KEYS:
                continue
            label = KEY_LABELS.get(key_name, key_name)
            self._add_row(label, self._format(value, key_name))
        if not hasattr(self, "_version_label"):
            self._add_row(KEY_LABELS["version"], "未知")

    @staticmethod
    def _format(value, key_name=""):
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (list, tuple)):
            labels = [
                str(item.get("label") or item.get("type") or "")
                for item in value
                if isinstance(item, dict)
            ]
            if labels and len(labels) == len(value):
                return " → ".join(labels)
            return ", ".join(str(v) for v in value)
        if isinstance(value, dict):
            return " / ".join("{0}: {1}".format(k, v) for k, v in value.items())
        return str(value)

    def _add_row(self, label, value):
        cap = QLabel(label)
        cap.setObjectName("hintLabel")
        cap.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        text = ElidedLabel(str(value), min_width=60, max_hint_width=760)
        text.setObjectName("cardDetail")
        text.setToolTip(str(value))
        self.grid.addWidget(cap, self._rows, 0)
        self.grid.addWidget(text, self._rows, 1)
        self._rows += 1
        self._caps.append(cap)
        if label == KEY_LABELS["version"]:
            self._version_label = text

    def set_version(self, text):
        if not hasattr(self, "_version_label"):
            return
        if not text:
            if self._version_label.text() not in ("", "未知"):
                return
            text = "未知"
        self._version_label.setText(text)
        self._version_label.setToolTip(text)

    def align_caps(self, width):
        width = int(width)
        if width <= 0:
            return
        for cap in self._caps:
            cap.setFixedWidth(width)

    def cap_width_hint(self):
        if not self._caps:
            return 0
        font = self._caps[0].font()
        fm = QFontMetrics(font)
        return max(fm.horizontalAdvance(cap.text()) for cap in self._caps)

    def set_state(self, enabled):
        self.state_label.setText("已启用" if enabled else "已禁用")

    def _open_repo(self):
        url = _repo_url(self.spec.get("repo"))
        if url:
            QDesktopServices.openUrl(QUrl(url))


class ProjectConfigPage(QWidget):
    """项目配置页：顶部项目标签栏 + 安装支持项目 + 详情操作与参数编辑。"""

    version_ready = Signal(str, str)
    projects_changed = Signal()
    open_gui_requested = Signal(str)
    open_path_requested = Signal(object)
    _op_line = Signal(str)
    _op_done = Signal(str, bool, str)
    _doctor_ready = Signal(str, list)

    def __init__(self, registry_getter=None, color_getter=None, log_fn=None, bus=None, parent=None):
        super().__init__(parent)
        self._registry_getter = registry_getter or config.registry
        self._color_getter = color_getter or (
            lambda key: themes.project_fallback_color(current_theme(), key)
        )
        self._log = log_fn or (lambda *a, **k: None)
        self._bus = bus

        self._version_cache = {}
        self._scan_pending = []
        self._scan_lock = threading.Lock()
        self._scan_inflight = set()
        self._version_thread = None

        self._current_key = ""
        self._op_busy = False
        self._loading_form = False
        self._rendered_snapshot = None

        self.version_ready.connect(self._apply_version)
        self._op_line.connect(self._append_op_line)
        self._op_done.connect(self._on_op_done)
        self._doctor_ready.connect(self._render_project_doctor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(10)

        # -- 顶部导航栏 + 右侧安装控制区 --------------------------------------
        self.sub_nav = SubNavBar()
        self.sub_nav.current_changed.connect(self.select_project)

        self.install_combo = QComboBox()
        self.install_combo.setMinimumWidth(190)
        self.install_combo.setToolTip("选择要安装的受支持项目")
        self.install_combo.currentIndexChanged.connect(lambda *_: self._sync_install_button())

        self.install_btn = AnimatedButton("安装项目", variant="primary")
        self.install_btn.setObjectName("primary")
        self.install_btn.clicked.connect(self._on_install_clicked)

        self.sub_nav.add_trailing(self.install_combo)
        self.sub_nav.add_trailing(self.install_btn)
        outer.addWidget(self.sub_nav)

        # -- 详情滚动区 -------------------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        enable_smooth_scroll(scroll)
        inner = QWidget()
        inner.setObjectName("central")
        body = QVBoxLayout(inner)
        body.setContentsMargins(2, 2, 6, 8)
        body.setSpacing(12)

        self._empty_label = QLabel("当前没有已注册项目，可在右上角下拉框选择并安装支持的项目。")
        self._empty_label.setObjectName("pagePlaceholder")
        self._empty_label.setAlignment(Qt.AlignCenter)
        body.addWidget(self._empty_label)

        # Card 1: 概览 & 操作按钮
        self.hero_card = HoverGlowCard()
        self.hero_card.setObjectName("card")
        hero_layout = QVBoxLayout(self.hero_card)
        hero_layout.setContentsMargins(18, 14, 18, 14)
        hero_layout.setSpacing(10)

        hero_top = QHBoxLayout()
        hero_top.setSpacing(10)
        self.accent_bar = QFrame()
        self.accent_bar.setFixedSize(4, 22)
        self.title_label = ElidedLabel("", min_width=80, max_hint_width=460)
        self.title_label.setObjectName("sectionTitleCard")
        self.key_pill = QLabel("")
        self.key_pill.setObjectName("configPill")
        self.status_badge = QLabel("")
        self.status_badge.setObjectName("configPill")
        self.version_pill = QLabel("版本：未知")
        self.version_pill.setObjectName("configPill")
        self.refresh_ver_btn = AnimatedButton("刷新版本", variant="mini")
        self.refresh_ver_btn.clicked.connect(lambda *_: self.reload(force_versions=True))
        self.repo_btn = AnimatedButton("GitHub", variant="mini")
        self.repo_btn.clicked.connect(self._open_repo)
        hero_top.addWidget(self.accent_bar)
        hero_top.addWidget(self.title_label, 1)
        hero_top.addWidget(self.key_pill)
        hero_top.addWidget(self.status_badge)
        hero_top.addWidget(self.version_pill)
        hero_top.addWidget(self.refresh_ver_btn)
        hero_top.addWidget(self.repo_btn)
        hero_layout.addLayout(hero_top)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.gui_btn = AnimatedButton("打开此项目界面", variant="primary")
        self.gui_btn.setObjectName("primary")
        self.gui_btn.clicked.connect(lambda: self.open_gui_requested.emit(self._current_key) if self._current_key else None)
        self.doctor_btn = AnimatedButton("环境检测")
        self.doctor_btn.clicked.connect(self._on_doctor_clicked)
        self.update_btn = AnimatedButton("更新项目")
        self.update_btn.clicked.connect(self._on_update_clicked)
        self.reinstall_btn = AnimatedButton("重装项目")
        self.reinstall_btn.clicked.connect(self._on_reinstall_clicked)
        self.delete_btn = AnimatedButton("删除项目", variant="danger")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        for btn in (self.gui_btn, self.doctor_btn, self.update_btn, self.reinstall_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        actions.addWidget(self.delete_btn)
        hero_layout.addLayout(actions)
        body.addWidget(self.hero_card)

        # Card 2: 参数信息与核心配置编辑
        self.meta_card = HoverGlowCard()
        self.meta_card.setObjectName("card")
        meta_layout = QVBoxLayout(self.meta_card)
        meta_layout.setContentsMargins(18, 14, 18, 14)
        meta_layout.setSpacing(10)

        meta_cap = QLabel("核心参数与配置")
        meta_cap.setObjectName("sectionTitleCard")
        meta_layout.addWidget(meta_cap)

        self._meta_grid = QGridLayout()
        self._meta_grid.setHorizontalSpacing(14)
        self._meta_grid.setVerticalSpacing(6)
        self._meta_grid.setColumnStretch(1, 1)
        self._meta_values = {}
        self._meta_caps = []
        self._open_project_dir_btn = AnimatedButton("打开目录", variant="mini")
        self._open_project_dir_btn.clicked.connect(lambda: self._emit_dir("project"))
        self._open_config_dir_btn = AnimatedButton("打开配置", variant="mini")
        self._open_config_dir_btn.clicked.connect(lambda: self._emit_dir("config"))

        meta_rows = [
            ("repo", "GitHub 仓库", None),
            ("version", "当前版本", None),
            ("launch_type", "启动方式", None),
            ("path", "项目目录", self._open_project_dir_btn),
            ("config_dir", "配置目录", self._open_config_dir_btn),
            ("runner", "解释器 / 程序", None),
            ("args", "启动参数", None),
            ("configs", "已发现配置", None),
        ]
        for r_idx, (field, label, extra_btn) in enumerate(meta_rows):
            cap = QLabel(label)
            cap.setObjectName("hintLabel")
            cap.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val = ElidedLabel("", min_width=60, max_hint_width=760)
            val.setObjectName("cardDetail")
            self._meta_grid.addWidget(cap, r_idx, 0)
            self._meta_grid.addWidget(val, r_idx, 1)
            if extra_btn is not None:
                self._meta_grid.addWidget(extra_btn, r_idx, 2)
            self._meta_caps.append(cap)
            self._meta_values[field] = val
        cap_w = max(QFontMetrics(self._meta_caps[0].font()).horizontalAdvance(c.text()) for c in self._meta_caps) + 10
        for cap in self._meta_caps:
            cap.setFixedWidth(cap_w)
        meta_layout.addLayout(self._meta_grid)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("toolbarDivider")
        meta_layout.addWidget(sep)

        edit_cap = QLabel("可编辑运行参数（保存后写入 afk_projects.yaml）")
        edit_cap.setObjectName("hintLabel")
        meta_layout.addWidget(edit_cap)

        form_row1 = QHBoxLayout()
        form_row1.setSpacing(10)
        self.enabled_check = QCheckBox("启用此项目")
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 10)
        self.retry_spin.setPrefix("重试 ")
        self.retry_spin.setSuffix(" 次")
        self.hang_spin = QSpinBox()
        self.hang_spin.setRange(60, 14400)
        self.hang_spin.setPrefix("卡死超时 ")
        self.hang_spin.setSuffix(" 秒")
        self.min_runtime_spin = QSpinBox()
        self.min_runtime_spin.setRange(0, 3600)
        self.min_runtime_spin.setPrefix("最短运行 ")
        self.min_runtime_spin.setSuffix(" 秒")
        form_row1.addWidget(self.enabled_check)
        form_row1.addWidget(self.retry_spin)
        form_row1.addWidget(self.hang_spin)
        form_row1.addWidget(self.min_runtime_spin)
        form_row1.addStretch(1)
        meta_layout.addLayout(form_row1)

        form_row2 = QHBoxLayout()
        form_row2.setSpacing(12)
        self.admin_check = QCheckBox("需要管理员权限")
        self.finish_check = QCheckBox("要求完成标记")
        self.emu_check = QCheckBox("接管模拟器")
        self.stop_err_check = QCheckBox("出错即停止")
        self.win_finish_check = QCheckBox("等窗口结束")
        for chk in (self.admin_check, self.finish_check, self.emu_check, self.stop_err_check, self.win_finish_check):
            form_row2.addWidget(chk)
        form_row2.addStretch(1)
        meta_layout.addLayout(form_row2)

        form_row3 = QHBoxLayout()
        form_row3.setSpacing(8)
        self.default_cfg_cap = QLabel("默认配置文件 / 实例")
        self.default_cfg_cap.setObjectName("hintLabel")
        self.default_cfg_combo = QComboBox()
        self.default_cfg_combo.setEditable(True)
        self.default_cfg_combo.setMinimumWidth(200)
        self.save_params_btn = AnimatedButton("保存参数", variant="primary")
        self.save_params_btn.setObjectName("primary")
        self.save_params_btn.clicked.connect(self._save_params)
        form_row3.addWidget(self.default_cfg_cap)
        form_row3.addWidget(self.default_cfg_combo)
        form_row3.addStretch(1)
        form_row3.addWidget(self.save_params_btn)
        meta_layout.addLayout(form_row3)
        body.addWidget(self.meta_card)

        # Card 3: 诊断 / 安装 / 更新输出
        self.output_card = HoverGlowCard()
        self.output_card.setObjectName("card")
        out_layout = QVBoxLayout(self.output_card)
        out_layout.setContentsMargins(18, 14, 18, 14)
        out_layout.setSpacing(8)
        out_head = QHBoxLayout()
        out_head.setSpacing(8)
        self.output_title = QLabel("环境检测 / 操作输出")
        self.output_title.setObjectName("sectionTitleCard")
        self.output_summary = QLabel("")
        self.output_summary.setObjectName("hintLabel")
        self.clear_output_btn = AnimatedButton("清空", variant="mini")
        out_head.addWidget(self.output_title)
        out_head.addWidget(self.output_summary, 1)
        out_head.addWidget(self.clear_output_btn)
        out_layout.addLayout(out_head)

        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setUndoRedoEnabled(False)
        self.output_view.setMaximumBlockCount(2000)
        self.output_view.setMinimumHeight(180)
        mono = QFont("Cascadia Mono", 9)
        mono.setStyleHint(QFont.Monospace)
        self.output_view.setFont(mono)
        enable_smooth_scroll(self.output_view)
        self.clear_output_btn.clicked.connect(self.output_view.clear)
        out_layout.addWidget(self.output_view)
        body.addWidget(self.output_card)

        body.addStretch(1)
        scroll.setWidget(inner)
        self._scroll = scroll
        outer.addWidget(scroll, 1)

        self.reload()

    # -- 数据与导航同步 -------------------------------------------------------
    def _projects(self):
        return (self._registry_getter() or {}).get("projects", {}) or {}

    def _project_path(self, key, spec):
        path = (spec or {}).get("path")
        if not path:
            return ""
        return str(config.ROOT / str(path))

    def _populate_install_combo(self, registry):
        cur = self.install_combo.currentData()
        self.install_combo.blockSignals(True)
        self.install_combo.clear()
        for item in project_ops.SUPPORTED_PROJECTS:
            k = item["key"]
            short = str(item["display_name"]).split("（")[0]
            installed = project_ops.is_project_installed(k, registry)
            label = "{0}（已安装）".format(short) if installed else "{0}（{1}）".format(short, k)
            self.install_combo.addItem(label, k)
        idx = self.install_combo.findData(cur)
        self.install_combo.setCurrentIndex(max(0, idx))
        self.install_combo.blockSignals(False)
        self._sync_install_button(registry)

    def _sync_install_button(self, registry=None):
        registry = registry if registry is not None else self._registry_getter()
        key = str(self.install_combo.currentData() or "")
        installed = bool(key) and project_ops.is_project_installed(key, registry)
        if installed:
            self.install_btn.setText("已安装")
            self.install_btn.setEnabled(False)
            self.install_btn.setToolTip("{0} 已安装；如需重新部署请点下方的「重装项目」".format(key))
        else:
            self.install_btn.setText("安装项目")
            self.install_btn.setEnabled(bool(key) and not self._op_busy)
            self.install_btn.setToolTip("克隆并构建安装 {0}".format(key))

    def reload(self, force_versions=False):
        registry = self._registry_getter() or {}
        projects = registry.get("projects", {}) or {}
        theme = current_theme()
        colors = tuple((k, str(self._color_getter(k))) for k in projects)
        snap = (copy.deepcopy(projects), theme, colors)

        self._populate_install_combo(registry)

        if (
            not force_versions
            and self._rendered_snapshot == snap
            and self.sub_nav.tab_keys() == list(projects.keys())
        ):
            self._start_version_scan(projects, force=False)
            if self._current_key in projects:
                self._sync_install_button(registry)
                return

        self._rendered_snapshot = snap
        self.sub_nav.clear_tabs()
        for key, spec in projects.items():
            short = str((spec or {}).get("display_name", key)).split("（")[0]
            self.sub_nav.add_tab(
                key,
                short,
                icon=_dot_icon(self._color_getter(key)),
                tooltip=str((spec or {}).get("display_name", key)),
            )

        has = bool(projects)
        self._empty_label.setVisible(not has)
        self.hero_card.setVisible(has)
        self.meta_card.setVisible(has)
        self.output_card.setVisible(has)

        if has:
            target = self._current_key if self._current_key in projects else next(iter(projects))
            self.sub_nav.set_current(target, emit=False)
            self.select_project(target)
        else:
            self._current_key = ""
        self._start_version_scan(projects, force=force_versions)

    def select_project(self, key):
        projects = self._projects()
        spec = projects.get(key)
        if spec is None:
            return
        self._current_key = key
        self.sub_nav.set_current(key, emit=False)
        spec = spec or {}
        color = self._color_getter(key)
        registry = self._registry_getter() or {}
        installed = project_ops.is_project_installed(key, registry)
        enabled = bool(spec.get("enabled", True))

        self.accent_bar.setStyleSheet("background: {0}; border-radius: 2px;".format(color))
        name = str(spec.get("display_name", key))
        self.title_label.setText(name)
        self.title_label.setToolTip(name)
        self.key_pill.setText(key)
        self.status_badge.setText(
            "{0} · {1}".format("已安装" if installed else "未安装", "已启用" if enabled else "已禁用")
        )

        pdir = self._project_path(key, spec)
        cached_ver = self._version_cache.get(pdir) or str(spec.get("version") or "")
        ver_text = cached_ver or "未知"
        self.version_pill.setText("版本：" + ver_text)

        repo = str(spec.get("repo") or "").strip()
        url = _repo_url(repo)
        self.repo_btn.setVisible(bool(url))
        self.repo_btn.setText(repo or "GitHub")
        self.repo_btn.setToolTip(url)

        has_gui = bool(spec.get("gui"))
        self.gui_btn.setEnabled(has_gui and installed and not self._op_busy)
        self.gui_btn.setToolTip(
            "独立打开 {0} 的原配置界面".format(key)
            if (has_gui and installed)
            else ("项目目录不存在，请先安装" if not installed else "注册表未配置 gui 入口")
        )
        self.update_btn.setEnabled(installed and not self._op_busy)
        self.reinstall_btn.setEnabled(not self._op_busy)
        self.doctor_btn.setEnabled(not self._op_busy)
        self.delete_btn.setEnabled(not self._op_busy)

        # -- 元数据网格 -------------------------------------------------------
        cfg_dir = config.config_dir_for(key, spec)
        runner = spec.get("exe") or spec.get("python") or "-"
        args = " ".join(str(a) for a in (spec.get("args") or [])) or "-"
        discovered = project_ops.discover_project_configs(key, spec)
        cfg_summary = "、".join(lbl for lbl, _v in discovered) if discovered else "未发现（使用默认配置）"

        meta_map = {
            "repo": url or "未填写",
            "version": ver_text,
            "launch_type": str(spec.get("launch_type") or "-"),
            "path": pdir or "未填写",
            "config_dir": str(cfg_dir),
            "runner": str(runner),
            "args": args,
            "configs": cfg_summary,
        }
        for field, val_text in meta_map.items():
            lbl = self._meta_values[field]
            lbl.setText(val_text)
            lbl.setToolTip(val_text)
        self._open_project_dir_btn.setEnabled(bool(pdir) and Path(pdir).exists())
        self._open_config_dir_btn.setEnabled(cfg_dir.exists())

        # -- 可编辑表单 -------------------------------------------------------
        self._loading_form = True
        try:
            self.enabled_check.setChecked(enabled)
            self.retry_spin.setValue(int(spec.get("retry", 1) or 0))
            self.hang_spin.setValue(max(60, int(spec.get("hang_timeout", 900) or 900)))
            self.min_runtime_spin.setValue(max(0, int(spec.get("min_runtime_sec", 0) or 0)))
            self.admin_check.setChecked(bool(spec.get("require_admin", False)))
            self.finish_check.setChecked(bool(spec.get("require_finish", False)))
            self.emu_check.setChecked(bool(spec.get("manage_emulator", False)))
            self.stop_err_check.setChecked(bool(spec.get("stop_on_error", False)))
            self.win_finish_check.setChecked(bool(spec.get("window_finish", False)))

            uses_instance = key == "MaaEnd" or any("{instance}" in str(a) for a in (spec.get("args") or []))
            cur_cfg = str(spec.get("instance" if uses_instance else "config_name") or "")
            self.default_cfg_cap.setText("默认实例" if uses_instance else "默认配置文件")
            self.default_cfg_combo.blockSignals(True)
            self.default_cfg_combo.clear()
            self.default_cfg_combo.addItem("", "")
            for lbl, val in discovered:
                self.default_cfg_combo.addItem(val, val)
            if cur_cfg and self.default_cfg_combo.findData(cur_cfg) < 0:
                self.default_cfg_combo.addItem(cur_cfg, cur_cfg)
            self.default_cfg_combo.setEditText(cur_cfg)
            self.default_cfg_combo.blockSignals(False)
        finally:
            self._loading_form = False

    def refresh_theme(self):
        self._rendered_snapshot = None
        for btn in (
            self.install_btn,
            self.refresh_ver_btn,
            self.repo_btn,
            self.gui_btn,
            self.doctor_btn,
            self.update_btn,
            self.reinstall_btn,
            self.delete_btn,
            self._open_project_dir_btn,
            self._open_config_dir_btn,
            self.save_params_btn,
            self.clear_output_btn,
        ):
            btn.set_variant(btn.variant_name)
        self.reload()

    # -- 操作与后台任务 -------------------------------------------------------
    def _emit_dir(self, kind):
        spec = self._projects().get(self._current_key) or {}
        if kind == "project":
            pdir = self._project_path(self._current_key, spec)
            if pdir:
                self.open_path_requested.emit(Path(pdir))
        else:
            self.open_path_requested.emit(config.config_dir_for(self._current_key, spec))

    def _open_repo(self):
        spec = self._projects().get(self._current_key) or {}
        url = _repo_url(spec.get("repo"))
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _save_params(self):
        key = self._current_key
        if not key or self._loading_form:
            return
        try:
            data = config.load_yaml(config.PROJECTS_YAML, {}) or {}
        except Exception as exc:
            QMessageBox.critical(self, "读取失败", str(exc))
            return
        projects = data.setdefault("projects", {})
        spec = projects.get(key)
        if not isinstance(spec, dict):
            QMessageBox.warning(self, "未找到项目", "afk_projects.yaml 中没有 {0}".format(key))
            return
        spec["enabled"] = bool(self.enabled_check.isChecked())
        spec["retry"] = int(self.retry_spin.value())
        spec["hang_timeout"] = int(self.hang_spin.value())
        spec["min_runtime_sec"] = int(self.min_runtime_spin.value())
        for field, chk in (
            ("require_admin", self.admin_check),
            ("require_finish", self.finish_check),
            ("manage_emulator", self.emu_check),
            ("stop_on_error", self.stop_err_check),
            ("window_finish", self.win_finish_check),
        ):
            val = bool(chk.isChecked())
            if val:
                spec[field] = True
            elif field in spec:
                spec[field] = False

        uses_instance = key == "MaaEnd" or any("{instance}" in str(a) for a in (spec.get("args") or []))
        cfg_field = "instance" if uses_instance else "config_name"
        cfg_val = self.default_cfg_combo.currentText().strip()
        if cfg_val:
            spec[cfg_field] = cfg_val
        else:
            spec.pop(cfg_field, None)

        try:
            config.save_yaml(config.PROJECTS_YAML, data)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._rendered_snapshot = None
        self.reload()
        self._log("core", "info", "已保存 {0} 的项目参数".format(key))
        self.projects_changed.emit()

    def _set_op_busy(self, busy):
        self._op_busy = bool(busy)
        for btn in (self.doctor_btn, self.update_btn, self.reinstall_btn, self.delete_btn, self.save_params_btn):
            btn.setEnabled(not busy)
        if self._current_key:
            spec = self._projects().get(self._current_key) or {}
            installed = project_ops.is_project_installed(self._current_key, self._registry_getter())
            self.gui_btn.setEnabled(bool(spec.get("gui")) and installed and not busy)
            self.update_btn.setEnabled(installed and not busy)
        self._sync_install_button()

    def _append_op_line(self, text):
        if text:
            self.output_view.appendPlainText(str(text))

    def _on_op_done(self, kind, ok, summary):
        self._set_op_busy(False)
        self.output_summary.setText(summary)
        self._append_op_line("[{0}] {1}".format("完成" if ok else "失败", summary))
        self._log("core", "info" if ok else "error", summary)
        self._rendered_snapshot = None
        self.reload(force_versions=True)
        if kind in ("install", "reinstall"):
            self.projects_changed.emit()

    def _on_install_clicked(self):
        if self._op_busy:
            return
        key = str(self.install_combo.currentData() or "")
        if not key:
            return
        if project_ops.is_project_installed(key, self._registry_getter()):
            self._sync_install_button()
            return
        self._set_op_busy(True)
        self._current_key = key
        self.output_title.setText("安装项目：{0}".format(key))
        self.output_summary.setText("正在安装…")
        self.output_view.setPlainText("开始安装 {0}...\n".format(key))

        def worker():
            try:
                ok, msg = project_ops.install_project(
                    key, bus=self._bus, log_cb=self._op_line.emit
                )
            except Exception as exc:
                ok, msg = False, str(exc)
            self._op_done.emit("install", bool(ok), "{0} {1}".format(key, msg))

        threading.Thread(target=worker, daemon=True, name="afk-install-" + key).start()

    def _on_reinstall_clicked(self):
        key = self._current_key
        if not key or self._op_busy:
            return
        choice = QMessageBox.question(
            self,
            "重装项目",
            "将删除 {0} 的源码目录并重新克隆与构建。\n用户配置（configs/{0}）会完整保留，确定继续吗？".format(key),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return
        spec = copy.deepcopy(self._projects().get(key) or {})
        self._set_op_busy(True)
        self.output_title.setText("重装项目：{0}".format(key))
        self.output_summary.setText("正在重装…")
        self.output_view.setPlainText("开始重装 {0}...\n".format(key))

        def worker():
            try:
                ok, msg = project_ops.reinstall_project(
                    key, spec=spec, bus=self._bus, log_cb=self._op_line.emit
                )
            except Exception as exc:
                ok, msg = False, str(exc)
            self._op_done.emit("reinstall", bool(ok), "{0} {1}".format(key, msg))

        threading.Thread(target=worker, daemon=True, name="afk-reinstall-" + key).start()

    def _on_delete_clicked(self):
        key = self._current_key
        if not key or self._op_busy:
            return
        box = QMessageBox(self)
        box.setWindowTitle("删除项目")
        box.setIcon(QMessageBox.Warning)
        box.setText("确定删除项目「{0}」吗？".format(key))
        box.setInformativeText("用户配置（configs/{0}）不会被删除；你可以选择是否同时删除本地源码目录。".format(key))
        btn_reg = box.addButton("仅从配置中移除", QMessageBox.AcceptRole)
        btn_files = box.addButton("同时删除源码目录", QMessageBox.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(btn_cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked == btn_cancel or clicked is None:
            return
        delete_files = clicked == btn_files
        try:
            project_ops.delete_project(key, delete_files=delete_files, bus=self._bus)
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self._current_key = ""
        self._rendered_snapshot = None
        self.output_summary.setText("已删除 {0}".format(key))
        self._log(
            "core",
            "info",
            "已删除项目 {0}{1}".format(key, "（并清理源码目录）" if delete_files else ""),
        )
        self.reload()
        self.projects_changed.emit()

    def _on_update_clicked(self):
        key = self._current_key
        if not key or self._op_busy:
            return
        spec = copy.deepcopy(self._projects().get(key) or {})
        self._set_op_busy(True)
        self.output_title.setText("更新项目：{0}".format(key))
        self.output_summary.setText("正在检查并拉取更新…")
        self.output_view.setPlainText("开始更新 {0}...\n".format(key))

        def worker():
            try:
                from afk_core.events import EventBus
                from afk_core.updater import Updater

                bus = self._bus or EventBus()
                res = Updater(bus, config.settings()).update_project(key, spec)
                updated = bool((res or {}).get("updated"))
                steps = (res or {}).get("steps") or []
                for step in steps:
                    self._op_line.emit("步骤：{0}".format(step))
                msg = "已更新至最新" if updated else "已是最新版本（或无需更新）"
                ok = True
            except Exception as exc:
                ok, msg = False, "更新失败：{0}".format(exc)
            self._op_done.emit("update", ok, "{0} {1}".format(key, msg))

        threading.Thread(target=worker, daemon=True, name="afk-update-" + key).start()

    def _on_doctor_clicked(self):
        key = self._current_key
        if not key or self._op_busy:
            return
        spec = copy.deepcopy(self._projects().get(key) or {})
        self._set_op_busy(True)
        self.output_title.setText("环境检测：{0}".format(key))
        self.output_summary.setText("正在检测…")
        self.output_view.setPlainText("正在对 {0} 执行环境检测...\n".format(key))

        def worker():
            try:
                results = doctor.run_project_checks(key, spec)
            except Exception as exc:
                results = [{"status": "fail", "name": key, "detail": str(exc)}]
            self._doctor_ready.emit(key, results)

        threading.Thread(target=worker, daemon=True, name="afk-doctor-" + key).start()

    def _render_project_doctor(self, key, results):
        self._set_op_busy(False)
        lines = []
        summary = doctor.summary(results)
        for item in results:
            lines.append(
                "[{0:<4}] {1:<26} {2}".format(
                    item.get("status", "ok"),
                    item.get("name", ""),
                    item.get("detail", ""),
                )
            )
        text = "通过 {ok} · 告警 {warn} · 失败 {fail}（共 {total} 项）".format(**summary)
        self.output_title.setText("环境检测：{0}".format(key))
        self.output_summary.setText(text)
        self.output_view.setPlainText("\n".join(lines))

    # -- 后台 git 版本扫描 ----------------------------------------------------
    def _start_version_scan(self, projects, force=False):
        targets = []
        with self._scan_lock:
            for key, spec in projects.items():
                spec = spec or {}
                path = self._project_path(key, spec)
                if not path:
                    continue
                if not force and (path in self._version_cache or path in self._scan_inflight):
                    continue
                targets.append((key, path))
            if targets:
                self._scan_pending.extend(t for t in targets if t not in self._scan_pending)
                self._scan_inflight.update(path for _key, path in targets)
        if not targets:
            return
        if self._version_thread is not None and self._version_thread.is_alive():
            return
        self._version_thread = threading.Thread(
            target=self._version_worker, daemon=True, name="afk-versions"
        )
        self._version_thread.start()

    def _version_worker(self):
        while True:
            with self._scan_lock:
                if not self._scan_pending:
                    return
                key, path = self._scan_pending.pop(0)
            try:
                version = read_version(path)
            except Exception:
                version = ""
            with self._scan_lock:
                self._version_cache[path] = version
                self._scan_inflight.discard(path)
            self.version_ready.emit(key, version)

    def _apply_version(self, key, version):
        if key != self._current_key:
            return
        spec = self._projects().get(key) or {}
        text = version or str(spec.get("version") or "") or "未知"
        self.version_pill.setText("版本：" + text)
        lbl = self._meta_values.get("version")
        if lbl is not None:
            lbl.setText(text)
            lbl.setToolTip(text)


ProjectStatusPage = ProjectConfigPage
