"""总览页的「上一个 ← 当前运行 → 下一个」舞台（Linear / Apple 风格三卡片）。

- 当前运行是视觉焦点（更大、顶部强调条、呼吸状态点），左右两张是弱化的侧卡；
- 并行时中间卡片里并排列出所有正在跑的任务；
- update_stage 用签名比对，300ms 轮询在状态没变时一个控件都不写；
- 窄于 720px 时箭头隐藏、三卡改为纵向堆叠。
"""

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.modern import AnimatedButton, ElidedLabel, HoverGlowCard, StatusDot

LIVE = ("running", "hang")
TERMINAL = ("success", "success_unverified", "failed", "stopped", "hang")
NARROW_WIDTH = 720


def _fmt_elapsed(seconds):
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return "{0}:{1:02d}:{2:02d}".format(h, m, s) if h else "{0:02d}:{1:02d}".format(m, s)


class _SideCard(QFrame):
    """上一个 / 下一个：弱化的信息卡。"""

    def __init__(self, badge, empty_text, parent=None):
        super().__init__(parent)
        self.setObjectName("stageSideCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._empty_text = empty_text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.badge = QLabel(badge)
        self.badge.setObjectName("stageBadge")
        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        top.addWidget(self.badge)
        top.addStretch(1)
        top.addWidget(self.status)
        layout.addLayout(top)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        self.bar = QFrame()
        self.bar.setFixedSize(3, 16)
        self.title = ElidedLabel("", min_width=40, max_hint_width=240)
        self.title.setObjectName("stageSideTitle")
        self.config = QLabel("")
        self.config.setObjectName("configPill")
        title_row.addWidget(self.bar)
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.config)
        layout.addLayout(title_row)
        self.detail = ElidedLabel("", min_width=40, max_hint_width=300)
        self.detail.setObjectName("cardDetail")
        layout.addWidget(self.detail)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.footer)
        self._sig = None

    def set_info(self, info):
        """info=None 表示空状态。"""
        sig = tuple(sorted(info.items())) if info else None
        if sig == self._sig:
            return
        self._sig = sig
        if not info:
            self.title.setText(self._empty_text)
            self.title.setStyleSheet("color: gray;")
            self.bar.setStyleSheet("background: transparent;")
            self.config.hide()
            self.status.setText("")
            self.detail.setText("")
            return
        self.title.setStyleSheet("")
        self.title.setText(info["title"])
        self.bar.setStyleSheet("background: {0}; border-radius: 1.5px;".format(info["color"]))
        cfg = info.get("config") or ""
        self.config.setText(cfg)
        self.config.setVisible(bool(cfg))
        self.status.setText(info.get("status_text", ""))
        self.status.setStyleSheet("color: {0};".format(info["status_color"]) if info.get("status_color") else "")
        self.detail.setText(info.get("detail", ""))


class _RunRow(QWidget):
    """当前运行卡片里的一行（一个正在跑的任务）。"""

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.started_at = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = StatusDot()
        self.title = ElidedLabel("", min_width=60, max_hint_width=360)
        self.title.setObjectName("stageHeroTitle")
        self.config = QLabel("")
        self.config.setObjectName("configPill")
        self.status = QLabel("")
        self.status.setObjectName("cardStatus")
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("hintLabel")
        top.addWidget(self.dot)
        top.addWidget(self.title, 1)
        top.addWidget(self.config)
        top.addWidget(self.status)
        top.addWidget(self.elapsed)
        layout.addLayout(top)
        self.detail = ElidedLabel("", min_width=60, max_hint_width=600)
        self.detail.setObjectName("cardDetail")
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.stop_btn = AnimatedButton("停止", variant="danger")
        self.gui_btn = AnimatedButton("界面", variant="mini")
        self.log_btn = AnimatedButton("日志列", variant="mini")
        actions.addWidget(self.stop_btn)
        actions.addWidget(self.gui_btn)
        actions.addWidget(self.log_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

    def tick_elapsed(self):
        if self.started_at:
            self.elapsed.setText(_fmt_elapsed(time.time() - self.started_at))


class RunStageWidget(QWidget):
    stop_requested = Signal(str)
    gui_requested = Signal(str)
    logs_requested = Signal(str)
    open_order_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = {}
        self._sig = None
        self._narrow = False
        self._can_gui = lambda key: False
        outer = QBoxLayout(QBoxLayout.LeftToRight, self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        self._outer = outer

        self.prev_card = _SideCard("上一个任务", "暂无上一项")
        self.arrow_left = QLabel("›")
        self.arrow_left.setObjectName("stageArrow")
        self.arrow_left.setAlignment(Qt.AlignCenter)

        self.hero = HoverGlowCard()
        self.hero.setObjectName("stageHeroCard")
        self.hero.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(18, 13, 18, 13)
        hero_layout.setSpacing(8)
        head = QHBoxLayout()
        self.hero_badge = QLabel("当前运行")
        self.hero_badge.setObjectName("stageBadge")
        self.hero_count = QLabel("")
        self.hero_count.setObjectName("hintLabel")
        head.addWidget(self.hero_badge)
        head.addStretch(1)
        head.addWidget(self.hero_count)
        hero_layout.addLayout(head)
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(12)
        hero_layout.addLayout(self.rows_layout)
        self.idle_title = QLabel("空闲待命")
        self.idle_title.setObjectName("stageHeroTitle")
        self.idle_detail = QLabel("")
        self.idle_detail.setObjectName("cardDetail")
        self.idle_detail.setWordWrap(True)
        self.idle_btn = AnimatedButton("去编排运行顺序", variant="mini")
        self.idle_btn.clicked.connect(self.open_order_requested.emit)
        hero_layout.addWidget(self.idle_title)
        hero_layout.addWidget(self.idle_detail)
        hero_layout.addWidget(self.idle_btn, 0, Qt.AlignLeft)

        self.arrow_right = QLabel("›")
        self.arrow_right.setObjectName("stageArrow")
        self.arrow_right.setAlignment(Qt.AlignCenter)
        self.next_card = _SideCard("下一个任务", "队列已空")
        self.order_btn = AnimatedButton("调整顺序", variant="mini")
        self.order_btn.clicked.connect(self.open_order_requested.emit)
        self.next_card.footer.addWidget(self.order_btn)
        self.next_card.footer.addStretch(1)

        outer.addWidget(self.prev_card, 2)
        outer.addWidget(self.arrow_left)
        outer.addWidget(self.hero, 4)
        outer.addWidget(self.arrow_right)
        outer.addWidget(self.next_card, 2)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def set_gui_probe(self, fn):
        self._can_gui = fn

    def _tick(self):
        for row in self._rows.values():
            row.tick_elapsed()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._rows and not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, ev):
        self._timer.stop()
        super().hideEvent(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() < NARROW_WIDTH
        if narrow != self._narrow:
            self._narrow = narrow
            self._outer.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
            self.arrow_left.setVisible(not narrow)
            self.arrow_right.setVisible(not narrow)

    def _make_row(self, key):
        row = _RunRow(key)
        row.stop_btn.clicked.connect(lambda: self.stop_requested.emit(key))
        row.gui_btn.clicked.connect(lambda: self.gui_requested.emit(key))
        row.log_btn.clicked.connect(lambda: self.logs_requested.emit(key))
        self.rows_layout.addWidget(row)
        return row

    def update_stage(self, tasks, runs, queue, chain_status, name_of, config_of, color_of, status_of):
        """
        tasks: 任务 id 列表（运行顺序里启用的，按顺序）
        runs: 快照里的 runs
        queue: 链队列（id 列表）；链没在跑时为空
        name_of/config_of/color_of(key) 取显示名 / 配置 / 颜色；status_of(status)->(文字, 颜色)
        """
        runs = runs or {}
        running = [k for k, r in runs.items() if isinstance(r, dict) and r.get("status") in LIVE]
        finished = sorted(
            (k for k, r in runs.items()
             if isinstance(r, dict) and r.get("status") in TERMINAL and r.get("status") not in LIVE
             and r.get("finished_at")),
            key=lambda k: runs[k].get("finished_at") or 0,
        )
        prev_key = finished[-1] if finished else None
        base = list(queue) if (queue and chain_status == "running") else list(tasks)
        upcoming = [
            k for k in base
            if k not in running and (k not in runs or runs[k].get("status") == "pending")
        ]
        next_key = upcoming[0] if upcoming else None

        def _snap(key):
            r = runs.get(key) or {}
            return (key, r.get("status"), r.get("pid"), r.get("last_message"),
                    r.get("attempt"), r.get("started_at"), r.get("exit_code"))

        sig = (
            tuple(_snap(k) for k in running),
            _snap(prev_key) if prev_key else None,
            next_key,
            len(upcoming),
            tuple(base).__len__(),
            chain_status,
            tuple((k, name_of(k), config_of(k)) for k in ([prev_key, next_key] + running) if k),
            tuple(color_of(k) for k in ([prev_key, next_key] + running) if k),
        )
        if sig == self._sig:
            return
        self._sig = sig

        # -- 左 / 右 ---------------------------------------------------------
        def _side(key, with_status):
            if not key:
                return None
            r = runs.get(key) or {}
            text, color = status_of(r.get("status", "pending")) if with_status else ("待运行", None)
            detail = (r.get("last_message") or "") if with_status else ""
            if with_status and r.get("exit_code") is not None and not detail:
                detail = "exit={0}".format(r["exit_code"])
            return {
                "title": name_of(key), "config": config_of(key), "color": color_of(key),
                "status_text": text, "status_color": color, "detail": detail,
            }

        self.prev_card.set_info(_side(prev_key, True))
        nxt = _side(next_key, False)
        if nxt is not None:
            nxt["detail"] = "队列中还有 {0} 个任务".format(len(upcoming))
        self.next_card.set_info(nxt)

        # -- 中间 ------------------------------------------------------------
        for key in [k for k in self._rows if k not in running]:
            row = self._rows.pop(key)
            row.setParent(None)
            row.deleteLater()
        for key in running:
            row = self._rows.get(key)
            if row is None:
                row = self._make_row(key)
                self._rows[key] = row
            r = runs[key]
            text, color = status_of(r.get("status", "running"))
            row.title.setText(name_of(key))
            cfg = config_of(key)
            row.config.setText(cfg)
            row.config.setVisible(bool(cfg))
            row.status.setText(text)
            row.status.setStyleSheet("color: {0};".format(color))
            row.dot.set_state(color, pulse=True, period=1.1 if r.get("status") == "hang" else 1.6)
            parts = []
            if r.get("pid"):
                parts.append("pid={0}".format(r["pid"]))
            if r.get("attempt") and r.get("attempt") > 1:
                parts.append("第 {0} 次尝试".format(r["attempt"]))
            if r.get("last_message"):
                parts.append(str(r["last_message"])[:120])
            row.detail.setText(" | ".join(parts) or "运行中…")
            row.started_at = r.get("started_at")
            row.gui_btn.setVisible(bool(self._can_gui(key)))
            row.tick_elapsed()
        busy = bool(running)
        self.idle_title.setVisible(not busy)
        self.idle_detail.setVisible(not busy)
        self.idle_btn.setVisible(not busy)
        self.hero_count.setText("{0} 个并行".format(len(running)) if len(running) > 1 else "")
        if not busy:
            if chain_status == "success":
                self.idle_title.setText("全部完成")
            elif chain_status == "failed":
                self.idle_title.setText("本轮有失败")
            elif chain_status == "stopped":
                self.idle_title.setText("已停止")
            else:
                self.idle_title.setText("空闲待命")
            self.idle_detail.setText(
                "队列里共有 {0} 个任务，点「一键挂机」按顺序开始。".format(len(tasks))
                if tasks else "运行顺序里还没有任务，先去添加。"
            )
            self._timer.stop()
        elif self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def refresh_theme(self):
        self._sig = None
        buttons = [self.idle_btn, self.order_btn]
        for row in self._rows.values():
            buttons += [row.stop_btn, row.gui_btn, row.log_btn]
        for btn in buttons:
            btn.set_variant(btn.variant_name)
