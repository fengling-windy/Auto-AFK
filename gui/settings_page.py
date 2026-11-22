"""设置页：外观 / 隐藏项目 / 新增项目 / 静默启动 / 运行顺序 / 运行结束后操作 / 空闲自动挂机 / 开机自启 / 桌面小组件。

所有修改即时写回 settings.yaml 并通过 changed 信号通知（刷新 supervisor.settings）。
外观里的主题切换额外通过 theme_changed 通知主窗口立刻换肤。
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from afk_core import autostart, config
from gui import themes
from gui.modern import AnimatedButton, enable_smooth_scroll

POST_ACTION_LABELS = [
    ("none", "无操作"),
    ("shutdown", "关机"),
    ("exit", "关闭程序"),
]

AUTOSTART_MODES = [
    (autostart.MODE_TRAY, autostart.MODE_LABELS[autostart.MODE_TRAY]),
    (autostart.MODE_ADMIN, autostart.MODE_LABELS[autostart.MODE_ADMIN]),
]

# 项目键会变成 configs/<键> 目录名、日志名、报告名，这些字符在 Windows 上
# 直接建目录会 OSError(123)，而 YAML 本身完全允许它们。
_BAD_KEY_CHARS = '\\/:*?"<>|'


def _bad_key_chars(key):
    """返回项目键里不能出现在 Windows 文件名中的字符（去重、保序）。"""
    out = []
    for ch in key:
        if ch in _BAD_KEY_CHARS and ch not in out:
            out.append(ch)
    if key.endswith(".") or key.endswith(" "):
        out.append("首尾的点或空格")
    return out


class _SectionCard(QFrame):
    def __init__(self, title, hint="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
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
        self.body.setSpacing(6)
        outer.addLayout(self.body)


class SettingsPage(QWidget):
    changed = Signal()
    # 主题切换要立刻重刷整套 QSS + 重建卡片配色，主窗口接这个信号
    theme_changed = Signal(str)
    # 「已隐藏的项目」里点了恢复：主窗口负责把卡片放回来
    restore_requested = Signal(str)
    # 新增挂机项目成功后通知主窗口重读注册表
    project_added = Signal(str)

    def __init__(self, log_fn=None, registry=None, parent=None):
        super().__init__(parent)
        self._log = log_fn or (lambda *a, **k: None)
        self._registry_injected = registry is not None
        self._registry = registry if registry is not None else config.registry()
        self._loading = True
        # 数值框是「按住箭头连续变值」的控件：每变一个数就整份重写 settings.yaml
        # 并往实时日志写一行。实测把「结束后延迟」从 60 拖到 89 就落盘 29 次、
        # 日志面板多出 36 行，把用户真正要看的日志全冲掉了。
        # 改成停止变动 400ms 后合并落盘一次（与桌面小组件拖动同一套思路）。
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.setInterval(400)
        self._pending_timer.timeout.connect(self._flush_pending_save)
        self._pending_saves = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        enable_smooth_scroll(scroll)
        inner = QWidget()
        inner.setObjectName("central")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 2, 8, 8)
        layout.setSpacing(10)

        # -- 0. 外观（主题 / 背景图）--------------------------------------------
        self.appearance_card = _SectionCard(
            "外观",
            "主题决定整套界面配色；背景图只作用在主窗口底板上，项目卡与日志面板保持不透明，避免文字压在图上看不清。",
        )
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_cap = QLabel("主题")
        theme_cap.setObjectName("hintLabel")
        theme_row.addWidget(theme_cap)
        self.theme_combo = QComboBox()
        for name in ("dark", "light"):
            self.theme_combo.addItem(themes.THEME_LABELS.get(name, name), name)
        self.theme_combo.currentIndexChanged.connect(self._save_appearance)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch(1)
        self.appearance_card.body.addLayout(theme_row)

        bg_row = QHBoxLayout()
        bg_row.setSpacing(8)
        bg_cap = QLabel("背景图")
        bg_cap.setObjectName("hintLabel")
        bg_row.addWidget(bg_cap)
        self.bg_edit = QLineEdit()
        self.bg_edit.setPlaceholderText("留空表示不用背景图（可填相对仓库根的路径）")
        self.bg_edit.editingFinished.connect(self._save_appearance)
        bg_row.addWidget(self.bg_edit, 1)
        bg_pick = AnimatedButton("选择…", variant="mini")
        bg_pick.clicked.connect(self._pick_background)
        bg_row.addWidget(bg_pick)
        bg_clear = AnimatedButton("清除", variant="mini")
        bg_clear.clicked.connect(self._clear_background)
        bg_row.addWidget(bg_clear)
        self.appearance_card.body.addLayout(bg_row)

        op_row = QHBoxLayout()
        op_row.setSpacing(8)
        op_cap = QLabel("背景不透明度")
        op_cap.setObjectName("hintLabel")
        op_row.addWidget(op_cap)
        self.bg_opacity = QSpinBox()
        self.bg_opacity.setRange(0, 100)
        self.bg_opacity.setSuffix(" %")
        self.bg_opacity.setValue(100)
        self.bg_opacity.valueChanged.connect(
            lambda _v: self._queue_save("gui.background_opacity", self._save_appearance)
        )
        op_row.addWidget(self.bg_opacity)
        op_row.addStretch(1)
        self.appearance_card.body.addLayout(op_row)
        layout.addWidget(self.appearance_card)

        # -- 0.1 已隐藏的项目 ----------------------------------------------------
        self.hidden_card = _SectionCard(
            "已隐藏的项目",
            "在总览的卡片上点 × 会把项目收起来（只在总览里不显示，注册表里仍然保留）。这里把它们放回总览。",
        )
        self._hidden_empty_hint = QLabel("（没有被隐藏的项目）")
        self._hidden_empty_hint.setObjectName("hintLabel")
        layout.addWidget(self.hidden_card)

        # -- 0.2 新增挂机项目 ----------------------------------------------------
        self.add_card = _SectionCard(
            "新增挂机项目",
            "直接把项目写进 afk_projects.yaml 的 projects 里，字段可以随后在「项目注册表」里细调。"
            "同一个项目要跑第二份配置时，把「继承自」填成已有项目的键即可（它的参数全部继承，只需改配置文件）。",
        )
        add_grid = QHBoxLayout()
        add_grid.setSpacing(8)
        self.add_key_edit = QLineEdit()
        self.add_key_edit.setPlaceholderText("项目键（唯一，如 BAAH2）")
        add_grid.addWidget(self.add_key_edit, 1)
        self.add_name_edit = QLineEdit()
        self.add_name_edit.setPlaceholderText("显示名（可留空）")
        add_grid.addWidget(self.add_name_edit, 1)
        self.add_card.body.addLayout(add_grid)
        add_grid2 = QHBoxLayout()
        add_grid2.setSpacing(8)
        self.add_base_combo = QComboBox()
        self.add_base_combo.addItem("不继承（手填下面这些）", "")
        add_grid2.addWidget(self.add_base_combo, 1)
        self.add_path_edit = QLineEdit()
        self.add_path_edit.setPlaceholderText("项目目录（相对仓库根，如 BAAH2）")
        add_grid2.addWidget(self.add_path_edit, 1)
        self.add_config_edit = QLineEdit()
        self.add_config_edit.setPlaceholderText("配置文件（可选，如 config.json）")
        add_grid2.addWidget(self.add_config_edit, 1)
        self.add_card.body.addLayout(add_grid2)
        add_actions = QHBoxLayout()
        add_actions.setSpacing(8)
        self.add_btn = AnimatedButton("新增项目", variant="primary")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._add_project)
        add_actions.addWidget(self.add_btn)
        add_actions.addStretch(1)
        self.add_card.body.addLayout(add_actions)
        layout.addWidget(self.add_card)

        # -- 1. 启动时自动打开项目界面 -----------------------------------------
        self.silent_card = _SectionCard(
            "启动时自动打开项目界面",
            "勾选后，Auto-AFK 启动时会把对应项目的配置界面独立打开（与手动双击该项目的启动器效果相同，窗口归项目自己管辖，不嵌入本窗口）。",
        )
        self.silent_checks = {}
        self._silent_empty_hint = QLabel("（当前没有配置 gui 入口的项目）")
        self._build_silent_checks()
        layout.addWidget(self.silent_card)

        # -- 2. 运行顺序 ---------------------------------------------------------
        self.order_card = _SectionCard(
            "项目运行顺序",
            "「一键挂机」组内按此顺序启动（并行组同时运行，仅影响启动先后）；未列出的项目按注册表原顺序垫底。",
        )
        order_row = QHBoxLayout()
        order_row.setSpacing(8)
        self.order_list = QListWidget()
        self.order_list.setObjectName("orderList")
        self.order_list.setDragDropMode(QListWidget.InternalMove)
        enable_smooth_scroll(self.order_list)
        for key in self._ordered_project_keys():
            spec = self._registry["projects"][key]
            item = QListWidgetItem(str(spec.get("display_name", key)).split("（")[0])
            item.setData(Qt.UserRole, key)
            self.order_list.addItem(item)
        self._fit_order_list_height()
        self.order_list.model().rowsMoved.connect(self._save_order)
        order_row.addWidget(self.order_list, 1)
        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        up_btn = AnimatedButton("上移")
        down_btn = AnimatedButton("下移")
        up_btn.clicked.connect(lambda: self._move_selected(-1))
        down_btn.clicked.connect(lambda: self._move_selected(1))
        btn_col.addWidget(up_btn)
        btn_col.addWidget(down_btn)
        btn_col.addStretch(1)
        order_row.addLayout(btn_col)
        self.order_card.body.addLayout(order_row)
        layout.addWidget(self.order_card)

        # -- 3. 运行结束后的操作 -------------------------------------------------
        self.post_card = _SectionCard(
            "所有运行结束后的操作",
            "「一键挂机」全部成功结束并经过延迟后执行；手动停止不会触发。",
        )
        post_row = QHBoxLayout()
        post_row.setSpacing(8)
        self.post_combo = QComboBox()
        for value, label in POST_ACTION_LABELS:
            self.post_combo.addItem(label, value)
        post_cfg = config.settings().get("post_actions") or {}
        idx = self.post_combo.findData(str(post_cfg.get("after_all", "none")))
        self.post_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.post_combo.currentIndexChanged.connect(self._save_post_action)
        self.post_delay = QSpinBox()
        self.post_delay.setRange(0, 86400)
        self.post_delay.setSuffix(" 秒后执行")
        self.post_delay.setValue(int(post_cfg.get("delay_sec", 60)))
        self.post_delay.valueChanged.connect(lambda _v: self._queue_save("post_delay", self._save_post_action))
        post_row.addWidget(QLabel("操作:"))
        post_row.addWidget(self.post_combo)
        post_row.addSpacing(12)
        post_row.addWidget(self.post_delay)
        post_row.addStretch(1)
        self.post_card.body.addLayout(post_row)
        layout.addWidget(self.post_card)

        # -- 4. 空闲自动挂机 -----------------------------------------------------
        self.idle_card = _SectionCard(
            "空闲自动挂机",
            "电脑连续无鼠标/键盘输入达到设定分钟数后，弹出提示框（停止 / 立刻开始 / 仅运行不占用鼠标任务）；"
            "倒计时结束仍未选择则自动运行全部任务。触发一次后解除监听，用户回来再离开会重新监听。",
        )
        idle_cfg = config.settings().get("idle") or {}
        idle_row = QHBoxLayout()
        idle_row.setSpacing(8)
        self.idle_check = QCheckBox("启用")
        self.idle_check.setChecked(bool(idle_cfg.get("enabled", False)))
        self.idle_check.toggled.connect(self._save_idle)
        self.idle_minutes = QSpinBox()
        self.idle_minutes.setRange(1, 1440)
        self.idle_minutes.setSuffix(" 分钟无操作后提示")
        self.idle_minutes.setValue(int(idle_cfg.get("minutes", 30) or 30))
        self.idle_minutes.valueChanged.connect(lambda _v: self._queue_save("idle", self._save_idle))
        self.idle_prompt = QSpinBox()
        self.idle_prompt.setRange(5, 600)
        self.idle_prompt.setSuffix(" 秒后自动执行")
        self.idle_prompt.setValue(int(idle_cfg.get("prompt_sec", 30) or 30))
        self.idle_prompt.valueChanged.connect(lambda _v: self._queue_save("idle", self._save_idle))
        idle_row.addWidget(self.idle_check)
        idle_row.addWidget(self.idle_minutes)
        idle_row.addWidget(self.idle_prompt)
        idle_row.addStretch(1)
        self.idle_card.body.addLayout(idle_row)

        # 今日已正常完成就不重复挂机
        self.idle_skip_check = QCheckBox("今天已正常完成则不再自动执行")
        self.idle_skip_check.setChecked(bool(idle_cfg.get("skip_if_completed_today", True)))
        self.idle_skip_check.toggled.connect(self._save_idle)
        self.idle_card.body.addWidget(self.idle_skip_check)

        self.idle_hint = QLabel("")
        self.idle_hint.setObjectName("hintLabel")
        self.idle_hint.setWordWrap(True)
        self.idle_card.body.addWidget(self.idle_hint)

        # 「今天已完成」状态 + 手动恢复自动挂机
        done_row = QHBoxLayout()
        done_row.setSpacing(8)
        self.completion_label = QLabel("")
        self.completion_label.setObjectName("hintLabel")
        self.clear_completion_btn = AnimatedButton("今天还没完成（恢复自动挂机）", variant="mini")
        self.clear_completion_btn.clicked.connect(self._clear_completion)
        done_row.addWidget(self.completion_label, 1)
        done_row.addWidget(self.clear_completion_btn)
        self.idle_card.body.addLayout(done_row)
        layout.addWidget(self.idle_card)

        # -- 5. 开机自启 ---------------------------------------------------------
        self.autostart_card = _SectionCard(
            "开机启动",
            "写当前用户注册表 Run 键（HKCU），不需要管理员权限；关闭开关即删除该项。",
        )
        auto_row = QHBoxLayout()
        auto_row.setSpacing(8)
        self.autostart_check = QCheckBox("开机启动")
        self.autostart_check.setChecked(bool(autostart.status().get("enabled")))
        self.autostart_check.toggled.connect(self._save_autostart)
        self.autostart_mode = QComboBox()
        for value, label in AUTOSTART_MODES:
            self.autostart_mode.addItem(label, value)
        mode = str(autostart.status().get("mode") or autostart.MODE_TRAY)
        mode_idx = self.autostart_mode.findData(mode)
        self.autostart_mode.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
        self.autostart_mode.currentIndexChanged.connect(self._save_autostart)
        auto_row.addWidget(self.autostart_check)
        auto_row.addWidget(self.autostart_mode)
        auto_row.addStretch(1)
        self.autostart_card.body.addLayout(auto_row)
        self.autostart_hint = QLabel("")
        self.autostart_hint.setObjectName("hintLabel")
        self.autostart_hint.setWordWrap(True)
        self.autostart_card.body.addWidget(self.autostart_hint)
        layout.addWidget(self.autostart_card)

        # -- 6. 桌面小组件 -------------------------------------------------------
        self.widget_card = _SectionCard(
            "桌面小组件",
            "屏幕上的常驻小面板：显示运行状态与空闲倒计时，提供「一键挂机 / 全部停止 / 显示主窗口」，可拖动与折叠。",
        )
        self.widget_check = QCheckBox("显示桌面小组件")
        self.widget_check.setChecked(
            bool((config.settings().get("gui") or {}).get("widget_enabled", False))
        )
        self.widget_check.toggled.connect(self._save_widget)
        self.widget_card.body.addWidget(self.widget_check)
        layout.addWidget(self.widget_card)

        layout.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self._refresh_idle_hint()
        self._refresh_autostart_hint()
        self._refresh_completion_label()
        self._build_hidden_checks()
        self._build_base_combo()
        self._load_appearance()
        self._loading = False

    # -- 外观 ------------------------------------------------------------------
    def _load_appearance(self):
        """把 gui.theme / gui.background_image / gui.background_opacity 读进控件。"""
        gui_cfg = config.settings().get("gui") or {}
        theme = themes.normalize_theme(gui_cfg.get("theme"))
        idx = self.theme_combo.findData(theme)
        if idx < 0:
            idx = self.theme_combo.findData(themes.DEFAULT_THEME)
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(max(0, idx))
        self.theme_combo.blockSignals(False)
        self.bg_edit.setText(str(gui_cfg.get("background_image") or ""))
        try:
            opacity = int(gui_cfg.get("background_opacity", 100))
        except (TypeError, ValueError):
            opacity = 100
        self.bg_opacity.blockSignals(True)
        self.bg_opacity.setValue(max(0, min(100, opacity)))
        self.bg_opacity.blockSignals(False)

    def _save_appearance(self, *_):
        if self._loading:
            return
        theme = str(self.theme_combo.currentData() or themes.DEFAULT_THEME)
        image = self.bg_edit.text().strip()
        opacity = int(self.bg_opacity.value())

        def mutate(settings):
            gui_cfg = settings.setdefault("gui", {})
            if not isinstance(gui_cfg, dict):
                gui_cfg = {}
                settings["gui"] = gui_cfg
            gui_cfg["theme"] = theme
            gui_cfg["background_image"] = image
            gui_cfg["background_opacity"] = opacity

        self._mutate_settings(mutate)
        self.theme_changed.emit(theme)
        self.changed.emit()
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
        if not path:
            return
        self.bg_edit.setText(path)
        self._save_appearance()

    def _clear_background(self):
        self.bg_edit.setText("")
        self._save_appearance()

    # -- 已隐藏的项目 ----------------------------------------------------------
    def _hidden_project_keys(self):
        projects = config.registry().get("projects", {}) or {}
        keys = []
        for key in ((config.settings().get("gui") or {}).get("hidden_projects") or []):
            if str(key) in projects:
                keys.append(str(key))
        return keys

    def _build_hidden_checks(self):
        """按当前隐藏列表重建「恢复」行（注册表里已删掉的项目不再列）。"""
        projects = config.registry().get("projects", {}) or {}
        body = self.hidden_card.body
        for row in getattr(self, "_hidden_rows", []):
            row.setParent(None)
            row.deleteLater()
        self._hidden_rows = []
        body.removeWidget(self._hidden_empty_hint)
        keys = self._hidden_project_keys()
        for key in keys:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            spec = projects.get(key) or {}
            label = QLabel(str(spec.get("display_name", key)).split("（")[0])
            label.setObjectName("cardDetail")
            row.addWidget(label, 1)
            btn = AnimatedButton("放回总览", variant="mini")
            btn.clicked.connect(lambda _=False, k=key: self.restore_requested.emit(k))
            row.addWidget(btn)
            self._hidden_rows.append(row_widget)
            body.addWidget(row_widget)
        if not keys:
            self._hidden_empty_hint.setText("（没有被隐藏的项目）")
        self._hidden_empty_hint.setVisible(not keys)
        body.addWidget(self._hidden_empty_hint)

    # -- 新增项目 --------------------------------------------------------------
    def _build_base_combo(self):
        """「继承自」下拉：列出当前注册表里已有的项目键。"""
        current = self.add_base_combo.currentData() or ""
        projects = config.registry().get("projects", {}) or {}
        self.add_base_combo.blockSignals(True)
        self.add_base_combo.clear()
        self.add_base_combo.addItem("不继承（手填下面这些）", "")
        for key, spec in projects.items():
            name = str((spec or {}).get("display_name", key)).split("（")[0]
            self.add_base_combo.addItem("{0}（{1}）".format(name, key), key)
        idx = self.add_base_combo.findData(current)
        self.add_base_combo.setCurrentIndex(max(0, idx))
        self.add_base_combo.blockSignals(False)

    def _add_project(self):
        key = self.add_key_edit.text().strip()
        if not key:
            self._log("core", "warn", "新增项目失败：项目键不能为空")
            return
        bad = _bad_key_chars(key)
        if bad:
            # 项目键不只是 YAML 里的一个名字：它还是 configs/<键> 目录名、
            # 日志文件名、报告文件名。带 Windows 保留字符（\ / : * ? " < > |）
            # 或首尾的点/空格时，建目录直接抛 OSError(123)，链跑起来在这一步
            # 就崩，而错误信息只有一个 WinError，看不出是项目键的问题。
            self._log(
                "core", "warn",
                "新增项目失败：项目键 {0} 含有不能用在文件名里的字符 {1}".format(
                    key, " ".join(bad)
                ),
            )
            return
        base = str(self.add_base_combo.currentData() or "")
        name = self.add_name_edit.text().strip()
        path = self.add_path_edit.text().strip()
        config_name = self.add_config_edit.text().strip()
        if not base and not path:
            self._log("core", "warn", "新增项目失败：不继承已有项目时必须填目录")
            return
        try:
            data = config.load_yaml(config.PROJECTS_YAML, {}) or {}
        except Exception as exc:
            self._log("core", "error", "读取项目注册表失败: {0}".format(exc))
            return
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            self._log("core", "error", "项目注册表格式不对：projects 不是映射")
            return
        if key in projects:
            self._log("core", "warn", "新增项目失败：{0} 已存在".format(key))
            return
        spec = {}
        if base:
            spec["base"] = base
        if path:
            spec["path"] = path
        if config_name:
            spec["config_name"] = config_name
        if name:
            spec["display_name"] = name
        if not base:
            # 不继承时必须自己给足启动所需的最小字段，否则 supervisor 起来也跑不动。
            spec.setdefault("launch_type", "python")
            spec.setdefault("python", ".venv\\Scripts\\python.exe")
        projects[key] = spec
        try:
            config.save_yaml(config.PROJECTS_YAML, data)
        except Exception as exc:
            self._log("core", "error", "写入项目注册表失败: {0}".format(exc))
            return
        self.add_key_edit.clear()
        self.add_name_edit.clear()
        self.add_path_edit.clear()
        self.add_config_edit.clear()
        self._log("core", "info", "已新增项目 {0}（可在「项目注册表」里继续细调）".format(key))
        self.project_added.emit(key)

    # -- 数据 ------------------------------------------------------------------
    def _projects_with_gui(self):
        projects = self._registry.get("projects", {}) or {}
        return {k: s for k, s in projects.items() if s.get("gui")}

    def _build_silent_checks(self):
        """按当前注册表重建「启动时自动打开项目界面」的勾选项。

        历史缺陷：这几个复选框只在 __init__ 里按当时的注册表建一次。用户在
        「项目注册表」tab 新增一个带界面的项目并保存后，主窗口的项目卡都跟着
        更新了，唯独这里没有它的勾选框——用户永远无法让新项目开机自动打开，
        也看不出少了一项。删掉的项目反之会留下一个勾选框，勾上后保存进
        silent_open_projects，之后每次启动都去拉起一个不存在的项目。
        """
        silent_set = {
            str(k)
            for k in ((config.settings().get("gui") or {}).get("silent_open_projects") or [])
        }
        # 注册表同样要重读（除非构造时被注入假注册表，那是测试/预览的用法）。
        if not self._registry_injected:
            self._registry = config.registry()
        body = self.silent_card.body
        for check in self.silent_checks.values():
            body.removeWidget(check)
            check.setParent(None)
            check.deleteLater()
        self.silent_checks = {}
        body.removeWidget(self._silent_empty_hint)
        for key, spec in self._projects_with_gui().items():
            check = QCheckBox(str(spec.get("display_name", key)).split("（")[0])
            check.setChecked(key in silent_set)
            check.toggled.connect(lambda checked, k=key: self._toggle_silent(k, checked))
            self.silent_checks[key] = check
            body.addWidget(check)
        self._silent_empty_hint.setVisible(not self.silent_checks)
        body.addWidget(self._silent_empty_hint)

    def _ordered_project_keys(self):
        settings = config.settings()
        order = [str(k) for k in ((settings.get("chain") or {}).get("order") or [])]
        keys = [k for k in order if k in (self._registry.get("projects", {}) or {})]
        for key in (self._registry.get("projects", {}) or {}):
            if key not in keys:
                keys.append(key)
        return keys

    # -- 保存 ------------------------------------------------------------------
    def _mutate_settings(self, mutate):
        settings = config.settings()
        mutate(settings)
        config.save_yaml(config.SETTINGS_YAML, settings)
        self.changed.emit()
        return settings

    def _queue_save(self, key, writer):
        """把「变一个值就写一次盘」的改动合并成一次落盘。

        数值框与拖动排序都是连续动作：每动一格就走一遍「读 yaml → 改 → 写回 →
        写日志」。实测「结束后延迟」从 60 拖到 89 落盘 29 次、日志面板多出 36 行，
        把用户真正要看的日志刷没了。这里只在停止改动 400ms 后执行最后一次。
        """
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
        """定时器到期：把攒下的改动一次性写回 settings.yaml。"""
        pending = self._pending_saves
        self._pending_saves = []
        for _key, writer in pending:
            try:
                writer()
            except Exception as exc:  # 写盘失败不能让设置页崩掉
                self._log("core", "error", "保存设置失败: {0}".format(exc))

    def flush_pending(self):
        """立刻结束防抖窗口并写盘（切页 / 退出前必须调用，否则改动会丢）。"""
        if self._pending_saves:
            self._pending_timer.stop()
            self._flush_pending_save()

    def _toggle_silent(self, key, checked):
        if self._loading:
            return

        def mutate(settings):
            gui_cfg = settings.setdefault("gui", {})
            current = [str(k) for k in (gui_cfg.get("silent_open_projects") or [])]
            if checked and key not in current:
                current.append(key)
            elif not checked and key in current:
                current.remove(key)
            gui_cfg["silent_open_projects"] = current

        self._mutate_settings(mutate)
        self._log("core", "info", "启动时自动打开界面 {0}: {1}".format(key, "开" if checked else "关"))

    def _save_order(self, *_):
        if self._loading:
            return
        keys = []
        for row in range(self.order_list.count()):
            key = self.order_list.item(row).data(Qt.UserRole)
            if key:
                keys.append(str(key))

        def mutate(settings):
            settings.setdefault("chain", {})["order"] = keys

        self._mutate_settings(mutate)
        self._log("core", "info", "运行顺序已保存: {0}".format(" → ".join(keys)))

    def _move_selected(self, delta):
        row = self.order_list.currentRow()
        if row < 0:
            return
        target = row + delta
        if not (0 <= target < self.order_list.count()):
            return
        item = self.order_list.takeItem(row)
        self.order_list.insertItem(target, item)
        self.order_list.setCurrentRow(target)
        self._save_order()

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

    def _save_idle(self, *_):
        if self._loading:
            return
        enabled = bool(self.idle_check.isChecked())
        minutes = int(self.idle_minutes.value())
        prompt_sec = int(self.idle_prompt.value())
        skip_done = bool(self.idle_skip_check.isChecked())

        def mutate(settings):
            idle_cfg = settings.setdefault("idle", {})
            idle_cfg["enabled"] = enabled
            idle_cfg["minutes"] = minutes
            idle_cfg["prompt_sec"] = prompt_sec
            idle_cfg["skip_if_completed_today"] = skip_done

        self._mutate_settings(mutate)
        self._refresh_idle_hint()
        self._log(
            "core",
            "info",
            "空闲自动挂机 {0}：{1} 分钟无操作，提示 {2} 秒，今日完成跳过={3}".format(
                "开" if enabled else "关", minutes, prompt_sec, "开" if skip_done else "关"
            ),
        )

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
                "core",
                "info",
                "开机启动 {0}（{1}）".format(
                    "已启用" if enabled else "已关闭", autostart.MODE_LABELS.get(mode, mode)
                ),
            )
        def mutate(settings):
            idle_cfg = settings.setdefault("idle", {})
            idle_cfg["autostart"] = enabled
            idle_cfg["autostart_mode"] = mode

        self._mutate_settings(mutate)
        self._refresh_autostart_hint()

    def _save_widget(self, checked):
        if self._loading:
            return

        def mutate(settings):
            settings.setdefault("gui", {})["widget_enabled"] = bool(checked)

        self._mutate_settings(mutate)
        self._log("core", "info", "桌面小组件 {0}".format("开" if checked else "关"))

    def _refresh_idle_hint(self):
        try:
            from afk_core import idle as idle_mod

            support = "支持" if idle_mod.supported() else "当前系统不支持空闲检测"
            self.idle_hint.setText(
                "空闲检测：{0}；阈值最小 60 秒。保存后立即生效，无需重启。".format(support)
            )
        except Exception as exc:
            self.idle_hint.setText("空闲检测不可用: {0}".format(exc))

    def _refresh_autostart_hint(self):
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
        """重新读取 settings.yaml 刷新界面（外部改配置后调用）。"""
        # 先把还没落盘的防抖改动写掉，否则 reload 会把界面上刚改的值覆盖回旧值。
        self.flush_pending()
        self._loading = True
        try:
            settings = config.settings()
            # 「启动时自动打开」勾选项要跟着注册表重建（外部新增/删除带界面的
            # 项目后必须反映到这几行，见 _build_silent_checks 的历史缺陷说明）。
            self._build_silent_checks()
            # 运行顺序也要跟着刷新：之前漏了这一项，外部改了 chain.order
            # （或直接在「设置」tab 里改了 yaml 保存）再回来，列表还是旧顺序，
            # 用户以为没生效。重建而不是原地重排，避免丢掉新增的项目。
            self._reload_order_list()
            post_cfg = settings.get("post_actions") or {}
            idx = self.post_combo.findData(str(post_cfg.get("after_all", "none")))
            self.post_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.post_delay.setValue(int(post_cfg.get("delay_sec", 60)))
            idle_cfg = settings.get("idle") or {}
            self.idle_check.setChecked(bool(idle_cfg.get("enabled", False)))
            self.idle_minutes.setValue(int(idle_cfg.get("minutes", 30) or 30))
            self.idle_prompt.setValue(int(idle_cfg.get("prompt_sec", 30) or 30))
            self.idle_skip_check.setChecked(bool(idle_cfg.get("skip_if_completed_today", True)))
            mode = str(autostart.status().get("mode") or autostart.MODE_TRAY)
            mode_idx = self.autostart_mode.findData(mode)
            self.autostart_mode.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
            self.autostart_check.setChecked(bool(autostart.status().get("enabled")))
            self.widget_check.setChecked(bool((settings.get("gui") or {}).get("widget_enabled", False)))
            self._refresh_idle_hint()
            self._refresh_autostart_hint()
            self._refresh_completion_label()
            self._load_appearance()
            self._build_hidden_checks()
            self._build_base_combo()
        finally:
            self._loading = False

    def _fit_order_list_height(self):
        """让列表高度正好放下所有行。

        历史缺陷：高度写死 168px，而 QSS 把每行撑到 44px（padding 7px + 字体），
        5 个项目只能完整显示 3 行，第 4 行被切在中间——看起来像界面画坏了，
        用户还会以为「绝区零一条龙」后面没有别的内容。这里按行数算高度，
        项目变多时列表跟着长（页面本来就有滚动区）。
        """
        count = self.order_list.count()
        if count <= 0:
            return
        row = self.order_list.sizeHintForRow(0)
        if row <= 0:
            # QSS 还没生效时 sizeHintForRow 会返回 -1，先按经验值算，稍后再校准。
            row = 44
        frame = self.order_list.frameWidth() * 2
        # QSS 给 QListWidget 设了 padding: 4px，视口比控件小 8px。
        padding = 8
        self.order_list.setFixedHeight(count * row + frame + padding)

    def _reload_order_list(self):
        # 只在注册表由外面注入时不动它（测试/预览构造用假注册表），
        # 正常运行时重读一次，让运行中新增的项目也能出现在列表里。
        if not self._registry_injected:
            self._registry = config.registry()
        self.order_list.clear()
        for key in self._ordered_project_keys():
            spec = (self._registry.get("projects", {}) or {}).get(key) or {}
            item = QListWidgetItem(str(spec.get("display_name", key)).split("（")[0])
            item.setData(Qt.UserRole, key)
            self.order_list.addItem(item)
        self._fit_order_list_height()
