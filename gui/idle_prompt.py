"""空闲触发的三选一提示框（停止 / 立刻开始 / 仅运行不占用鼠标任务）。

倒计时结束仍无选择时，按「立刻开始」处理（等价于自动运行所有任务）。
纯逻辑部分（DECISIONS / decide_on_timeout）与 Qt 解耦，便于离屏测试。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from gui.modern import AnimatedButton

DECISION_STOP = "stop"
DECISION_ALL = "all"
DECISION_BACKGROUND = "background"
DECISION_NONE = "none"

DECISION_LABELS = {
    DECISION_STOP: "停止",
    DECISION_ALL: "立刻开始",
    DECISION_BACKGROUND: "仅运行不占用鼠标任务",
}


def decide_on_timeout():
    """倒计时结束未选择时的默认动作。"""
    return DECISION_ALL


class IdlePromptDialog(QDialog):
    """30 秒倒计时 + 三个按钮；关闭窗口视为「停止」。"""

    def __init__(self, idle_text="", prompt_sec=30, parent=None):
        super().__init__(parent)
        self.decision = DECISION_NONE
        self._idle_text = idle_text or "—"
        self._remaining = max(0, int(prompt_sec))
        self._total = max(1, int(prompt_sec)) if prompt_sec else 1

        self.setWindowTitle("Auto-AFK 空闲自动挂机")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setModal(True)
        self.setMinimumWidth(460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)

        title = QLabel("电脑已空闲，是否开始挂机？")
        title_font = QFont("Microsoft YaHei UI")
        title_font.setPixelSize(17)
        title_font.setWeight(QFont.DemiBold)
        title.setFont(title_font)
        outer.addWidget(title)

        self.hint_label = QLabel(self._hint_text())
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setWordWrap(True)
        outer.addWidget(self.hint_label)

        bar = QProgressBar()
        bar.setRange(0, self._total)
        bar.setValue(self._total)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        outer.addWidget(bar)
        self.progress = bar

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        self.stop_btn = AnimatedButton("停止（不进游戏）", variant="danger")
        self.all_btn = AnimatedButton("立刻开始（全部任务）", variant="primary")
        self.bg_btn = AnimatedButton("仅运行不占用鼠标任务（云游戏 + 模拟器）")
        for btn in (self.stop_btn, self.all_btn, self.bg_btn):
            btn.setMinimumHeight(34)
            card_layout.addWidget(btn)

        keys = QLabel("快捷键：S 停止 / A 立刻开始 / B 仅后台任务 / Esc 停止")
        keys.setObjectName("hintLabel")
        card_layout.addWidget(keys)
        outer.addWidget(card)

        self.stop_btn.clicked.connect(lambda: self._choose(DECISION_STOP))
        self.all_btn.clicked.connect(lambda: self._choose(DECISION_ALL))
        self.bg_btn.clicked.connect(lambda: self._choose(DECISION_BACKGROUND))

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._countdown = None

    # -- 生命周期 --------------------------------------------------------------
    def _hint_text(self):
        return "空闲时长：{0}\n{1} 秒后未选择将自动运行【所有】任务。".format(
            self._idle_text, max(0, self._remaining)
        )

    def start_countdown(self, on_timeout=None):
        self._on_timeout = on_timeout
        self._timer.start()

    def _tick(self):
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.progress.setValue(0)
            self._choose(decide_on_timeout())
            return
        self.progress.setValue(self._remaining)
        self.hint_label.setText(self._hint_text())

    def _choose(self, decision):
        if self.decision != DECISION_NONE:
            return
        self.decision = decision
        self._timer.stop()
        self.accept()

    # -- 键盘 ------------------------------------------------------------------
    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self._choose(DECISION_STOP)
            return
        text = (event.text() or "").lower()
        if text == "s":
            self._choose(DECISION_STOP)
        elif text == "a":
            self._choose(DECISION_ALL)
        elif text == "b":
            self._choose(DECISION_BACKGROUND)
        else:
            super().keyPressEvent(event)

    def reject(self):
        # 关闭窗口（X / Esc）等同「停止」
        self._choose(DECISION_STOP)

    def showEvent(self, event):
        super().showEvent(event)
        self.progress.setValue(max(0, self._remaining))
        self.raise_()
        self.activateWindow()


def ask_idle(idle_text="", prompt_sec=30, parent=None, app=None):
    """弹出提示框并返回决定（stop / all / background）；无 Qt 环境时返回 all。

    这是给 GUI 线程调用的阻塞式封装；测试可直接构造 IdlePromptDialog 并调用
    _choose()，无需事件循环。
    """
    if app is None:
        app = QApplication.instance()
    if app is None:
        return decide_on_timeout()
    dialog = IdlePromptDialog(idle_text=idle_text, prompt_sec=prompt_sec, parent=parent)
    dialog._idle_text = idle_text
    dialog.start_countdown()
    dialog.exec()
    return dialog.decision if dialog.decision != DECISION_NONE else decide_on_timeout()


__all__ = [
    "DECISION_ALL",
    "DECISION_BACKGROUND",
    "DECISION_LABELS",
    "DECISION_NONE",
    "DECISION_STOP",
    "IdlePromptDialog",
    "ask_idle",
    "decide_on_timeout",
]
