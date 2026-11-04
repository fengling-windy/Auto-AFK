"""设置页：静默启动 / 运行顺序 / 运行结束后操作 / 空闲自动挂机 / 开机自启 / 桌面小组件。

所有修改即时写回 settings.yaml 并通过 changed 信号通知（刷新 supervisor.settings）。
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from afk_core import autostart, config
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

        # -- 1. 启动时静默打开项目界面 -----------------------------------------
        self.silent_card = _SectionCard(
            "启动时后台打开项目界面",
            "勾选后，Auto-AFK 启动时会自动拉起对应项目的配置界面并隐藏到后台；点击左侧导航进入该页时再嵌入显示。",
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
        self._loading = False

    # -- 数据 ------------------------------------------------------------------
    def _projects_with_gui(self):
        projects = self._registry.get("projects", {}) or {}
        return {k: s for k, s in projects.items() if s.get("gui")}

    def _build_silent_checks(self):
        """按当前注册表重建「启动时后台打开项目界面」的勾选项。

        历史缺陷：这几个复选框只在 __init__ 里按当时的注册表建一次。用户在
        「项目注册表」tab 新增一个带界面的项目并保存后，主窗口的项目卡、左侧
        导航、嵌入页都跟着更新了，唯独这里没有它的勾选框——用户永远无法让新
        项目开机静默启动，也看不出少了一项。删掉的项目反之会留下一个勾选框，
        勾上后保存进 silent_open_projects，之后每次启动都去拉起一个不存在的项目。
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
        self._log("core", "info", "静默启动界面 {0}: {1}".format(key, "开" if checked else "关"))

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
            # 静默启动勾选项要跟着注册表重建（外部新增/删除带界面的项目后必须
            # 反映到这几行，见 _build_silent_checks 的历史缺陷说明）。
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
