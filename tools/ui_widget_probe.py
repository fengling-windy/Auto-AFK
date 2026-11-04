"""把常见原生控件铺一张对照图，用于检查 QSS 覆盖是否完整。

用途：QSS 漏写某个子控件（例如 QSpinBox 的上下按钮、QComboBox 的
drop-down 箭头）时，控件会退回 Qt 默认绘制，在深色主题里非常突兀。
这个脚本把控件逐个截图拼成一张，肉眼即可发现漏网之处。

用法：
    QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR="C:\\Windows\\Fonts" \
        .venv/Scripts/python.exe tools/ui_widget_probe.py <输出png>
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTime
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from gui.modern import APP_QSS, apply_palette


def _row(grid, r, name, widget):
    label = QLabel(name)
    label.setObjectName("hintLabel")
    grid.addWidget(label, r, 0)
    grid.addWidget(widget, r, 1)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs" / "ui_widget_probe.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    apply_palette(app)
    app.setStyleSheet(APP_QSS)
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    app.setFont(font)

    host = QWidget()
    host.setObjectName("central")
    host.resize(560, 520)
    grid = QGridLayout(host)
    grid.setContentsMargins(20, 18, 20, 18)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(12)
    grid.setColumnStretch(1, 1)

    spin = QSpinBox()
    spin.setRange(0, 86400)
    spin.setSuffix(" 秒后执行")
    spin.setValue(60)
    _row(grid, 0, "QSpinBox", spin)

    spin2 = QSpinBox()
    spin2.setRange(1, 1440)
    spin2.setSuffix(" 分钟无操作后提示")
    spin2.setValue(30)
    _row(grid, 1, "QSpinBox 长后缀", spin2)

    combo = QComboBox()
    combo.addItems(["无操作", "关机", "休眠"])
    _row(grid, 2, "QComboBox", combo)

    time_edit = QTimeEdit()
    time_edit.setTime(QTime(0, 0))
    _row(grid, 3, "QTimeEdit", time_edit)

    line = QLineEdit()
    line.setText("E:/GameAFK")
    _row(grid, 4, "QLineEdit", line)

    check = QCheckBox("运行前更新")
    check.setChecked(True)
    _row(grid, 5, "QCheckBox", check)

    btn = QPushButton("普通按钮")
    _row(grid, 6, "QPushButton", btn)

    bar = QProgressBar()
    bar.setRange(0, 30)
    bar.setValue(18)
    bar.setTextVisible(False)
    _row(grid, 7, "QProgressBar", bar)

    lst = QListWidget()
    lst.addItems(["三月七小助手", "BAAH"])
    lst.setFixedHeight(70)
    _row(grid, 8, "QListWidget", lst)

    text = QPlainTextEdit()
    text.setPlainText("[08:09:47] [core] 启动更新: 正在后台更新全部项目...")
    text.setFixedHeight(70)
    _row(grid, 9, "QPlainTextEdit", text)

    extra = QVBoxLayout()
    extra.setContentsMargins(0, 0, 0, 0)
    extra.setSpacing(6)
    hint = QLabel("QScrollBar 竖条")
    hint.setObjectName("hintLabel")
    extra.addWidget(hint)
    scroller = QPlainTextEdit()
    scroller.setPlainText("\n".join("line {0}".format(i) for i in range(60)))
    scroller.setFixedHeight(110)
    extra.addWidget(scroller)
    grid.addLayout(extra, 10, 0, 1, 2)

    host.show()
    for _ in range(6):
        app.processEvents()
    pix = host.grab()
    pix.save(str(out))
    print("saved:", out, pix.width(), "x", pix.height())
    return 0


if __name__ == "__main__":
    sys.exit(main())
