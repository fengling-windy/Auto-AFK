"""桌面小组件：常驻浮窗，显示任务链状态与空闲倒计时，并提供快捷入口。

它是主窗口之外的第二块「看板」——主窗口关到托盘后，小组件仍能告诉你
「现在在跑什么」「再空闲多久会自动开始」，并且可以一键挂机 / 全部停止。

窗口属性：无边框 + Tool（不占任务栏）+ 可拖动；位置写在 settings.yaml 的
gui.widget_pos，下次启动回到原处。所有数据由主窗口的 300ms 轮询通过
tick() 推进（小组件自己不轮询、不碰 supervisor）。
"""

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from afk_core.idle import format_idle
from gui.modern import AnimatedButton, AnimatedPill, StatusDot

CHAIN_COLORS = {
    "idle": "#7d879c",
    "running": "#3d7dfa",
    "success": "#5fd28a",
    "failed": "#ff7a90",
    "stopped": "#ffb74d",
}
CHAIN_TEXT = {
    "idle": "空闲",
    "running": "运行中",
    "success": "全部完成",
    "failed": "有失败",
    "stopped": "已停止",
}


class DesktopWidget(QWidget):
    """桌面小组件浮窗。"""

    def __init__(self, on_chain=None, on_stop=None, on_show=None, parent=None):
        super().__init__(parent)
        self._on_chain = on_chain
        self._on_stop = on_stop
        self._on_show = on_show
        self._drag_origin = None
        self._collapsed = False
        self._on_moved = None

        self.setWindowTitle("Auto-AFK 小组件")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setObjectName("widgetRoot")
        self.setFixedWidth(232)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        self._card = card

        body = QVBoxLayout(card)
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(8)

        # -- 标题行：应用名 + 折叠按钮 ------------------------------------------
        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("Auto-AFK")
        title_font = QFont("Microsoft YaHei UI")
        title_font.setPixelSize(12)
        title_font.setWeight(QFont.DemiBold)
        title.setFont(title_font)
        self.state_dot = StatusDot(CHAIN_COLORS["idle"], pulse=False, period=1.2)
        self.fold_btn = AnimatedButton("—", variant="mini")
        self.fold_btn.setFixedWidth(26)
        self.fold_btn.setToolTip("折叠 / 展开")
        self.fold_btn.clicked.connect(self.toggle_collapsed)
        head.addWidget(self.state_dot)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.fold_btn)
        body.addLayout(head)

        # -- 状态行 ------------------------------------------------------------
        self.chain_pill = AnimatedPill("空闲", CHAIN_COLORS["idle"])
        body.addWidget(self.chain_pill, 0, Qt.AlignLeft)

        self.run_label = QLabel("未运行")
        self.run_label.setObjectName("hintLabel")
        self.run_label.setWordWrap(True)
        body.addWidget(self.run_label)

        # -- 空闲倒计时 --------------------------------------------------------
        self.idle_label = QLabel("空闲自动挂机：未启用")
        self.idle_label.setObjectName("hintLabel")
        self.idle_label.setWordWrap(True)
        body.addWidget(self.idle_label)

        # -- 按钮区（折叠时隐藏）----------------------------------------------
        self._actions = QWidget()
        actions = QVBoxLayout(self._actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.chain_btn = AnimatedButton("一键挂机", variant="primary")
        self.chain_btn.clicked.connect(lambda: self._fire(self._on_chain))
        self.stop_btn = AnimatedButton("全部停止", variant="danger")
        self.stop_btn.clicked.connect(lambda: self._fire(self._on_stop))
        self.show_btn = AnimatedButton("显示主窗口")
        self.show_btn.clicked.connect(lambda: self._fire(self._on_show))
        for btn in (self.chain_btn, self.stop_btn, self.show_btn):
            btn.setMinimumHeight(28)
            actions.addWidget(btn)
        body.addWidget(self._actions)

        self._hint = QLabel("拖动标题区域可移动")
        self._hint.setObjectName("hintLabel")
        body.addWidget(self._hint)

        # 末尾必须留弹性空隙：否则 QVBoxLayout 会把可伸展的 QLabel
        # （默认 Preferred 纵向策略）拉高填满窗口，折叠后剩下一大片空白，
        # 小组件高度几乎不缩（实测 267 -> 261，只有 6px）。
        body.addStretch(1)

    # -- 外部接口 --------------------------------------------------------------
    def tick(self, snapshot, idle_status=None, threshold_sec=0, completed_today=False):
        """由主窗口轮询调用：snapshot 来自 supervisor.status_snapshot()。"""
        chain = (snapshot or {}).get("chain_status", "idle")
        self.chain_pill.set_state(CHAIN_TEXT.get(chain, chain), CHAIN_COLORS.get(chain, CHAIN_COLORS["idle"]))
        running = [
            key
            for key, run in ((snapshot or {}).get("runs") or {}).items()
            if isinstance(run, dict) and run.get("status") == "running"
        ]
        self.state_dot.set_state(
            CHAIN_COLORS.get(chain, CHAIN_COLORS["idle"]),
            pulse=bool(running) or chain == "running",
            period=1.2,
        )
        if running:
            self.run_label.setText("正在运行：{0}".format("、".join(running)))
        else:
            self.run_label.setText("未运行任何任务")

        self._completed_today = bool(completed_today)
        self.idle_label.setText(self._idle_text(idle_status, threshold_sec))

    def _idle_text(self, idle_status, threshold_sec):
        if not idle_status or not idle_status.get("enabled"):
            return "空闲自动挂机：未启用"
        if not idle_status.get("supported"):
            return "空闲自动挂机：当前系统不支持检测"
        if getattr(self, "_completed_today", False):
            return "今日已完成，空闲时不再自动执行"
        idle = float(idle_status.get("idle_sec") or 0)
        threshold = float(threshold_sec or idle_status.get("threshold_sec") or 0)
        cooldown = int(idle_status.get("cooldown_left_sec") or 0)
        if cooldown > 0:
            return "刚触发过，{0} 分后恢复监听".format(max(1, cooldown // 60))
        if threshold and idle >= threshold:
            return "空闲达标：{0}（等待提示框）".format(format_idle(idle))
        if threshold:
            left = max(0, int(threshold - idle))
            return "空闲 {0}，再 {1} 自动开始".format(format_idle(idle), format_idle(left))
        return "空闲 {0}".format(format_idle(idle))

    def toggle_collapsed(self):
        self._collapsed = not self._collapsed
        self._actions.setVisible(not self._collapsed)
        self._hint.setVisible(not self._collapsed)
        self.run_label.setVisible(not self._collapsed)
        self.fold_btn.setText("+" if self._collapsed else "—")
        # 隐藏子控件不会在同一次调用栈里刷新 sizeHint（Qt 把布局重算排到
        # 事件循环里），所以此刻直接 adjustSize() 拿到的还是折叠前的 282px，
        # 小组件高度卡在 261 几乎不缩。推迟到下一轮事件循环再按真实
        # sizeHint 收缩。
        QTimer.singleShot(0, self._apply_collapsed_size)

    def _apply_collapsed_size(self):
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        target = max(self.minimumSizeHint().height(), self.sizeHint().height())
        if target > 0 and target != self.height():
            self.resize(self.width(), target)

    # 位置校验至少要留这么多像素在屏上，否则判为「看不见」
    MIN_VISIBLE = 48

    def _is_on_some_screen(self, x, y):
        """(x, y) 放小组件后是否还留有可点到的部分。

        历史缺陷：restore_position 无条件接受写进 settings.yaml 的坐标。拔掉
        副屏、改分辨率或改缩放比例后，上次记下的坐标（例如 5000,300）仍然被
        原样恢复，小组件就在所有屏幕之外——用户既看不到也没法拖动，且这是
        每次启动都会重放的状态，只能手动改 settings.yaml 才能救回来。
        """
        try:
            from PySide6.QtCore import QRect
            from PySide6.QtGui import QGuiApplication

            width = self.width() if self.width() > 0 else 232
            rect = QRect(x, y, width, max(self.height(), self.MIN_VISIBLE))
            for screen in QGuiApplication.screens():
                area = screen.availableGeometry()
                overlap = area.intersected(rect)
                if (
                    overlap.width() >= min(self.MIN_VISIBLE, rect.width())
                    and overlap.height() >= min(self.MIN_VISIBLE, rect.height())
                ):
                    return True
        except Exception:
            # 拿不到屏幕信息时不要因此把用户存的位置丢掉
            return True
        return False

    def restore_position(self, pos):
        """pos 为 [x, y] 或 None；越界时回落到屏幕右上角附近。"""
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                x, y = int(pos[0]), int(pos[1])
            except (TypeError, ValueError):
                x = None
            if x is not None and self._is_on_some_screen(x, y):
                self.move(x, y)
                return
        screen = None
        try:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
        except Exception:
            screen = None
        if screen is not None:
            area = screen.availableGeometry()
            width = self.width() if self.width() > 0 else 232
            x = max(area.left(), area.right() - width - 24)
            y = area.top() + 96
            if y > area.bottom() - self.MIN_VISIBLE:
                y = max(area.top(), area.bottom() - self.height() - 24)
            self.move(x, y)
        else:
            self.move(1200, 120)

    def current_position(self):
        point = self.pos()
        return [int(point.x()), int(point.y())]

    # -- 拖动 ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._on_moved is not None:
            try:
                self._on_moved(self.current_position())
            except Exception as exc:
                # 回调是外部注入的（主窗口存小组件位置）。它坏了的话位置就不
                # 会被记住，下次开窗小组件跳回默认坐标——留痕好定位。
                logging.getLogger("afk.gui").debug("desktop widget move handler failed: %s", exc)

    def set_move_handler(self, handler):
        self._on_moved = handler

    def _fire(self, callback):
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            logging.getLogger("afk.gui").debug("desktop widget button callback failed: %s", exc)


__all__ = ["CHAIN_COLORS", "CHAIN_TEXT", "DesktopWidget"]
