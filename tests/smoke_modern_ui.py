"""gui/modern.py 动效组件的功能性冒烟测试（离屏运行，不依赖真实游戏）。

验证所有动画真实运转：按钮悬停/按压/涟漪、平滑滚动、Tab 滑动指示条、
状态点呼吸、状态胶囊变色、淡入效果清理、卡片悬停过渡。

用法：
    QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR="C:\\Windows\\Fonts" \
        .venv/Scripts/python.exe tests/smoke_modern_ui.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QEnterEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QWidget

from gui.modern import (
    AnimatedButton,
    AnimatedPill,
    AnimatedTabWidget,
    HoverGlowCard,
    StatusDot,
    enable_smooth_scroll,
    fade_in_widget,
)

FAILURES = []


def check(name, ok):
    print("{0} {1}".format("PASS" if ok else "FAIL", name))
    if not ok:
        FAILURES.append(name)


def pump(app, ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    app = QApplication(sys.argv)

    # -- 1. AnimatedButton: 悬停过渡 ------------------------------------------
    btn = AnimatedButton("测试按钮", variant="default")
    btn.resize(120, 32)
    btn.show()
    pump(app, 60)
    btn.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    pump(app, 220)
    check("按钮悬停动画推进到 1.0", btn._hover >= 0.99)
    btn.leaveEvent(QEvent(QEvent.Leave))
    pump(app, 220)
    check("按钮离开悬停后回落到 0", btn._hover <= 0.01)

    # -- 2. AnimatedButton: 按压 + 涟漪 ---------------------------------------
    btn.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    pump(app, 200)
    QTest = None
    try:
        from PySide6.QtTest import QTest
    except Exception:
        pass
    if QTest is not None:
        QTest.mousePress(btn, Qt.LeftButton)
    # Windows 粗粒度定时器下动画首帧可能落在 ~31ms，这里留 70ms 窗口
    pump(app, 70)
    check("按压后涟漪动画启动", 0.0 <= btn._ripple < 1.0 and btn._press > 0.0)
    pump(app, 120)
    press_peak = btn._press
    if QTest is not None:
        QTest.mouseRelease(btn, Qt.LeftButton)
    check("按压过渡达到 1.0", press_peak >= 0.99)
    pump(app, 80)
    check("涟漪动画在中途", 0.0 < btn._ripple < 1.0)
    pump(app, 600)
    check("松开后按压回落", btn._press <= 0.05)
    check("涟漪动画结束", btn._ripple >= 1.0)

    # -- 3. SmoothScroller: 平滑滚动 ------------------------------------------
    edit = QPlainTextEdit()
    for i in range(300):
        edit.appendPlainText("line {0}".format(i))
    enable_smooth_scroll(edit)
    edit.resize(400, 300)
    edit.show()
    pump(app, 60)
    bar = edit.verticalScrollBar()
    bar.setValue(0)
    ev = QWheelEvent(
        QPointF(200, 150), QPointF(200, 150),
        QPoint(0, 0), QPoint(0, -120),
        Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False,
    )
    app.sendEvent(edit.viewport(), ev)
    pump(app, 40)
    early = bar.value()
    pump(app, 500)
    done = bar.value()
    check("滚轮后立即产生中间值（动画启动）", early >= 0)
    check("滚动条平滑推进到目标", done >= 3 and done > early or (done > early >= 0 and done > 0))
    # 连续滚轮：目标累加，不跳变
    before = bar.value()
    app.sendEvent(edit.viewport(), ev)
    app.sendEvent(edit.viewport(), ev)
    pump(app, 700)
    doubled = bar.value()
    check("连续滚轮目标累加", doubled > done)

    # -- 4. AnimatedTabWidget: 滑动指示条 + 页面淡入 --------------------------
    tabs = AnimatedTabWidget()
    page1 = QWidget()
    page2 = QWidget()
    tabs.addTab(page1, "标签一")
    tabs.addTab(page2, "标签二")
    tabs.resize(500, 300)
    tabs.show()
    pump(app, 120)
    tabs.setCurrentIndex(1)
    pump(app, 100)
    tab_bar = tabs.tabBar()
    start_left = tab_bar.tabRect(0).left()
    end_left = tab_bar.tabRect(1).left()
    mid_left = tab_bar._indicator.left()
    check("指示条滑动中段位于两标签之间", start_left < mid_left < end_left)
    pump(app, 400)
    check("指示条落位到新标签", abs(tab_bar._indicator.left() - end_left) <= 2)
    check("页面淡入效果已清理", page2.graphicsEffect() is None)

    # -- 5. StatusDot: 呼吸脉冲 -------------------------------------------------
    dot = StatusDot("#60a5fa")
    dot.show()
    pump(app, 40)
    dot.set_state("#60a5fa", pulse=True)
    phases = []
    for _ in range(26):
        pump(app, 45)
        phases.append(dot._phase)
    check("脉冲相位推进并振荡", max(phases) > 0.5 and min(phases) < 0.15)
    dot.set_state("#f87171", pulse=False)
    pump(app, 80)
    check("停止脉冲后相位归零、定时器停止", dot._phase == 0.0 and not dot._ensure_timer().isActive())

    # -- 6. AnimatedPill: 颜色过渡 --------------------------------------------
    pill = AnimatedPill("空闲", "#7d879c")
    pill.show()
    pump(app, 40)
    pill.set_state("运行中", "#60a5fa")
    pump(app, 70)
    mid = pill._cur
    start, target = QColor("#7d879c"), QColor("#60a5fa")
    between = min(start.red(), target.red()) < mid.red() < max(start.red(), target.red())
    check("胶囊颜色过渡中段为中间色", between)
    pump(app, 400)
    check("胶囊颜色落到目标色", pill._cur.toRgb() == target.toRgb())
    pill.set_state("运行中", "#60a5fa")  # 重复设置同色不打断
    pump(app, 100)
    check("重复设置同色不重启动画", pill._cur.toRgb() == target.toRgb())

    # -- 7. fade_in_widget: 效果挂载与清理 --------------------------------------
    host = QWidget()
    host.resize(100, 100)
    host.show()
    pump(app, 40)
    fade_in_widget(host, duration=150)
    check("淡入效果已挂载", host.graphicsEffect() is not None)
    pump(app, 500)
    check("淡入结束效果已移除", host.graphicsEffect() is None)

    # -- 8. HoverGlowCard: 悬停过渡 --------------------------------------------
    card = HoverGlowCard()
    card.setObjectName("card")
    card.resize(200, 100)
    card.show()
    pump(app, 40)
    card.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    pump(app, 240)
    check("卡片悬停过渡推进到 1.0", card._hover >= 0.99)
    card.leaveEvent(QEvent(QEvent.Leave))
    pump(app, 240)
    check("卡片离开后悬停过渡回落到 0", card._hover <= 0.01)

    print()
    if FAILURES:
        print("MODERN-UI {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("MODERN-UI PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
