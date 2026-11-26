"""总览页的多列实时日志：每个正在运行的项目单独一列，可拖动分隔条缩放。

设计要点：
- 一列 = 一个 QPlainTextEdit（不记 Undo、最多 4000 块），项目开始运行时自动开一列
  （可在工具栏关掉「运行时自动开启」），也可以手动用下拉框开任意项目的列；
- 「core / update / emulator」这类没有独立列的来源，统一落到「系统日志」列；
- 用户亲手关掉的列，在**同一次运行**里不会被自动再开（记 run_id，下一次运行才重新开）；
- 行追加遵循「只有追加前本来就停在底部才跟着滚」，用户往上翻着读历史不被拽走。
"""

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.modern import AnimatedButton, ElidedLabel, enable_smooth_scroll, fade_in_widget

CORE_KEY = "core"
CORE_TITLE = "系统日志"
LIVE_STATUSES = ("running", "hang")


def _mono_font():
    font = QFont("Cascadia Mono", 9)
    font.setStyleHint(QFont.Monospace)
    return font


class LogColumn(QFrame):
    """一列日志：标题行（色点 + 名称 + 状态 + 自动滚动 + 清空 + 关闭）+ 文本区。"""

    close_requested = Signal(str)

    def __init__(self, key, title, color, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("logColumn")
        self.setMinimumWidth(180)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("logColumnHeader")
        head = QHBoxLayout(header)
        head.setContentsMargins(10, 7, 6, 7)
        head.setSpacing(6)
        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self.title_label = ElidedLabel(title, min_width=40, max_hint_width=200)
        self.title_label.setObjectName("stageSideTitle")
        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        self.follow_check = QCheckBox("跟随")
        self.follow_check.setChecked(True)
        self.follow_check.setToolTip("有新日志时自动滚到底部（你往上翻着看时不会被拉回）")
        self.clear_btn = AnimatedButton("清空", variant="mini")
        self.clear_btn.clicked.connect(self.clear)
        self.close_btn = AnimatedButton("×", variant="mini")
        self.close_btn.setFixedWidth(24)
        self.close_btn.setToolTip("关闭这一列（本次运行内不会再自动打开）")
        self.close_btn.clicked.connect(lambda: self.close_requested.emit(self.key))
        head.addWidget(self._dot)
        head.addWidget(self.title_label, 1)
        head.addWidget(self.status_label)
        head.addWidget(self.follow_check)
        head.addWidget(self.clear_btn)
        head.addWidget(self.close_btn)
        outer.addWidget(header)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setUndoRedoEnabled(False)
        self.view.setMaximumBlockCount(4000)
        self.view.setFont(_mono_font())
        enable_smooth_scroll(self.view)
        outer.addWidget(self.view, 1)
        self.set_color(color)

    def set_color(self, color):
        self._dot.setStyleSheet("background: {0}; border-radius: 4px;".format(color))

    def set_title(self, title):
        if self.title_label.text() != title:
            self.title_label.setText(title)

    def set_status(self, text, color=None):
        if self.status_label.text() != text:
            self.status_label.setText(text)
        style = "color: {0};".format(color) if color else ""
        if self.status_label.styleSheet() != style:
            self.status_label.setStyleSheet(style)

    def _at_bottom(self):
        bar = self.view.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _scroll_bottom(self):
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append_html_lines(self, lines, follow=True):
        lines = [line for line in lines if line]
        if not lines:
            return
        stick = follow and self.follow_check.isChecked() and self._at_bottom()
        if len(lines) == 1:
            self.view.appendHtml(lines[0])
        else:
            # 按段落拼：<br> 只产生一个文本块，会让 maximumBlockCount 失效
            self.view.appendHtml(
                '<p style="margin:0">' + '</p><p style="margin:0">'.join(lines) + "</p>"
            )
        if stick:
            self._scroll_bottom()

    def set_html_lines(self, lines, follow=True):
        self.view.setUpdatesEnabled(False)
        try:
            self.view.clear()
            lines = [line for line in lines if line]
            if lines:
                self.view.appendHtml(
                    '<p style="margin:0">' + '</p><p style="margin:0">'.join(lines) + "</p>"
                )
            if follow and self.follow_check.isChecked():
                self._scroll_bottom()
        finally:
            self.view.setUpdatesEnabled(True)

    def clear(self):
        self.view.clear()

    def refresh_theme(self, color):
        self.set_color(color)
        for btn in (self.clear_btn, self.close_btn):
            btn.set_variant(btn.variant_name)


class MultiColumnLogBoard(QFrame):
    """工具栏 + 横向 QSplitter（每列一个项目）。"""

    auto_open_toggled = Signal(bool)
    rerender_requested = Signal()
    columns_changed = Signal()

    def __init__(self, auto_open=True, parent=None):
        super().__init__(parent)
        self.setObjectName("logCard")
        self._columns = {}
        self._catalog = []  # [(key, title)]
        self._user_closed_runs = {}
        self._live_runs = {}
        self._color_fn = None
        self._backfill_fn = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._bar = bar
        title = QLabel("实时日志")
        title.setObjectName("sectionTitleCard")
        self.count_label = QLabel("")
        self.count_label.setObjectName("hintLabel")
        self.add_combo = QComboBox()
        self.add_combo.setMinimumWidth(110)
        self.add_btn = AnimatedButton("打开列", variant="mini")
        self.add_btn.clicked.connect(self._on_add_clicked)
        self.auto_open_check = QCheckBox("运行时自动开启")
        self.auto_open_check.setChecked(bool(auto_open))
        self.auto_open_check.setToolTip("项目开始运行时自动为它新开一列日志")
        self.auto_open_check.toggled.connect(self.auto_open_toggled.emit)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索日志关键字…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(220)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self.rerender_requested.emit)
        self.search.textChanged.connect(lambda *_: self._search_timer.start())
        self.autoscroll_check = QCheckBox("自动滚动")
        self.autoscroll_check.setChecked(True)
        clear_all = AnimatedButton("清空全部", variant="mini")
        clear_all.clicked.connect(self.clear_all)
        bar.addWidget(title)
        bar.addWidget(self.count_label)
        bar.addSpacing(6)
        bar.addWidget(self.add_combo)
        bar.addWidget(self.add_btn)
        bar.addWidget(self.search, 1)
        bar.addStretch(1)
        bar.addWidget(self.auto_open_check)
        bar.addWidget(self.autoscroll_check)
        bar.addWidget(clear_all)
        outer.addLayout(bar)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        outer.addWidget(self.splitter, 1)

        self.empty_hint = QWidget()
        hint_layout = QVBoxLayout(self.empty_hint)
        hint_layout.setAlignment(Qt.AlignCenter)
        hint = QLabel("没有打开的日志列")
        hint.setObjectName("pagePlaceholder")
        hint.setAlignment(Qt.AlignCenter)
        open_core = AnimatedButton("打开系统日志", variant="mini")
        open_core.clicked.connect(lambda: self.open_column(CORE_KEY, CORE_TITLE, None))
        hint_layout.addWidget(hint)
        hint_layout.addWidget(open_core, 0, Qt.AlignCenter)
        outer.addWidget(self.empty_hint, 1)
        self._sync_empty()

    # -- 兼容旧接口 -----------------------------------------------------------
    @property
    def log_view(self):
        """任意一列的文本区（系统日志列优先）；没有列时返回一个隐藏的占位。"""
        col = self._columns.get(CORE_KEY) or next(iter(self._columns.values()), None)
        if col is not None:
            return col.view
        if not hasattr(self, "_dummy_view"):
            self._dummy_view = QPlainTextEdit()
            self._dummy_view.setMaximumBlockCount(1)
        return self._dummy_view

    def keyword(self):
        return self.search.text().strip().lower()

    def accepts(self, event):
        word = self.keyword()
        if not word:
            return True
        hay = "{0} {1} {2}".format(
            event.get("project", ""), event.get("level", ""), event.get("message", "")
        ).lower()
        return word in hay

    # -- 列管理 ---------------------------------------------------------------
    def set_column_providers(self, color_fn=None, backfill_fn=None):
        """注册按列键取色和回填最近历史的方法（打开列 Dropdown / 系统日志按钮共用）。"""
        self._color_fn = color_fn
        self._backfill_fn = backfill_fn

    def set_catalog(self, items):
        """items: [(key, title)]，供「打开列」下拉用。"""
        items = [(CORE_KEY, CORE_TITLE)] + [(k, t) for k, t in items if k != CORE_KEY]
        if items == self._catalog:
            return
        self._catalog = items
        current = self.add_combo.currentData()
        self.add_combo.blockSignals(True)
        self.add_combo.clear()
        for key, label in items:
            self.add_combo.addItem(label, key)
        idx = self.add_combo.findData(current)
        self.add_combo.setCurrentIndex(max(0, idx))
        self.add_combo.blockSignals(False)

    def column_keys(self):
        return list(self._columns.keys())

    def has_column(self, key):
        return key in self._columns

    def column(self, key):
        return self._columns.get(key)

    def _title_for(self, key):
        for k, t in self._catalog:
            if k == key:
                return t
        return CORE_TITLE if key == CORE_KEY else key

    def _sync_empty(self):
        has = bool(self._columns)
        self.splitter.setVisible(has)
        self.empty_hint.setVisible(not has)
        self.count_label.setText("{0} 列".format(len(self._columns)) if has else "")
        self.columns_changed.emit()

    def _on_add_clicked(self):
        key = self.add_combo.currentData()
        if key:
            self._user_closed_runs.pop(key, None)
            self.open_column(key, self._title_for(key), None, focus=True)

    def open_column(self, key, title, color, backfill_lines=None, focus=False):
        col = self._columns.get(key)
        if col is not None:
            if focus:
                fade_in_widget(col, duration=160)
            return col
        if color is None and self._color_fn is not None:
            color = self._color_fn(key)
        if backfill_lines is None and self._backfill_fn is not None:
            backfill_lines = self._backfill_fn(key)
        col = LogColumn(key, title or self._title_for(key), color or "#8b97ad")
        col.close_requested.connect(lambda k: self.close_column(k, user_initiated=True))
        self._columns[key] = col
        self.splitter.addWidget(col)
        if backfill_lines:
            col.set_html_lines(backfill_lines, follow=True)
        # 新列与老列平分宽度：先均分，用户之后拖分隔条再自己调
        count = self.splitter.count()
        self.splitter.setSizes([1000] * count)
        self._sync_empty()
        fade_in_widget(col, duration=180)
        return col

    def close_column(self, key, user_initiated=False):
        col = self._columns.pop(key, None)
        if col is None:
            return
        if user_initiated:
            run_id = self._live_runs.get(key)
            if run_id:
                self._user_closed_runs[key] = run_id
        col.setParent(None)
        col.deleteLater()
        count = self.splitter.count()
        if count:
            self.splitter.setSizes([1000] * count)
        self._sync_empty()

    def clear_all(self):
        for col in self._columns.values():
            col.clear()

    # -- 事件路由 ---------------------------------------------------------------
    def route_key(self, project):
        project = str(project or CORE_KEY)
        if project in self._columns:
            return project
        return CORE_KEY if CORE_KEY in self._columns else None

    def route_event_html(self, project, html):
        key = self.route_key(project)
        if key is None:
            return False
        self._columns[key].append_html_lines([html], follow=self.autoscroll_check.isChecked())
        return True

    def append_batch(self, by_project, follow=True):
        """by_project: {project: [html, ...]}，按列分发一整批。"""
        follow = follow and self.autoscroll_check.isChecked()
        grouped = {}
        for project, lines in by_project.items():
            key = self.route_key(project)
            if key is not None:
                grouped.setdefault(key, []).extend(lines)
        for key, lines in grouped.items():
            self._columns[key].append_html_lines(lines, follow=follow)

    def rerender_all(self, events, html_for_event, limit=2000):
        """按各列自己的来源 + 关键字重画（切搜索词 / 新开列后用）。"""
        per_col = {key: [] for key in self._columns}
        for event in events:
            if not self.accepts(event):
                continue
            key = self.route_key(event.get("project"))
            if key is not None:
                per_col[key].append(event)
        for key, col in self._columns.items():
            col.set_html_lines(
                [html_for_event(e) for e in per_col[key][-limit:]],
                follow=self.autoscroll_check.isChecked(),
            )

    # -- 运行态同步 -------------------------------------------------------------
    def sync_running_projects(self, runs, info_fn, status_fn, backfill_fn=None):
        """runs: supervisor 快照里的 runs；info_fn(key)->(title,color)；status_fn(status)->(text,color)。"""
        live = {}
        for key, run in (runs or {}).items():
            if not isinstance(run, dict):
                continue
            status = run.get("status")
            if status in LIVE_STATUSES:
                live[key] = run.get("run_id") or ""
        self._live_runs = live
        # 同一个键换了新的 run_id = 新一轮运行：之前用户关掉的记号作废
        for key in list(self._user_closed_runs):
            if live.get(key) != self._user_closed_runs[key]:
                self._user_closed_runs.pop(key, None)
        if self.auto_open_check.isChecked():
            for key, run_id in live.items():
                if key in self._columns or self._user_closed_runs.get(key) == run_id:
                    continue
                title, color = info_fn(key)
                lines = backfill_fn(key) if backfill_fn is not None else None
                self.open_column(key, title, color, backfill_lines=lines)
        for key, col in self._columns.items():
            if key == CORE_KEY:
                continue
            run = (runs or {}).get(key)
            if isinstance(run, dict):
                text, color = status_fn(run.get("status", "pending"))
                col.set_status(text, color)
            else:
                col.set_status("", None)

    def refresh_theme(self, color_fn=None):
        for key, col in self._columns.items():
            color = color_fn(key) if color_fn is not None else None
            col.refresh_theme(color or "#8b97ad")
        for btn in (self.add_btn,):
            btn.set_variant(btn.variant_name)
