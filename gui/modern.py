"""Auto-AFK GUI 现代主题与动效组件。

包含：
- APP_QSS / apply_palette: 全局深色现代主题与基础调色板
- AnimatedButton: 悬停/按压颜色过渡 + 点击涟漪 + 按压下沉
- SmoothScroller / enable_smooth_scroll: 滚轮平滑滚动（缓动动画）
- AnimatedTabBar / AnimatedTabWidget: Tab 切换滑动指示条 + 页面淡入
- StatusDot: 状态点呼吸脉冲
- AnimatedPill: 状态胶囊颜色渐变
- HoverGlowCard: 卡片悬停边框过渡
- fade_in_widget / fade_in_window: 淡入工具
"""

import math
import time
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QFont,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGraphicsOpacityEffect,
    QFrame,
    QLayout,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QWidget,
)

ACCENT = "#4c8dff"


class FlowLayout(QLayout):
    """自动换行的流式布局（Qt 官方 FlowLayout 示例的等价实现）。

    存在的理由：总览页把 5 个项目卡横排在一个 QHBoxLayout 里，每张卡
    minimumSizeHint 约 264px，合计把 MainWindow 的最小宽度顶到 1592px
    —— 1366/1440 宽的笔记本上窗口撑出屏幕，w.resize(1360, 900) 会被 Qt
    直接拉回 1592。改用本布局后卡片按可用宽度自动折行，最小宽度回落到
    单张卡的宽度。
    """

    def __init__(self, parent=None, margin=0, h_spacing=12, v_spacing=12):
        super().__init__(parent)
        self._items = []
        self._h_spacing = int(h_spacing)
        self._v_spacing = int(v_spacing)
        self.setContentsMargins(margin, margin, margin, margin)

    # -- QLayout 要求的接口 ------------------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def v_spacing(self):
        return self._v_spacing

    def heightForWidth(self, width):
        return self._do_layout(QRectF(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(QRectF(rect), test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    # -- 折行计算 ----------------------------------------------------------
    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )

        visible = []
        widest = 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            # 卡片 minimumSizeHint 远大于 sizeHint 时用 minimum 兜底，
            # 否则窄窗口里卡片会被压到画不全。
            width = max(hint.width(), item.minimumSize().width())
            height = max(hint.height(), item.minimumSize().height())
            visible.append((item, width, height))
            widest = max(widest, width)

        if not visible:
            return margins.top() + margins.bottom()

        # 一行能放几张就均分成几张的宽度：所有卡片等宽，换行后最后一行
        # 左对齐（不拉伸成一张超宽卡，那样视觉上像出错）。
        per_row = max(1, int((effective.width() + self._h_spacing) // (widest + self._h_spacing)))
        # 末尾那行只剩一张卡时（例如 5 张卡排成 4+1），把每行减一张换成
        # 3+2 这种更均衡的分行，避免第二行孤零零一张。
        if per_row > 2 and len(visible) % per_row == 1:
            per_row -= 1
        cell = (effective.width() - (per_row - 1) * self._h_spacing) / per_row
        cell = max(cell, float(widest))

        y = effective.y()
        x = effective.x()
        row_height = 0
        placed = 0
        for item, _width, height in visible:
            if placed and placed % per_row == 0:
                y += row_height + self._v_spacing
                x = effective.x()
                row_height = 0
            if not test_only:
                item.setGeometry(
                    QRectF(x, y, cell, height).toAlignedRect()
                )
            x += cell + self._h_spacing
            row_height = max(row_height, height)
            placed += 1

        return y + row_height - rect.y() + margins.bottom()


class FlowHost(QWidget):
    """承载 FlowLayout 的容器：把「当前宽度下需要几行」的高度报给父布局。

    为什么需要它：FlowLayout 直接 addLayout 进 QVBoxLayout 时，父布局只按
    sizeHint（单张卡的高度）分配空间，折行后的第二行会被画到下面的控件上面
    （实测 1062 宽时第二行项目卡压住了实时日志的页签）。QWidget 的
    heightForWidth 只有在容器自己身上才被父布局稳定采纳，所以外面套一层。

    高度不写死成两行：宽窗口下只要一行，硬撑着两行的高度会白占屏幕。
    sizeHint / minimumSizeHint 都按当前宽度算，窗口从宽变窄时布局会跟着
    要求更多高度（这也让 MainWindow 的最小高度在窄窗口下自然抬高，
    而不是让内容重叠）。
    """

    def __init__(self, h_spacing=12, v_spacing=12, parent=None):
        super().__init__(parent)
        self._flow = FlowLayout(self, h_spacing=h_spacing, v_spacing=v_spacing)
        self._syncing = False
        # 纵向 Fixed + 自己按当前宽度算高度（见 sync_height）：不用 heightForWidth。
        # 早期版本走 heightForWidth，Qt 在窗口首轮布局前会用 QWidget 默认宽度
        # (640) 问一次 sizeHint，5 张卡被算成 5 行 848px，于是主窗口最小高度
        # 抬到 1165，show() 时窗口从 900 直接撑到 1165（1080p 屏贴着任务栏，
        # 1366x768 屏直接超出屏幕）。
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        policy.setHeightForWidth(False)
        self.setSizePolicy(policy)

    def flow(self):
        return self._flow

    def _rows_height(self, width=None):
        width = int(width if width is not None else self.width())
        if width <= 0 and self.parentWidget() is not None:
            width = int(self.parentWidget().width())
        if width <= 0:
            return self._single_row_height()
        height = self._flow.heightForWidth(width)
        if height <= 0:
            height = self._flow.minimumSize().height()
        return int(height)

    def _single_row_height(self):
        """单行时的高度（一行里最高的那张卡）。"""
        tallest = 0
        for index in range(self._flow.count()):
            item = self._flow.itemAt(index)
            if item is None:
                continue
            tallest = max(tallest, int(item.minimumSize().height()))
        if tallest <= 0:
            return 0
        margins = self._flow.contentsMargins()
        return int(tallest + margins.top() + margins.bottom())

    def hasHeightForWidth(self):
        return False

    def v_spacing(self):
        """行间距（取自内部 FlowLayout），供滚动区做整行吸附。"""
        return self._flow.v_spacing()

    def single_row_height(self):
        """单行时的高度（一行里最高的那张卡），供滚动区做整行吸附。"""
        return self._single_row_height()

    def rows_height_for(self, width):
        """给定容器宽度时该项目卡区域应有的高度（折成几行就有多高）。"""
        return self._rows_height(width)

    def sync_height(self, width=None):
        """按宽度钉住自身高度（折成几行就多高）。

        由父窗口在窗口宽度变化时调用——必须在真正 resize 时算，而不是让 Qt
        在布局求解阶段用未知宽度去猜。
        """
        target = self._rows_height(width)
        if target <= 0:
            return 0
        if self.minimumHeight() != target or self.maximumHeight() != target:
            self.setFixedHeight(target)
            self.updateGeometry()
        return target

    def sizeHint(self):
        # 首轮布局前宽度是不可信的（QWidget 默认 640），按当前 width() 算折行只会
        # 把 5 张卡算成竖排、把窗口最小高度顶到 1165。这里统一报「一行」，
        # 真实高度由 sync_height 在窗口 resize 之后钉死。
        return QSize(self._flow.minimumSize().width(), self._single_row_height())

    def minimumSizeHint(self):
        return QSize(self._flow.minimumSize().width(), self._single_row_height())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # 宽度变了折行数就可能变：自己把高度钉到「当前宽度需要几行」。
        # 放在 resizeEvent 里而不是交给布局求解，是因为布局求解阶段拿不到可信
        # 宽度（见 sizeHint 注释）；setFixedHeight 会再触发一次 resizeEvent，
        # 用 _syncing 兜住避免递归。
        if not self._syncing:
            self._syncing = True
            try:
                self.sync_height(self.width())
            finally:
                self._syncing = False
        self.updateGeometry()


APP_QSS = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    outline: none;
}
QMainWindow, QWidget#central {
    background: #0b0d12;
}
QFrame#header {
    background: #10141d;
    border: 1px solid #1d2432;
    border-radius: 16px;
}
QLabel#title {
    font-size: 20px;
    font-weight: 700;
    color: #ecf1fa;
}
QLabel#subtitle {
    font-size: 12px;
    color: #7f8aa3;
}
QLabel#sectionTitle {
    color: #7f8aa3;
    font-size: 12px;
    font-weight: 600;
    padding-left: 2px;
}
QFrame#card {
    background: #141926;
    border: 1px solid #212b3c;
    border-radius: 14px;
}
QLabel#cardName {
    font-size: 14px;
    font-weight: 700;
    color: #e9eef9;
}
QLabel#cardStatus {
    font-size: 13px;
    font-weight: 700;
}
QLabel#cardDetail {
    color: #7f8aa3;
    font-size: 11px;
}
QLabel#cardAttempt {
    color: #5c667d;
    font-size: 11px;
}
QPushButton {
    background: #1a2130;
    color: #ccd6ea;
    border: 1px solid #2a3547;
    border-radius: 9px;
    padding: 6px 14px;
    font-size: 12px;
}
QPushButton:hover {
    background: #243048;
    border-color: #3f5173;
}
QPushButton:pressed {
    background: #151c2a;
}
QPushButton:disabled {
    color: #59627a;
    background: #161c28;
}
QPushButton#primary {
    background: #3d7dfa;
    border: 1px solid #3d7dfa;
    color: #ffffff;
    font-weight: 700;
    padding: 7px 20px;
}
QPushButton#primary:hover {
    background: #5594ff;
    border-color: #5594ff;
}
QPushButton#danger {
    background: #251821;
    border: 1px solid #48242f;
    color: #ff92a2;
}
QPushButton#danger:hover {
    background: #33212c;
}
QPushButton#mini {
    padding: 4px 8px;
    font-size: 11px;
    border-radius: 7px;
}
QTabWidget::pane {
    border: 1px solid #1d2432;
    border-radius: 12px;
    background: #10141d;
    top: -1px;
}
QTabBar {
    background: transparent;
}
QTabBar::tab {
    background: transparent;
    color: #7f8aa3;
    padding: 9px 16px;
    margin-right: 4px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
}
QTabBar::tab:hover {
    color: #b9c6de;
    background: rgba(255, 255, 255, 0.04);
}
QTabBar::tab:selected {
    color: #ecf1fa;
}
QPlainTextEdit, QTextEdit {
    background: #0a0c11;
    border: 1px solid #1d2432;
    border-radius: 10px;
    color: #d7deec;
    selection-background-color: #2b4a7a;
    padding: 6px;
}
QLineEdit {
    background: #151b28;
    border: 1px solid #242f42;
    border-radius: 8px;
    padding: 5px 10px;
    color: #ccd6ea;
    selection-background-color: #2b4a7a;
}
QLineEdit:hover {
    border-color: #33415c;
}
QLineEdit:focus {
    border-color: #4c8dff;
}
/* 空闲提示框的倒计时条：默认样式是纯蓝色实心方块，与圆角主题不搭 */
QProgressBar {
    background: #151b28;
    border: 1px solid #242f42;
    border-radius: 4px;
    color: #ccd6ea;
    text-align: center;
    font-size: 11px;
}
QProgressBar::chunk {
    background: #3d7dfa;
    border-radius: 3px;
}
QListWidget {
    background: #0a0c11;
    border: 1px solid #1d2432;
    border-radius: 10px;
    color: #cfd8ea;
    padding: 4px;
}
QListWidget::item {
    padding: 7px 8px;
    border-radius: 7px;
}
QListWidget::item:hover {
    background: rgba(255, 255, 255, 0.04);
}
QListWidget::item:selected {
    background: #1e2c44;
    color: #ffffff;
}
QComboBox, QTimeEdit {
    background: #151b28;
    border: 1px solid #242f42;
    border-radius: 8px;
    padding: 5px 10px;
    color: #ccd6ea;
    min-width: 80px;
}
QComboBox:hover, QTimeEdit:hover {
    border-color: #33415c;
}
QComboBox:focus, QTimeEdit:focus {
    border-color: #4c8dff;
}
QComboBox::drop-down, QTimeEdit::drop-down {
    subcontrol-origin: border;
    subcontrol-position: center right;
    width: 20px;
    background: transparent;
    border: none;
    border-left: 1px solid #242f42;
}
QComboBox::down-arrow, QTimeEdit::down-arrow {
    image: url("__ASSET_DIR__/chevron.svg");
    width: 9px;
    height: 6px;
}
QComboBox QAbstractItemView {
    background: #141a26;
    border: 1px solid #2a3547;
    color: #ccd6ea;
    selection-background-color: #1e2c44;
    outline: none;
}
QCheckBox {
    color: #9fb0cc;
    font-size: 12px;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #33405a;
    background: #131926;
}
QCheckBox::indicator:hover {
    border-color: #4a5a7a;
}
QCheckBox::indicator:checked {
    background: #3d7dfa;
    border-color: #3d7dfa;
}
QSplitter::handle {
    background: transparent;
    height: 8px;
}
QScrollBar:vertical {
    background: transparent;
    width: 11px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #2b3547;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #3d4d6b;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 11px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #2b3547;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #3d4d6b;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    width: 0;
    background: transparent;
}
QToolTip {
    background: #1a2130;
    color: #dfe6f5;
    border: 1px solid #33405a;
    padding: 4px 8px;
    border-radius: 6px;
}
QDialog, QMessageBox {
    background: #131826;
    color: #dbe3f2;
}
QMessageBox QLabel {
    color: #dbe3f2;
    font-size: 13px;
}
QMenu {
    background: #141a26;
    border: 1px solid #2a3547;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    color: #c6d0e4;
    padding: 6px 22px 6px 14px;
    border-radius: 6px;
    font-size: 12px;
}
QMenu::item:selected {
    background: #1e2c44;
    color: #ffffff;
}
QWidget#sidebar {
    background: #0d1017;
    border-right: 1px solid #1a2130;
}
QLabel#navBrand {
    font-size: 15px;
    font-weight: 700;
    color: #ecf1fa;
}
QLabel#navSection {
    color: #5c667d;
    font-size: 11px;
    font-weight: 600;
    padding-left: 8px;
}
QPushButton#navItem {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 9px 14px 9px 18px;
    text-align: left;
    color: #8b97ad;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#navItem:hover {
    background: rgba(255, 255, 255, 0.045);
    color: #cdd7ea;
}
QPushButton#navItem:checked {
    background: #1a2334;
    color: #eef3fc;
}
QWidget#pageBar {
    background: #10141d;
    border: 1px solid #1d2432;
    border-radius: 12px;
}
QLabel#pageTitle {
    font-size: 15px;
    font-weight: 700;
    color: #e9eef9;
}
QLabel#pageState {
    font-size: 12px;
    color: #7f8aa3;
}
QLabel#pagePlaceholder {
    color: #5f6a82;
    font-size: 13px;
}
QLabel#sectionTitleCard {
    color: #b9c4da;
    font-size: 13px;
    font-weight: 700;
}
QLabel#hintLabel {
    color: #6b7690;
    font-size: 11px;
}
QSpinBox {
    background: #151b28;
    border: 1px solid #242f42;
    border-radius: 8px;
    padding: 5px 10px;
    color: #ccd6ea;
    min-width: 90px;
}
QSpinBox:hover {
    border-color: #33415c;
}
QSpinBox:focus {
    border-color: #4c8dff;
}
/* Qt 默认绘制 QSpinBox 的上下按钮和 QComboBox 的箭头时，会画成浅色立体块，
 * 在深色圆角输入框里像两个突兀的小方块越出边界（实测见 tools/ui_widget_probe.py）。
 * 这里把按钮背景设为透明，箭头改用 SVG 资源——CSS 三角形边框技巧在 QSS 里
 * 不生效（只会画出小方块），必须走 image。 */
QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 16px;
    background: transparent;
    border: none;
}
QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 16px;
    background: transparent;
    border: none;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background: rgba(255, 255, 255, 0.06);
}
QSpinBox::up-arrow {
    image: url("__ASSET_DIR__/arrow_up.svg");
    width: 8px;
    height: 5px;
}
QSpinBox::down-arrow {
    image: url("__ASSET_DIR__/arrow_down.svg");
    width: 8px;
    height: 5px;
}
"""


# 箭头图标目录。QSS 的 url() 用绝对路径最稳（相对路径依赖进程工作目录），
# 且必须 as_posix：Windows 反斜杠在 QSS 里会被当成转义符。
ASSET_DIR = (Path(__file__).resolve().parent / "assets").as_posix()
APP_QSS = APP_QSS.replace("__ASSET_DIR__", ASSET_DIR)


def apply_palette(app):
    """给未被 QSS 覆盖的原生部分（对话框、菜单、编辑选中态）铺一层深色。"""
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#0b0d12"))
    pal.setColor(QPalette.WindowText, QColor("#e8edf8"))
    pal.setColor(QPalette.Base, QColor("#0a0c11"))
    pal.setColor(QPalette.AlternateBase, QColor("#10141d"))
    pal.setColor(QPalette.Text, QColor("#d7deec"))
    pal.setColor(QPalette.Button, QColor("#1a2130"))
    pal.setColor(QPalette.ButtonText, QColor("#ccd6ea"))
    pal.setColor(QPalette.Highlight, QColor("#2b4a7a"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor("#1a2130"))
    pal.setColor(QPalette.ToolTipText, QColor("#dfe6f5"))
    pal.setColor(QPalette.PlaceholderText, QColor("#5c667d"))
    app.setPalette(pal)


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def fade_in_widget(widget, duration=200, delay=0):
    """淡入一个控件；动画结束后移除效果，避免常驻开销。"""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def _cleanup():
        anim.stop()
        widget.setGraphicsEffect(None)
        anim.deleteLater()

    anim.finished.connect(_cleanup)
    if delay > 0:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(delay, anim.start)
    else:
        anim.start()
    return anim


def fade_in_window(window, duration=260):
    """窗口整体淡入（windowOpacity 动画）。"""
    anim = QPropertyAnimation(window, b"windowOpacity", window)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    window.setWindowOpacity(0.0)
    anim.start()
    return anim


def add_shadow(widget, color=ACCENT, alpha=110, blur=18, dy=3):
    """给控件加柔和投影（用于主按钮，增加层次感）。"""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    effect = QGraphicsDropShadowEffect(widget)
    shadow_color = QColor(str(color)) if isinstance(color, str) else QColor(color)
    shadow_color.setAlpha(alpha)
    effect.setColor(shadow_color)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    widget.setGraphicsEffect(effect)
    return effect


# ---------------------------------------------------------------------------
# 按钮：颜色过渡 + 涟漪 + 按压下沉
# ---------------------------------------------------------------------------

_BUTTON_VARIANTS = {
    "default": {
        "bg": "#1a2130", "bg_hover": "#243048", "bg_press": "#151c2a",
        "border": "#2a3547", "border_hover": "#3f5173", "text": "#ccd6ea",
        "ripple": "#7fb0ff", "radius": 9.0, "gradient": 0.0,
    },
    "primary": {
        "bg": "#3d7dfa", "bg_hover": "#5594ff", "bg_press": "#2e6ae6",
        "border": "#3d7dfa", "border_hover": "#5594ff", "text": "#ffffff",
        "ripple": "#ffffff", "radius": 9.0, "gradient": 0.07,
    },
    "danger": {
        "bg": "#251821", "bg_hover": "#33212c", "bg_press": "#1e1319",
        "border": "#48242f", "border_hover": "#5e2f3d", "text": "#ff92a2",
        "ripple": "#ff92a2", "radius": 9.0, "gradient": 0.0,
    },
    "mini": {
        "bg": "#1a2130", "bg_hover": "#243048", "bg_press": "#151c2a",
        "border": "#2a3547", "border_hover": "#3f5173", "text": "#ccd6ea",
        "ripple": "#7fb0ff", "radius": 7.0, "gradient": 0.0,
    },
}


class AnimatedButton(QPushButton):
    """自绘按钮：悬停/按压颜色过渡，点击涟漪扩散 + 轻微下沉。

    背景、边框、涟漪全部代码绘制（覆盖 QSS 底色）；尺寸 hint 仍来自
    QSS 的 padding/font-size 规则，调用方保持原有 objectName 设置即可。
    """

    HOVER_MS = 140
    PRESS_IN_MS = 70
    PRESS_OUT_MS = 200
    RIPPLE_MS = 450

    def __init__(self, text="", variant="default", parent=None):
        super().__init__(text, parent)
        self._variant = _BUTTON_VARIANTS[variant]
        self._hover = 0.0
        self._press = 0.0
        self._ripple_center = QPointF()
        self._ripple = -1.0  # <0 表示无涟漪
        self.setCursor(Qt.PointingHandCursor)

        self._anim_hover = self._make_anim(self.HOVER_MS, self._set_hover)
        self._anim_press = self._make_anim(self.PRESS_IN_MS, self._set_press)
        self._anim_ripple = self._make_anim(self.RIPPLE_MS, self._set_ripple)

    def _make_anim(self, duration, setter):
        anim = QVariantAnimation(self)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda v: setter(float(v)))
        return anim

    def _run(self, anim, start, end, duration=None, easing=None):
        if end == start:
            return
        anim.stop()
        anim.setStartValue(start)
        anim.setEndValue(end)
        if duration is not None:
            anim.setDuration(duration)
        if easing is not None:
            anim.setEasingCurve(easing)
        anim.start()

    def _set_hover(self, v):
        self._hover = v
        self.update()

    def _set_press(self, v):
        self._press = v
        self.update()

    def _set_ripple(self, v):
        self._ripple = v
        self.update()

    def enterEvent(self, ev):
        if self.isEnabled():
            self._run(self._anim_hover, self._hover, 1.0)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._run(self._anim_hover, self._hover, 0.0)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.isEnabled():
            self._ripple_center = QPointF(ev.position())
            self._run(self._anim_ripple, 0.0, 1.0)
            self._run(self._anim_press, self._press, 1.0)
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._run(
            self._anim_press, self._press, 0.0,
            duration=self.PRESS_OUT_MS, easing=QEasingCurve.OutBack,
        )
        super().mouseReleaseEvent(ev)

    def changeEvent(self, ev):
        if ev.type() == QEvent.EnabledChange:
            if not self.isEnabled():
                self._run(self._anim_hover, self._hover, 0.0)
                self._run(self._anim_press, self._press, 0.0)
            self.update()
        super().changeEvent(ev)

    def paintEvent(self, ev):
        from PySide6.QtWidgets import QStyle, QStyleOptionButton

        spec = self._variant
        w, h = self.width(), self.height()
        radius = spec["radius"]

        if not self.isEnabled():
            bg = QColor("#161c28")
            border = QColor("#232c3d")
        else:
            bg = _mix(QColor(spec["bg"]), QColor(spec["bg_hover"]), self._hover)
            border = _mix(QColor(spec["border"]), QColor(spec["border_hover"]), self._hover)
            if self._press > 0:
                bg = _mix(bg, QColor(spec["bg_press"]), self._press)
                border = _mix(border, QColor(spec["bg_press"]), self._press * 0.6)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius, radius)

        if spec["gradient"] > 0 and self.isEnabled():
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, bg.lighter(100 + int(spec["gradient"] * 100)))
            grad.setColorAt(1.0, bg.darker(100 + int(spec["gradient"] * 100)))
            painter.fillPath(path, grad)
        else:
            painter.fillPath(path, bg)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)

        if self.hasFocus() and self.isEnabled():
            ring = QPainterPath()
            ring.addRoundedRect(QRectF(1.5, 1.5, w - 3.0, h - 3.0), radius, radius)
            focus_color = QColor(ACCENT)
            focus_color.setAlpha(150)
            painter.setPen(QPen(focus_color, 1.4))
            painter.drawPath(ring)

        if 0.0 <= self._ripple < 1.0 and self.isEnabled():
            p = self._ripple
            max_r = 0.75 * (w * w + h * h) ** 0.5
            radius_r = 4.0 + p * max_r
            alpha = int(110 * (1.0 - p) ** 1.4)
            if alpha > 0:
                ripple_color = QColor(spec["ripple"])
                ripple_color.setAlpha(alpha)
                painter.save()
                painter.setClipPath(path)
                painter.setPen(Qt.NoPen)
                painter.setBrush(ripple_color)
                painter.drawEllipse(self._ripple_center, radius_r, radius_r)
                painter.restore()

        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        painter.save()
        if self._press > 0:
            painter.translate(0.0, self._press)
        self.style().drawControl(QStyle.CE_PushButtonLabel, opt, painter, self)
        painter.restore()


# ---------------------------------------------------------------------------
# 平滑滚动
# ---------------------------------------------------------------------------

class SmoothScroller(QObject):
    """给 QAbstractScrollArea 装上带缓动的滚轮滚动。

    拦截 viewport 的 Wheel 事件，用 QPropertyAnimation 缓动滚动条；
    连续滚动时目标值累加、动画从当前位置继续，不会跳变。
    """

    def __init__(self, area, duration=260, notches=3.0, parent=None):
        super().__init__(area if parent is None else parent)
        self._area = area
        self._duration = duration
        self._notches = float(notches)
        self._targets = {}
        self._anims = {}
        area.viewport().installEventFilter(self)

    def eventFilter(self, watched, ev):
        if ev.type() != QEvent.Wheel:
            return False
        delta = ev.angleDelta()
        vertical = abs(delta.y()) >= abs(delta.x())
        raw = delta.y() if vertical else delta.x()
        if vertical:
            bar = self._area.verticalScrollBar()
        else:
            bar = self._area.horizontalScrollBar()
        if raw == 0 or bar is None or bar.maximum() <= bar.minimum():
            return False
        step = max(bar.singleStep(), 1) * self._notches
        anim = self._anims.get(bar)
        if anim is None:
            anim = QPropertyAnimation(bar, b"value", bar)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            self._anims[bar] = anim
        base = self._targets.get(bar, bar.value())
        target = int(round(base - (raw / 120.0) * step))
        target = max(bar.minimum(), min(bar.maximum(), target))
        self._targets[bar] = target
        anim.stop()
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.setDuration(max(120, min(420, int(self._duration * (1.0 + abs(target - bar.value()) / max(step, 1) * 0.25)))))
        anim.start()
        return True


def enable_smooth_scroll(area):
    """给滚动区域挂平滑滚动；列表控件再切换到像素级滚动。"""
    SmoothScroller(area)
    if isinstance(area, QAbstractItemView):
        area.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        area.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)


# ---------------------------------------------------------------------------
# Tab：滑动指示条 + 页面淡入
# ---------------------------------------------------------------------------

class AnimatedTabBar(QTabBar):
    """切换 Tab 时指示条从旧位置滑到新位置。"""

    SLIDE_MS = 280

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDrawBase(False)
        self._indicator = QRectF()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.SLIDE_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self.currentChanged.connect(self._slide_to_current)

    def _on_value(self, value):
        self._indicator = QRectF(value)
        self.update()

    def _snap(self):
        index = self.currentIndex()
        self._anim.stop()
        if 0 <= index < self.count():
            self._indicator = QRectF(self.tabRect(index))
        self.update()

    def _slide_to_current(self, index):
        if index < 0 or self.count() == 0:
            return
        rect = QRectF(self.tabRect(index))
        if rect.isNull():
            return
        if not self._indicator.isValid():
            self._indicator = rect
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._indicator)
        self._anim.setEndValue(rect)
        self._anim.start()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._snap()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._snap()

    def tabInserted(self, index):
        super().tabInserted(index)
        self._snap()

    def tabRemoved(self, index):
        super().tabRemoved(index)
        self._snap()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        index = self.currentIndex()
        if index < 0 or not self._indicator.isValid() or self._indicator.isNull():
            return
        if self._indicator.bottom() > self.height():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        tab = self._indicator
        bar_w = tab.width() * 0.62
        bar_rect = QRectF(
            tab.center().x() - bar_w / 2.0,
            self.height() - 3.5,
            bar_w,
            3.0,
        )
        glow = QColor(ACCENT)
        glow.setAlpha(70)
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawRoundedRect(
            QRectF(bar_rect.left(), bar_rect.top() - 1.5, bar_rect.width(), bar_rect.height() + 1.5),
            2.0, 2.0,
        )
        painter.setBrush(QColor(ACCENT))
        painter.drawRoundedRect(bar_rect, 1.5, 1.5)


class AnimatedTabWidget(QTabWidget):
    """AnimatedTabBar + 页面切换淡入。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(AnimatedTabBar())
        self.currentChanged.connect(self._fade_current)

    def _fade_current(self, index):
        page = self.widget(index)
        if page is not None:
            fade_in_widget(page, duration=180)


# ---------------------------------------------------------------------------
# 状态点：呼吸脉冲
# ---------------------------------------------------------------------------

class StatusDot(QWidget):
    """运行中/卡死时呼吸脉冲的状态点。"""

    TICK_MS = 33

    def __init__(self, color="#7d879c", pulse=False, period=1.6, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._color = QColor(str(color))
        self._pulse = bool(pulse)
        self._period = float(period)
        self._t0 = time.monotonic()
        self._phase = 0.0
        self._timer = None

    def _ensure_timer(self):
        if self._timer is None:
            from PySide6.QtCore import QTimer

            self._timer = QTimer(self)
            self._timer.setInterval(self.TICK_MS)
            self._timer.timeout.connect(self._tick)
        return self._timer

    def _tick(self):
        elapsed = (time.monotonic() - self._t0) % self._period
        self._phase = 0.5 - 0.5 * math.cos(2.0 * math.pi * elapsed / self._period)
        self.update()

    def set_state(self, color, pulse, period=None):
        target = QColor(str(color))
        changed = target != self._color or pulse != self._pulse
        self._color = target
        self._pulse = bool(pulse)
        if period is not None:
            self._period = float(period)
        timer = self._ensure_timer()
        if self._pulse:
            if changed or not timer.isActive():
                self._t0 = time.monotonic()
                timer.start()
        else:
            timer.stop()
            self._phase = 0.0
        self.update()

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        color = QColor(self._color)

        halo = QColor(color)
        halo.setAlpha(46)
        painter.setBrush(halo)
        painter.drawEllipse(center, 7.5, 7.5)

        if self._pulse and self._phase > 0.01:
            ring = QColor(color)
            ring.setAlpha(int(150 * (1.0 - self._phase)))
            painter.setBrush(ring)
            ring_r = 4.0 + self._phase * 5.0
            painter.drawEllipse(center, ring_r, ring_r)

        painter.setBrush(color)
        painter.drawEllipse(center, 3.6, 3.6)

        core = QColor("#ffffff")
        core.setAlpha(110)
        painter.setBrush(core)
        painter.drawEllipse(QPointF(center.x() - 1.0, center.y() - 1.2), 1.1, 1.1)


# ---------------------------------------------------------------------------
# 状态胶囊：颜色渐变
# ---------------------------------------------------------------------------

class AnimatedPill(QWidget):
    """背景/边框/文字颜色平滑过渡的状态胶囊（替代 QLabel#pill）。"""

    def __init__(self, text="", color="#7d879c", parent=None):
        super().__init__(parent)
        self._text = str(text)
        self._cur = QColor(str(color))
        self._target = QColor(str(color))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(12)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        # 高度必须固定。默认 Preferred 让它在 QVBoxLayout 里被纵向拉满——
        # 桌面小组件（gui/widget.py）里胶囊就摊成了一个竖向椭圆的怪物。
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_value)

    def _on_value(self, value):
        self._cur = QColor(value)
        self.update()

    def set_state(self, text, color):
        self._text = str(text)
        target = QColor(str(color))
        self.updateGeometry()
        if target == self._target:
            # 状态未变（如 300ms 轮询重复设置）：不打断进行中的过渡动画
            self.update()
            return
        self._target = target
        self._anim.stop()
        self._anim.setStartValue(QColor(self._cur))
        self._anim.setEndValue(target)
        self._anim.start()
        self.update()

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._text) + 30, fm.height() + 12)

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0

        bg = QColor(self._cur)
        bg.setAlpha(36)
        border = QColor(self._cur)
        border.setAlpha(120)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, bg)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)

        painter.setPen(QColor(self._cur))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignCenter, self._text)


# ---------------------------------------------------------------------------
# 卡片：悬停边框过渡
# ---------------------------------------------------------------------------

class HoverGlowCard(QFrame):
    """悬停时边框颜色平滑过渡的卡片容器（配合 QSS 背景 #card 使用）。"""

    HOVER_MS = 160

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.HOVER_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_hover)

    def _set_hover(self, v):
        self._hover = float(v)
        self.update()

    def enterEvent(self, ev):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(0.0)
        self._anim.start()
        super().leaveEvent(ev)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._hover <= 0.01:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 13.0, 13.0)

        border = _mix(QColor("#212b3c"), QColor(ACCENT), self._hover * 0.75)
        painter.setPen(QPen(border, 1.2))
        painter.drawPath(path)

        highlight = QColor("#ffffff")
        highlight.setAlpha(int(9 * self._hover))
        top = QPainterPath()
        top.addRoundedRect(QRectF(rect.left() + 14.0, rect.top() + 0.5, rect.width() - 28.0, 1.6), 0.8, 0.8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(highlight)
        painter.drawPath(top)
