"""项目 / 任务卡片（运行顺序页用；总览页的舞台不再放卡片）。

从 gui/main.py 拆出来，免得 run_order_page 反过来 import main 造成循环依赖。
"""

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
)

from gui import icons, themes
from gui.modern import (
    AnimatedButton,
    ElidedLabel,
    HoverGlowCard,
    StatusDot,
    current_theme,
)

STATUS_TEXT = {
    "success": "完成",
    "success_unverified": "完成(未校验)",
    "running": "运行中",
    "pending": "等待",
    "failed": "失败",
    "hang": "卡死",
    "stopped": "已停止",
}

# 还没收尾的状态：卡片上「启动」置灰、「停止」可点。hang（卡死）也算在跑，
# 否则卡住时反而点不到「停止」。
BUSY_STATUSES = ("pending", "running", "hang")

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


def _theme_status_colors(theme=None):
    """当前主题下的状态色表（卡片状态点、状态文字）。

    历史缺陷：STATUS_COLORS 是一份写死的深色配色，状态文字直接
    `setStyleSheet("color: ...")`。切到浅色主题后 #34d399 / #60a5fa 落在白卡上
    对比度只有 1.9 / 2.5，状态几乎看不见（7 个状态全部低于 WCAG AA 的 4.5）。
    这里按主题取表，深浅两套都由 gui/themes.py 给。
    """
    name = themes.normalize_theme(theme or current_theme())
    return themes.STATUS_PALETTE.get(name, themes.STATUS_PALETTE[themes.DEFAULT_THEME])


# apply_spec 的哨兵值：区分「没传 on_gui」和「显式传 None（这个项目没有界面入口）」
_UNSET = object()

CARDS_MIN_HEIGHT = 120


class ProjectCard(HoverGlowCard):
    # 按住卡片移动超过这么多像素才算拖拽（避免点按钮/微抖动误触发重排）
    DRAG_THRESHOLD = 6

    # 卡片被拖动、移动、松手；参数是该卡的 key / 全局坐标
    drag_started = Signal(str)
    drag_moved = Signal(str, QPoint)
    drag_finished = Signal(str)

    def __init__(
        self,
        key,
        spec,
        color,
        on_start,
        on_stop,
        on_logs,
        on_gui=None,
        on_parallel=None,
        on_remove=None,
        config_options=None,
        current_config="",
        on_config_change=None,
        base_project=None,
    ):
        super().__init__()
        self.key = key
        self.base_project = base_project or key
        self.spec = spec
        self.color = color
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_logs = on_logs
        self.on_gui = on_gui
        self.on_parallel = on_parallel
        self.on_remove = on_remove
        self.on_config_change = on_config_change
        self._updating_config = False
        self._parallel = False
        self._parallel_pending = False
        self._press_pos = None
        self._dragging = False
        self._state_sig = None
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(7)

        accent = QFrame()
        accent.setFixedHeight(3)
        accent.setStyleSheet("background: {0}; border-radius: 1.5px;".format(color))
        layout.addWidget(accent)
        self.accent = accent

        head = QHBoxLayout()
        head.setSpacing(8)
        self.dot = StatusDot(_theme_status_colors()["pending"])
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
        self.instance_pill = None
        if self.key != self.base_project:
            suffix = "#" + self.key.split("#", 1)[1] if "#" in self.key else self.key
            pill = QLabel(suffix)
            pill.setObjectName("configPill")
            pill.setToolTip("同一项目的第 {0} 个配置任务（任务 ID: {1}）".format(suffix.lstrip("#"), self.key))
            head.addWidget(pill)
            self.instance_pill = pill
        # 拖动把手仅作视觉提示；整张卡片空白或非交互子控件均可直接拖起
        self.setCursor(Qt.OpenHandCursor)
        self.drag_handle = QLabel()
        self.drag_handle.setObjectName("cardGrip")
        self.drag_handle.setPixmap(
            icons.make("grip", themes.icon_color(current_theme()), 14).pixmap(14, 14)
        )
        self.drag_handle.setToolTip("按住卡片任意区域拖动可调整运行顺序")
        head.addWidget(self.drag_handle)
        # 删除任务卡
        self.close_btn = AnimatedButton("", variant="mini")
        self.close_btn.setObjectName("cardClose")
        self.close_btn.setIcon(icons.make("close", themes.icon_color(current_theme(), subtle=True), 12))
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setToolTip("从运行顺序中移除此任务")
        self.close_btn.clicked.connect(lambda: self.on_remove(self.key) if self.on_remove else None)
        head.addWidget(self.close_btn)
        layout.addLayout(head)

        # 状态与尝试次数并排同行（Linear 式紧凑元数据行：避免空尝试行白占 22px 高度）
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(8)
        self.status_label = QLabel("等待")
        self.status_label.setObjectName("cardStatus")
        # 记住当前画出来的状态文字/颜色，update_state 里据此跳过无变化的写入
        self._status_text = "等待"
        self._status_color = _theme_status_colors()["pending"]
        self.status_label.setStyleSheet("color: {0};".format(self._status_color))
        self.attempt_label = QLabel("")
        self.attempt_label.setObjectName("cardAttempt")
        meta_row.addWidget(self.status_label)
        meta_row.addStretch(1)
        meta_row.addWidget(self.attempt_label)
        layout.addLayout(meta_row)

        self.detail_label = QLabel("未运行")
        self.detail_label.setObjectName("cardDetail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumHeight(28)
        layout.addWidget(self.detail_label)

        # 配置文件 / 实例选择行
        cfg_row = QHBoxLayout()
        cfg_row.setContentsMargins(0, 0, 0, 0)
        cfg_row.setSpacing(6)
        cfg_cap = QLabel("配置")
        cfg_cap.setObjectName("hintLabel")
        self.config_combo = QComboBox()
        self.config_combo.setToolTip("指定本次任务运行使用的配置文件/实例")
        self.config_combo.currentIndexChanged.connect(self._on_config_selected)
        cfg_row.addWidget(cfg_cap)
        cfg_row.addWidget(self.config_combo, 1)
        layout.addLayout(cfg_row)
        self.set_config_options(config_options or [], current_config)

        buttons = QHBoxLayout()
        buttons.setSpacing(5)
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
        buttons.addStretch(1)
        buttons.addWidget(self.parallel_btn)
        layout.addLayout(buttons)
        self._apply_enabled()

    # -- 启用状态 -----------------------------------------------------------
    @staticmethod
    def _set_text_if_changed(label, text):
        """文本没变就不写控件（setText 会触发重排，300ms 轮询下纯属浪费）。"""
        if label.text() != text:
            label.setText(text)

    @staticmethod
    def _set_tooltip_if_changed(widget, tip):
        if widget.toolTip() != tip:
            widget.setToolTip(tip)

    @staticmethod
    def _set_enabled_if_changed(widget, enabled):
        enabled = bool(enabled)
        if widget.isEnabled() != enabled:
            widget.setEnabled(enabled)

    def _set_status_text(self, text, color):
        """写状态文字与颜色，并记住上次画的内容（没变就不碰控件）。

        _poll 每 300ms 对每张卡调一次 update_state，而 setText/setStyleSheet
        都会让 Qt 重算样式（实测 5 张卡空转 20 轮打了 100 次 setStyleSheet、
        3.0ms/轮）。缓存必须和控件内容一起改：`_apply_enabled` 也会直接写这
        个标签（写「已禁用」），漏同步缓存的话，恢复启用后 update_state 会
        以为「还是等待」而跳过写入，卡片永远停在「已禁用」（冒烟实测）。
        """
        if text == self._status_text and color == self._status_color:
            return
        self._status_text = text
        self._status_color = color
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: {0};".format(color))

    def _apply_enabled(self, busy=None):
        """enabled: false 时把卡片标灰、禁用启动按钮并说明原因。

        busy 传 None 表示不清楚当前有没有在跑，沿用上一次算出来的值。
        """
        enabled = bool((self.spec or {}).get("enabled", True))
        self._enabled = enabled
        self._state_sig = None
        if enabled:
            tip = ""
            if self._status_text == "已禁用":
                pending_color = _theme_status_colors()["pending"]
                self._set_status_text("等待", pending_color)
                self.dot.set_state(pending_color, pulse=False)
                self._set_text_if_changed(self.detail_label, "未运行")
        else:
            tip = "注册表里这个项目是 enabled: false，跑链时会跳过；要启用请到「项目配置」改成 true"
            self._set_status_text("已禁用", _theme_status_colors()["pending"])
            self.dot.set_state(_theme_status_colors()["pending"], pulse=False)
            self._set_text_if_changed(self.detail_label, "注册表里 enabled: false，链会跳过它")
            self._set_text_if_changed(self.attempt_label, "")
        self._set_tooltip_if_changed(self.start_btn, tip)
        self._set_enabled_if_changed(self.parallel_btn, enabled)
        self._set_tooltip_if_changed(
            self.parallel_btn,
            tip or "与相邻的项目同时运行：两张相邻的卡各点一次",
        )
        # 禁用也要留着「停止」：用户取消勾选时，之前启动的进程不会自己退出。
        self._apply_buttons(busy=busy)

    def is_enabled(self):
        return bool(getattr(self, "_enabled", True))

    # -- 启动 / 停止按钮 -----------------------------------------------------
    def _apply_buttons(self, busy=None):
        """按「这个项目现在有没有在跑」调整启动/停止两个按钮（仅状态变化时写控件）。"""
        if busy is None:
            busy = bool(getattr(self, "_busy", False))
        self._busy = bool(busy)
        enabled = self.is_enabled()
        self._set_enabled_if_changed(self.start_btn, enabled and not busy)
        self._set_enabled_if_changed(self.stop_btn, busy)
        if not enabled:
            self._set_tooltip_if_changed(
                self.stop_btn,
                "停止这个项目（会连同它的子进程一起结束）" if busy else "",
            )
        elif busy:
            self._set_tooltip_if_changed(
                self.start_btn, "这个项目已经在运行；要重跑请先「停止」"
            )
            self._set_tooltip_if_changed(
                self.stop_btn, "停止这个项目（会连同它的子进程一起结束）"
            )
        else:
            self._set_tooltip_if_changed(self.start_btn, "")
            self._set_tooltip_if_changed(
                self.stop_btn, "这个项目没在运行，没有可停止的东西"
            )

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

    # -- 整卡拖动排序 ---------------------------------------------------------
    def _is_interactive_child(self, widget):
        cur = widget
        while cur is not None and cur is not self:
            if isinstance(cur, (QAbstractButton, QComboBox)):
                return True
            cur = cur.parentWidget()
        return False

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and not self._is_interactive_child(self.childAt(ev.position().toPoint())):
            self._press_pos = ev.globalPosition().toPoint()
            self._dragging = False
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._press_pos is not None:
            pos = ev.globalPosition().toPoint()
            if not self._dragging:
                if (pos - self._press_pos).manhattanLength() >= self.DRAG_THRESHOLD:
                    self._dragging = True
                    self.setCursor(Qt.ClosedHandCursor)
                    self.drag_started.emit(self.key)
            if self._dragging:
                self.drag_moved.emit(self.key, pos)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._press_pos is not None and ev.button() == Qt.LeftButton:
            was_dragging = self._dragging
            self._press_pos = None
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            if was_dragging:
                self.drag_finished.emit(self.key)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def hideEvent(self, ev):
        if self._dragging:
            self._press_pos = None
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            self.drag_finished.emit(self.key)
        super().hideEvent(ev)

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
            self.accent.setStyleSheet("background: {0}; border-radius: 1.5px;".format(color))
        # enabled 也可能在注册表里被改（false → true），刷新时要跟着恢复/置灰
        self._apply_enabled()

    def refresh_theme(self, color, run=None):
        """就地换肤：不销毁重建 C++ 控件，直接刷新色条、位图图标、按钮变体与状态色。"""
        theme = current_theme()
        if color:
            self.color = color
            self.accent.setStyleSheet("background: {0}; border-radius: 1.5px;".format(color))
        self.drag_handle.setPixmap(
            icons.make("grip", themes.icon_color(theme), 14).pixmap(14, 14)
        )
        self.close_btn.setIcon(
            icons.make("close", themes.icon_color(theme, subtle=True), 12)
        )
        for btn in (self.close_btn, self.gui_btn, self.start_btn, self.stop_btn, self.logs_btn, self.parallel_btn):
            btn.set_variant(btn.variant_name)
        self._state_sig = None
        self.update_state(run)
        self.update()

    def update_state(self, run):
        status = run.get("status", "pending") if run else "pending"
        enabled = self.is_enabled()
        theme = current_theme()
        if run is None:
            sig = (theme, enabled, None)
        else:
            sig = (
                theme,
                enabled,
                status,
                run.get("pid"),
                run.get("exit_code"),
                run.get("last_message"),
                run.get("attempt", 1),
            )
        if sig == self._state_sig:
            return
        self._state_sig = sig

        if not enabled and (run is None or status not in ("running", "hang")):
            self._apply_enabled(busy=bool(run) and status in ("running", "hang"))
            self._state_sig = sig
            return
        self._apply_buttons(busy=bool(run) and status in BUSY_STATUSES)
        palette = _theme_status_colors()
        color = palette.get(status, palette["pending"])
        period = 1.1 if status == "hang" else 1.6
        self.dot.set_state(color, pulse=status in ("running", "hang"), period=period)
        self._set_status_text(STATUS_TEXT.get(status, status), color)
        if run is None:
            self._set_text_if_changed(self.detail_label, "未运行")
            self._set_text_if_changed(self.attempt_label, "")
            return
        parts = []
        if run.get("pid"):
            parts.append("pid={0}".format(run["pid"]))
        if run.get("exit_code") is not None:
            parts.append("exit={0}".format(run["exit_code"]))
        if run.get("last_message"):
            parts.append(run["last_message"][:110])
        detail = " | ".join(parts) if parts else STATUS_DETAIL_FALLBACK.get(status, "")
        self._set_text_if_changed(self.detail_label, detail)
        self._set_text_if_changed(self.attempt_label, "尝试 {0} 次".format(run.get("attempt", 1)))

    def _on_config_selected(self, _index=0):
        if self._updating_config or self.on_config_change is None:
            return
        self.on_config_change(self.key, str(self.config_combo.currentData() or ""))

    def set_config_options(self, options, current=""):
        self._updating_config = True
        self.config_combo.blockSignals(True)
        try:
            self.config_combo.clear()
            self.config_combo.addItem("默认配置", "")
            seen = {""}
            for label, val in options or []:
                val_str = str(val)
                if val_str in seen:
                    continue
                seen.add(val_str)
                self.config_combo.addItem(str(label), val_str)
            cur = str(current or "")
            if cur and cur not in seen:
                self.config_combo.addItem(cur, cur)
            idx = self.config_combo.findData(cur)
            self.config_combo.setCurrentIndex(max(0, idx))
        finally:
            self.config_combo.blockSignals(False)
            self._updating_config = False


class CardsScrollArea(QScrollArea):
    """项目卡区域的滚动容器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._content_height = 0
        self._row_height = 0
        self._row_spacing = 0

    def set_row_metrics(self, row_height, row_spacing):
        row_height = int(row_height)
        row_spacing = int(row_spacing)
        if (row_height, row_spacing) == (self._row_height, self._row_spacing):
            return
        self._row_height = row_height
        self._row_spacing = row_spacing
        self.updateGeometry()

    def snapped_height(self, available):
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
        return self.snapped_height(available)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        floor = CARDS_MIN_HEIGHT
        if self._row_height > 0:
            floor = max(floor, self._row_height)
        height = max(hint.height(), floor)
        return QSize(hint.width(), height)

