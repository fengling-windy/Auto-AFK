"""无名片配色兜底是否跟主题走。

历史上 StatusDot / AnimatedPill / ParallelLinks 的默认色都是写死的深色主题值
（#7d879c / #3d7dfa）。调用方基本都会显式传色，但只要有一条路径漏了，浅色主题
下就会出现「白底上一颗几乎看不见的点」或「白底上一条深蓝弧线」。这里直接建
不带色参数的控件，检查它们取的是当前主题的值。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from gui import themes
from gui.modern import AnimatedPill, ParallelLinks, StatusDot, apply_theme

FAILS = []


def check(name, ok, detail=""):
    if ok:
        print("ok   " + name)
    else:
        print("FAIL " + name + (("  -- " + str(detail)) if detail else ""))
        FAILS.append(name)


def _rgb(color):
    return (color.red(), color.green(), color.blue())


def _parse(value):
    c = QColor(str(value))
    return (c.red(), c.green(), c.blue())


def _contrast(fg, bg):
    def lum(rgb):
        out = []
        for v in rgb:
            s = v / 255.0
            out.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _run():
    app = QApplication.instance() or QApplication([])
    for name in ("dark", "light"):
        apply_theme(app, name)
        token = themes.resolve(name)
        card = _parse(token["card"])
        expected = _parse(themes.status_color(name, "pending"))

        dot = StatusDot()
        check(
            "{0}: 不带色参数的状态点取当前主题的等待色".format(name),
            _rgb(dot._color) == expected,
            (_rgb(dot._color), expected),
        )
        check(
            "{0}: 状态点在卡片底上看得清（≥4.5）".format(name),
            _contrast(_rgb(dot._color), card) >= 4.5,
            round(_contrast(_rgb(dot._color), card), 2),
        )

        pill = AnimatedPill("空闲")
        check(
            "{0}: 不带色参数的胶囊取当前主题的等待色".format(name),
            _rgb(pill._cur) == expected,
            (_rgb(pill._cur), expected),
        )

        links = ParallelLinks()
        check(
            "{0}: 不带色参数的连线层取当前主题的强调色".format(name),
            _rgb(links._color) == _parse(token["accent"]),
            (_rgb(links._color), token["accent"]),
        )

    # 显式传色仍然优先（调用方说了算）
    apply_theme(app, "light")
    custom = StatusDot("#123456")
    check("显式传色时不受主题默认值影响", _rgb(custom._color) == (0x12, 0x34, 0x56), _rgb(custom._color))
    check(
        "两套主题的默认等待色不同",
        themes.status_color("dark", "pending") != themes.status_color("light", "pending"),
    )
    return not FAILS


def main():
    ok = _run()
    print("MODERN-DEFAULTS", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
