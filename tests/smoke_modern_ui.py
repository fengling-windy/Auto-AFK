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
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QWidget

from gui.modern import (
    ASSET_DIR,
    APP_QSS,
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
    # 基于动画状态等待收尾，而不是固定 sleep：负载高时 280ms 滑动可能超过固定窗口
    deadline = time.monotonic() + 5.0
    while tab_bar._anim.state() != tab_bar._anim.State.Stopped and time.monotonic() < deadline:
        pump(app, 20)
    pump(app, 20)
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

    # -- 9. QSS 箭头资源：原生控件的箭头必须走 SVG，不能退回 Qt 默认浅色块 -----
    # 历史缺陷：QSpinBox 的上下按钮 / QComboBox 的 drop-down 未写 QSS 时，
    # Qt 用默认样式绘制，在深色圆角输入框里画出两个越出边界的浅色小方块。
    # 修法是给按钮设 transparent 背景 + SVG image 箭头（CSS 三角形边框在
    # QSS 里不生效，只会画出小方块，已实测）。
    check("QSS 已把占位符替换成实际资源目录", "__ASSET_DIR__" not in APP_QSS)
    for asset in ("arrow_up.svg", "arrow_down.svg", "chevron.svg"):
        path = Path(ASSET_DIR) / asset
        check("箭头资源存在 {0}".format(asset), path.is_file())
        check("QSS 引用了 {0}".format(asset), asset in APP_QSS)
    check("QSpinBox 上下按钮背景透明", "QSpinBox::up-button" in APP_QSS and "background: transparent" in APP_QSS)
    check("QComboBox 下拉箭头有独立样式", "QComboBox::down-arrow" in APP_QSS)
    # QTimeEdit 是 QAbstractSpinBox 的子类（右侧是上下两个堆叠按钮），不能跟
    # QComboBox 那样只给一个 ::down-arrow。历史缺陷：它被归进 QComboBox 那条
    # 规则，上面那个按钮仍由 Qt 默认画成实心三角，与下面我们给的 chevron 挤在
    # 一起、还越出圆角（header 的「定时启动 04:00」）。
    check("QTimeEdit 上下按钮单独写了样式", "QTimeEdit::up-button" in APP_QSS and "QTimeEdit::down-button" in APP_QSS)
    check("QTimeEdit 箭头用 SVG 而不是 Qt 默认三角", "QTimeEdit::up-arrow" in APP_QSS and "QTimeEdit::down-arrow" in APP_QSS)
    check("QComboBox 不再把 QTimeEdit 一起塞进 drop-down 规则", "QComboBox::drop-down, QTimeEdit::drop-down" not in APP_QSS)

    # -- 10. 状态色必须按主题给（浅色底上看得见） -------------------------------
    # 历史缺陷：DESKTOP/主窗口里的状态色是一份写死的深色配色，卡片状态文字直接
    # setStyleSheet("color: ...")。切到浅色主题后 #34d399 / #60a5fa 落在白卡上
    # 对比度只有 1.9 / 2.5，七个状态全部低于 WCAG AA 的 4.5，状态几乎看不见。
    from gui import themes as th

    def _rel_lum(hex_color):
        h = hex_color.lstrip("#")
        parts = []
        for i in (0, 2, 4):
            c = int(h[i:i + 2], 16) / 255.0
            parts.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]

    def _contrast(fg, bg):
        a, b = _rel_lum(fg), _rel_lum(bg)
        hi, lo = max(a, b), min(a, b)
        return (hi + 0.05) / (lo + 0.05)

    for name in ("dark", "light"):
        card_bg = th.resolve(name)["card"]
        weak = [
            state
            for state in ("success", "success_unverified", "running", "pending",
                          "failed", "hang", "stopped")
            if _contrast(th.status_color(name, state), card_bg) < 4.5
        ]
        check("{0} 主题七个状态色都过 4.5:1（弱项 {1}）".format(name, weak), not weak)
        weak_log = [
            level
            for level in ("error", "warn")
            if _contrast(th.log_color(name, level), card_bg) < 4.5
        ]
        check("{0} 主题日志级别色都过 4.5:1（弱项 {1}）".format(name, weak_log), not weak_log)
    check("深色主题没被改动（success 仍是 #34d399）",
          th.status_color("dark", "success") == "#34d399")
    check("浅色主题换了状态色（success 不再是 #34d399）",
          th.status_color("light", "success") != th.status_color("dark", "success"))
    check("链状态与卡片状态同色（running）",
          th.chain_color("light", "running") == th.status_color("light", "running"))
    check("未知状态回退 pending",
          th.status_color("light", "no-such-state") == th.status_color("light", "pending"))

    # -- 11. 项目身份色也必须按主题给 -----------------------------------------
    # 历史缺陷：PROJECT_COLORS 是一份写死的亮色板（#4fc3f7 / #ffd54f …）。浅色主题
    # 的白卡和白色日志区上，这 5 个项目色的对比度只有 1.41–3.45，卡片色条还在但
    # 日志前缀基本糊进背景。另外 settings.yaml 出厂就写着这 8 个默认色，若把它
    # 当「用户自定义」直接覆盖主题色板，浅色主题永远拿不到深色版。
    for name in ("dark", "light"):
        card_bg = th.resolve(name)["card"]
        log_bg = th.resolve(name)["input_bg_deep"]
        for key in ("March7thAssistant", "BAAH", "MaaEnd",
                    "ZenlessZoneZero-OneDragon", "BetterGI"):
            color = th.project_color(name, key)
            check("{0} 主题 {1} 的项目色存在".format(name, key), bool(color))
            check("{0} 主题 {1} 的项目色在卡片上过 4.5:1（{2}）".format(
                name, key, round(_contrast(color, card_bg), 2)),
                _contrast(color, card_bg) >= 4.5)
            check("{0} 主题 {1} 的项目色在日志区上过 4.5:1（{2}）".format(
                name, key, round(_contrast(color, log_bg), 2)),
                _contrast(color, log_bg) >= 4.5)
        weak_fallback = [
            i for i in range(8)
            if _contrast(th.project_fallback_color(name, "proj{0}".format(i)), card_bg) < 4.5
        ]
        check("{0} 主题备用项目色板都过 4.5:1（弱项 {1}）".format(name, weak_fallback),
              not weak_fallback)
    check("深色项目色没被改动（March7thAssistant 仍是 #4fc3f7）",
          th.project_color("dark", "March7thAssistant") == "#4fc3f7")
    check("浅色项目色与深色不同（March7thAssistant）",
          th.project_color("light", "March7thAssistant") != th.project_color("dark", "March7thAssistant"))
    check("项目色表里没有的键返回 None",
          th.project_color("light", "no-such-project") is None)
    check("按名字哈希的备用色稳定（同一键两次相同）",
          th.project_fallback_color("light", "Xyz") == th.project_fallback_color("light", "Xyz"))
    check("出厂默认项目色被认成「未自定义」",
          th.is_default_project_color("March7thAssistant", "#4fc3f7"))
    check("大小写不同也算默认色",
          th.is_default_project_color("March7thAssistant", "#4FC3F7"))
    check("改过的颜色不算默认色",
          not th.is_default_project_color("March7thAssistant", "#123456"))
    check("表里没有的项目键不算默认色",
          not th.is_default_project_color("no-such-project", "#4fc3f7"))

    # -- 12. 单色小图标也要按主题给 -------------------------------------------
    # 拖动把手/关闭/打开仓库这些是 QPixmap 位图，QSS 的 $nav_text 换不到它们身上。
    # 写死 #8b97ad / #7f8aa3 时，浅色白卡上的对比度只有 2.95 / 3.46，图标淡到
    # 几乎看不见（深色主题上反而正常），所以取色必须跟着当前主题走。
    for name in ("dark", "light"):
        card_bg = th.resolve(name)["card"]
        for subtle in (False, True):
            color = th.icon_color(name, subtle=subtle)
            level = 4.5 if not subtle else 3.0
            check("{0} 主题{1}图标色在白/深卡上过得去（{2} / {3}）".format(
                name, "次要" if subtle else "", color, round(_contrast(color, card_bg), 2)),
                _contrast(color, card_bg) >= level)
    check("深色与浅色的图标色不同",
          th.icon_color("dark") != th.icon_color("light"))
    check("深色图标色仍是原来的 #8b97ad",
          th.icon_color("dark") == "#8b97ad")
    check("未知主题的图标色回退深色",
          th.icon_color("no-such-theme") == th.icon_color("dark"))
    check("icon / icon_subtle 两档都在令牌表里",
          all("icon" in th.resolve(n) and "icon_subtle" in th.resolve(n)
              for n in ("dark", "light")))
    missing_icon, extra_icon = th.check_theme("light")
    check("两套主题的令牌表仍然完全对齐（缺 {0} / 多 {1}）".format(
        missing_icon, extra_icon), not missing_icon and not extra_icon)

    print()
    if FAILURES:
        print("MODERN-UI {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("MODERN-UI PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
