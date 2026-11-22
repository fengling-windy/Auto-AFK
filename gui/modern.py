"""Auto-AFK GUI 现代主题与动效组件。

包含：
- APP_QSS_TEMPLATE / build_qss / apply_palette: 主题化 QSS 与基础调色板
  （颜色抽进 gui/themes.py 的设计令牌，深色/浅色共用同一份模板）
- AnimatedButton: 悬停/按压颜色过渡 + 点击涟漪 + 按压下沉
- SmoothScroller / enable_smooth_scroll: 滚轮平滑滚动（缓动动画）
- AnimatedTabBar / AnimatedTabWidget: Tab 切换滑动指示条 + 页面淡入
- StatusDot: 状态点呼吸脉冲
- AnimatedPill: 状态胶囊颜色渐变
- HoverGlowCard: 卡片悬停边框过渡
- CardBackground: 卡片背景（可选自定义背景图，带不透明度）
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
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGraphicsOpacityEffect,
    QFrame,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QWidget,
    QWidgetItem,
)

from gui import themes

ACCENT = "#4c8dff"


class ElidedLabel(QLabel):
    """文本过长时用省略号裁掉显示，但不把窗口最小宽度一起顶出去。

    真实缺陷（2026-11-06 实测）：在「项目注册表」里给某个 project 写了长
    display_name，三处标签的最小宽度一起爆掉——
        项目卡标题 QLabel#cardName   minimumSizeHint 1232px
        头部副标题 QLabel#subtitle    minimumSizeHint 1409px
        左侧导航项 QPushButton#navItem minimumSizeHint 1176px
    叠加后 MainWindow.minimumSizeHint().width() 变成 1927px：1920 的屏幕都
    放不下，窗口被 Qt 强行拉宽，右侧一整截跑到屏幕外，按钮点不到。
    QLabel 的 minimumSizeHint 默认等于整段文字宽度，所以只能靠自己把它压住：
    minimumSizeHint 只报一个下限宽度，实际显示时按当前宽度裁。完整文本留在
    tooltip / full_text() 里。
    """

    def __init__(self, text="", min_width=56, max_hint_width=260, parent=None):
        super().__init__(parent)
        self._full_text = str(text)
        self._min_width = int(min_width)
        # sizeHint 的上限：想让标签「尽量显示完整、放不下才裁」，sizeHint 就得
        # 报完整文本的宽度；但对卡片标题这类被放进 FlowLayout 的控件，sizeHint
        # 一大就等于告诉布局「这张卡必须这么宽」，折行算法会把整屏挤成一张卡。
        # 所以给一个上限，既能让副标题这种长文本拿到足够宽度，又不会反噬布局。
        self._max_hint_width = int(max_hint_width) or 0
        # 调用方显式设过的 tooltip（例如注册表里的完整 display_name）。
        # 显式设过就优先用；设成空串表示「交回给自动省略逻辑」。
        self._explicit_tooltip = ""
        super().setText(self._full_text)

    def setToolTip(self, text):
        self._explicit_tooltip = str(text or "")
        self._apply_elide()

    def setText(self, text):
        self._full_text = str(text)
        self._apply_elide()

    def text(self):
        return self._full_text

    def full_text(self):
        return self._full_text

    def display_text(self):
        """屏幕上真正画出来的那段（可能带省略号）。测试用它判断有没有裁。"""
        return QLabel.text(self)

    def _apply_elide(self):
        width = self.width()
        if width <= 0 or not self._full_text:
            QLabel.setText(self, self._full_text)
            return
        metrics = self.fontMetrics()
        shown = metrics.elidedText(self._full_text, Qt.ElideRight, max(0, width - 2))
        QLabel.setText(self, shown)
        # tooltip 优先用调用方显式给的那段（注册表里的完整 display_name）；被裁掉
        # 又没人给过，就把完整文本补上，否则用户没法看到全名。
        tip = self._explicit_tooltip
        if not tip and shown != self._full_text:
            tip = self._full_text
        QLabel.setToolTip(self, tip)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_elide()

    def minimumSizeHint(self):
        return QSize(self._min_width, super().minimumSizeHint().height())

    def sizeHint(self):
        hint = super().sizeHint()
        if self._max_hint_width > 0 and hint.width() > self._max_hint_width:
            return QSize(self._max_hint_width, hint.height())
        return hint


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

    def insertItem(self, index, item):
        """插到指定位置（拖动改顺序时用；QLayout 默认只提供 addItem）。"""
        index = max(0, min(int(index), len(self._items)))
        self._items.insert(index, item)

    def clear_items(self):
        """清空内部列表（只清列表，不销毁控件：重排序时控件要复用）。"""
        self._items = []

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

    # -- 顺序调整（卡片拖动排序）------------------------------------------
    def insert_widget(self, index, widget):
        """把控件插到指定序号（拖动改运行顺序时用）。

        QLayout 只有 addWidget（追加），没有 insertWidget；这里自己把 QWidgetItem
        插进内部列表，位置就直接决定了 FlowLayout 的排布顺序。
        """
        item = QWidgetItem(widget)
        self.addChildWidget(widget)
        if index < 0 or index > self._flow.count():
            index = self._flow.count()
        self._flow.insertItem(index, item)
        widget.show()
        self.updateGeometry()
        return index

    def index_of(self, widget):
        """控件在流式顺序里的序号（-1 表示不在里面）。"""
        for index in range(self._flow.count()):
            item = self._flow.itemAt(index)
            if item is not None and item.widget() is widget:
                return index
        return -1

    def ordered_widgets(self):
        """按当前排布顺序列出控件（隐藏的也算，顺序即运行顺序）。"""
        out = []
        for index in range(self._flow.count()):
            item = self._flow.itemAt(index)
            if item is not None and item.widget() is not None:
                out.append(item.widget())
        return out

    def set_order(self, widgets):
        """按给定顺序重排控件（未列出的保持在尾部原顺序）。

        只动内部列表顺序，不重建控件——重建会丢卡片的运行状态与信号连接。
        """
        wanted = [w for w in widgets if w is not None]
        pool = set(id(w) for w in wanted)
        rest = [w for w in self.ordered_widgets() if id(w) not in pool]
        order = wanted + rest
        items = [self._flow.itemAt(i) for i in range(self._flow.count())]
        by_widget = {}
        for item in items:
            if item is not None and item.widget() is not None:
                by_widget[id(item.widget())] = item
        rebuilt = [by_widget[id(w)] for w in order if id(w) in by_widget]
        # 保险：万一有控件没在 order 里（理论上不会），补回尾部避免丢项
        for item in items:
            if item is not None and item not in rebuilt:
                rebuilt.append(item)
        self._flow.clear_items()
        for item in rebuilt:
            self._flow.addItem(item)
        self.updateGeometry()


class ParallelLinks(QWidget):
    """在相邻的并行卡片之间画连接线。

    为什么单独一层：卡片自己是 QFrame（HoverGlowCard 还有发光 paintEvent），
    在卡片上画跨卡片的线会被卡片边界裁掉。这里用一张与卡片容器等大的透明
    覆盖层，鼠标事件穿透（WA_TransparentForMouseEvents），只在 paintEvent 里
    按当前卡片几何画曲线——卡片被拖到别处、窗口变窄折行，线都跟着走。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._groups = []          # [[QWidget, QWidget, ...], ...]
        self._color = QColor("#3d7dfa")
        self._active = None        # 正在编辑（还没成为正式一组）的那一组，画虚线

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def set_groups(self, groups, active=None):
        """groups: 每个并行组一个卡片列表（相邻的两张或多张）。"""
        self._groups = [[w for w in group if w is not None] for group in (groups or [])]
        self._active = [w for w in (active or []) if w is not None]
        self.update()

    def _centers(self):
        """返回 [(widget, QRectF)] —— 用卡片在覆盖层坐标系里的几何。"""
        out = []
        for group in self._groups:
            for widget in group:
                if widget is None:
                    continue
                out.append((widget, QRectF(widget.geometry())))
        return out

    def paintEvent(self, ev):
        if not self._groups:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for group in self._groups:
            rects = [QRectF(w.geometry()) for w in group if w is not None]
            if len(rects) < 2:
                continue
            is_active = bool(self._active) and all(w in self._active for w in group)
            for left, right in zip(rects, rects[1:]):
                self._draw_link(painter, left, right, is_active)

    def _draw_link(self, painter, left, right, dashed):
        """两张相邻卡片之间的连线：同行走弧线，折行时绕到下方。"""
        color = QColor(self._color)
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.RoundCap)
        if dashed:
            pen.setStyle(Qt.DashLine)
            pen.setDashPattern([4.0, 3.0])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        same_row = abs(left.center().y() - right.center().y()) < 4.0
        if same_row:
            y = min(left.bottom(), right.bottom()) - 6.0
            start = QPointF(left.right() - 2.0, y)
            end = QPointF(right.left() + 2.0, y)
            path = QPainterPath(start)
            span = max(8.0, end.x() - start.x())
            path.cubicTo(
                QPointF(start.x() + span * 0.35, y + 14.0),
                QPointF(end.x() - span * 0.35, y + 14.0),
                end,
            )
        else:
            start = QPointF(left.center().x(), left.bottom() - 2.0)
            end = QPointF(right.center().x(), right.top() + 2.0)
            path = QPainterPath(start)
            span = max(20.0, abs(end.y() - start.y()))
            path.cubicTo(
                QPointF(start.x(), start.y() + span * 0.5),
                QPointF(end.x(), end.y() - span * 0.5),
                end,
            )
        painter.drawPath(path)

        # 两端各点一个小圆，读起来像「接上了」
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for point in (path.pointAtPercent(0.0), path.pointAtPercent(1.0)):
            painter.drawEllipse(point, 3.0, 3.0)


APP_QSS_TEMPLATE = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    outline: none;
}
QMainWindow {
    background: $window;
}
/* 页面容器单独设透明：主窗口底板下垫了一层 CardBackground（可在设置里换成
   自定义背景图），容器若继续不透明会把图整片盖住，用户以为背景设置没生效。 */
QWidget#central {
    background: transparent;
}
QFrame#header {
    background: $panel;
    border: 1px solid $border;
    border-radius: 16px;
}
QLabel#title {
    font-size: 20px;
    font-weight: 700;
    color: $text_strong;
}
QLabel#subtitle {
    font-size: 12px;
    color: $text_muted;
}
QLabel#sectionTitle {
    color: $text_muted;
    font-size: 12px;
    font-weight: 600;
    padding-left: 2px;
}
QFrame#card {
    background: $card;
    border: 1px solid $card_border;
    border-radius: 14px;
}
QLabel#cardName {
    font-size: 14px;
    font-weight: 700;
    color: $text_strong;
}
QLabel#cardStatus {
    font-size: 13px;
    font-weight: 700;
}
QLabel#cardDetail {
    color: $text_muted;
    font-size: 11px;
}
QLabel#cardAttempt {
    color: $text_dim;
    font-size: 11px;
}
QLabel#cardGrip {
    background: transparent;
    padding: 0px;
}
QPushButton#cardClose {
    padding: 0px;
    border: none;
    background: transparent;
}
QPushButton#parallel {
    padding: 4px 9px;
    font-size: 11px;
    border-radius: 7px;
}
QPushButton {
    background: $control_bg;
    color: $text_control;
    border: 1px solid $control_border;
    border-radius: 9px;
    padding: 6px 14px;
    font-size: 12px;
}
QPushButton:hover {
    background: $control_bg_hover;
    border-color: $control_border_hover;
}
QPushButton:pressed {
    background: $control_bg_press;
}
QPushButton:disabled {
    color: $text_disabled;
    background: $disabled_bg;
}
QPushButton#primary {
    background: $accent;
    border: 1px solid $accent;
    color: $on_accent;
    font-weight: 700;
    padding: 7px 20px;
}
QPushButton#primary:hover {
    background: $accent_hover;
    border-color: $accent_hover;
}
QPushButton#danger {
    background: $danger_bg;
    border: 1px solid $danger_border;
    color: $danger_text;
}
QPushButton#danger:hover {
    background: $danger_bg_hover;
}
QPushButton#mini {
    padding: 4px 8px;
    font-size: 11px;
    border-radius: 7px;
}
QTabWidget::pane {
    border: 1px solid $border;
    border-radius: 12px;
    background: $panel;
    top: -1px;
}
QTabBar {
    background: transparent;
}
QTabBar::tab {
    background: transparent;
    color: $text_muted;
    padding: 9px 16px;
    margin-right: 4px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
}
QTabBar::tab:hover {
    color: $text_control;
    background: $overlay_subtle;
}
QTabBar::tab:selected {
    color: $text_strong;
}
QPlainTextEdit, QTextEdit {
    background: $input_bg_deep;
    border: 1px solid $border;
    border-radius: 10px;
    color: $text;
    selection-background-color: $selection;
    padding: 6px;
}
QLineEdit {
    background: $input_bg;
    border: 1px solid $input_border;
    border-radius: 8px;
    padding: 5px 10px;
    color: $text_control;
    selection-background-color: $selection;
}
QLineEdit:hover {
    border-color: $input_border_hover;
}
QLineEdit:focus {
    border-color: $accent_soft;
}
/* 空闲提示框的倒计时条：默认样式是纯蓝色实心方块，与圆角主题不搭 */
QProgressBar {
    background: $input_bg;
    border: 1px solid $input_border;
    border-radius: 4px;
    color: $text_control;
    text-align: center;
    font-size: 11px;
}
QProgressBar::chunk {
    background: $accent;
    border-radius: 3px;
}
QListWidget {
    background: $input_bg_deep;
    border: 1px solid $border;
    border-radius: 10px;
    color: $text_control;
    padding: 4px;
}
QListWidget::item {
    padding: 7px 8px;
    border-radius: 7px;
}
QListWidget::item:hover {
    background: $overlay_subtle;
}
QListWidget::item:selected {
    background: $selection;
    color: $on_selection;
}
QComboBox, QTimeEdit {
    background: $input_bg;
    border: 1px solid $input_border;
    border-radius: 8px;
    padding: 5px 10px;
    color: $text_control;
    min-width: 80px;
}
QComboBox:hover, QTimeEdit:hover {
    border-color: $input_border_hover;
}
QComboBox:focus, QTimeEdit:focus {
    border-color: $accent_soft;
}
/* 只给 QComboBox 画下拉箭头。QTimeEdit 是 QAbstractSpinBox 的子类，它的右侧是
 * 「上/下」一对堆叠按钮，用 ::down-arrow 一个 chevron 去套它，上面那个按钮仍由
 * Qt 默认绘制成实心三角，两个形状挤在一起还越出圆角（实测见 header 的定时启动框）。
 * QTimeEdit 的按钮在下面按 QSpinBox 的方式统一处理。 */
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: center right;
    width: 20px;
    background: transparent;
    border: none;
    border-left: 1px solid $input_border;
}
QComboBox::down-arrow {
    image: url("__ASSET_DIR__/chevron.svg");
    width: 9px;
    height: 6px;
}
QComboBox QAbstractItemView {
    background: $card;
    border: 1px solid $control_border;
    color: $text_control;
    selection-background-color: $selection;
    outline: none;
}
QCheckBox {
    color: $text_control;
    font-size: 12px;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid $input_border_hover;
    background: $input_bg;
}
QCheckBox::indicator:hover {
    border-color: $input_border_hover;
}
QCheckBox::indicator:checked {
    background: $accent;
    border-color: $accent;
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
    background: $scroll_handle;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: $scroll_handle_hover;
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
    background: $scroll_handle;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: $scroll_handle_hover;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    width: 0;
    background: transparent;
}
QToolTip {
    background: $tooltip_bg;
    color: $tooltip_text;
    border: 1px solid $input_border_hover;
    padding: 4px 8px;
    border-radius: 6px;
}
QDialog, QMessageBox {
    background: $panel;
    color: $text;
}
QMessageBox QLabel {
    color: $text;
    font-size: 13px;
}
QMenu {
    background: $card;
    border: 1px solid $control_border;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    color: $text_control;
    padding: 6px 22px 6px 14px;
    border-radius: 6px;
    font-size: 12px;
}
QMenu::item:selected {
    background: $selection;
    color: $on_selection;
}
QWidget#sidebar {
    background: $sidebar_bg;
    border-right: 1px solid $border;
}
QLabel#navBrand {
    font-size: 15px;
    font-weight: 700;
    color: $text_strong;
}
QLabel#navSection {
    color: $text_dim;
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
    color: $nav_text;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#navItem:hover {
    background: $overlay_nav;
    color: $nav_text_hover;
}
QPushButton#navItem:checked {
    background: $nav_checked_bg;
    color: $nav_text_checked;
}
QWidget#pageBar {
    background: $panel;
    border: 1px solid $border;
    border-radius: 12px;
}
QLabel#pageTitle {
    font-size: 15px;
    font-weight: 700;
    color: $text_strong;
}
QLabel#pageState {
    font-size: 12px;
    color: $text_muted;
}
QLabel#pagePlaceholder {
    color: $text_placeholder;
    font-size: 13px;
}
QLabel#sectionTitleCard {
    color: $section_title;
    font-size: 13px;
    font-weight: 700;
}
QLabel#hintLabel {
    color: $text_dim;
    font-size: 11px;
}
QSpinBox {
    background: $input_bg;
    border: 1px solid $input_border;
    border-radius: 8px;
    padding: 5px 10px;
    color: $text_control;
    min-width: 90px;
}
QSpinBox:hover {
    border-color: $input_border_hover;
}
QSpinBox:focus {
    border-color: $accent_soft;
}
/* Qt 默认绘制 QSpinBox / QTimeEdit 的上下按钮和 QComboBox 的箭头时，会画成浅色立体块，
 * 在深色圆角输入框里像两个突兀的小方块越出边界（实测见 tools/ui_widget_probe.py）。
 * 这里把按钮背景设为透明，箭头改用 SVG 资源——CSS 三角形边框技巧在 QSS 里
 * 不生效（只会画出小方块），必须走 image。
 * QTimeEdit 也在这组里：它是 QAbstractSpinBox 的子类，右侧同样是上下两个按钮。 */
QSpinBox::up-button, QTimeEdit::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 16px;
    background: transparent;
    border: none;
}
QSpinBox::down-button, QTimeEdit::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 16px;
    background: transparent;
    border: none;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QTimeEdit::up-button:hover, QTimeEdit::down-button:hover {
    background: $overlay_hover;
}
QSpinBox::up-arrow, QTimeEdit::up-arrow {
    image: url("__ASSET_DIR__/arrow_up.svg");
    width: 8px;
    height: 5px;
}
QSpinBox::down-arrow, QTimeEdit::down-arrow {
    image: url("__ASSET_DIR__/arrow_down.svg");
    width: 8px;
    height: 5px;
}
"""


# 箭头图标目录。QSS 的 url() 用绝对路径最稳（相对路径依赖进程工作目录），
# 且必须 as_posix：Windows 反斜杠在 QSS 里会被当成转义符。
ASSET_DIR = (Path(__file__).resolve().parent / "assets").as_posix()


def build_qss(theme=None):
    """按主题把令牌替换成真实颜色，返回可直接 setStyleSheet 的 QSS。

    历史：整个界面原先只有一份硬编码深色的 APP_QSS，加浅色主题只能复制
    400 行样式再逐色改。现在颜色都在 gui/themes.py 的令牌表里，模板只写
    ``$token``；未知令牌原样保留（Qt 会把整条声明当无效丢掉，不会崩，
    冒烟测试 check_theme() 会守住完整性）。
    """
    tokens = themes.resolve(theme)
    text = APP_QSS_TEMPLATE.replace("__ASSET_DIR__", ASSET_DIR)
    # 必须按令牌名长度倒序替换：$control_bg 是 $control_bg_hover 的前缀，
    # 先换短的会把后者变成 "#242f42_hover"（Qt 报 Unknown color name，
    # 整条声明被丢掉，表现为按钮悬停色失效）。
    for name in sorted(tokens, key=len, reverse=True):
        text = text.replace("$" + name, tokens[name])
    return text


# 深色 QSS，供不关心主题的调用方（历史代码、探针）沿用
APP_QSS = build_qss(themes.DEFAULT_THEME)


def apply_palette(app, theme=None):
    """给未被 QSS 覆盖的原生部分（对话框、菜单、编辑选中态）铺一层主题色。"""
    tokens = themes.resolve(theme)
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(tokens["window"]))
    pal.setColor(QPalette.WindowText, QColor(tokens["text_strong"]))
    pal.setColor(QPalette.Base, QColor(tokens["input_bg_deep"]))
    pal.setColor(QPalette.AlternateBase, QColor(tokens["panel"]))
    pal.setColor(QPalette.Text, QColor(tokens["text"]))
    pal.setColor(QPalette.Button, QColor(tokens["control_bg"]))
    pal.setColor(QPalette.ButtonText, QColor(tokens["text_control"]))
    pal.setColor(QPalette.Highlight, QColor(tokens["selection"]))
    pal.setColor(QPalette.HighlightedText, QColor(tokens["on_selection"]))
    pal.setColor(QPalette.ToolTipBase, QColor(tokens["tooltip_bg"]))
    pal.setColor(QPalette.ToolTipText, QColor(tokens["tooltip_text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(tokens["text_dim"]))
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


def add_shadow(widget, color=None, alpha=110, blur=18, dy=3):
    """给控件加柔和投影（用于主按钮，增加层次感）。

    默认色取当前主题的 accent：浅色主题下深色的 #4c8dff 投影会糊成一片。
    """
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    if color is None:
        color = accent_color()
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

# 按钮配色按主题给（gui/themes.py 的 BUTTONS）。这里保留深色默认表的别名，
# 老调用方/探针读 _BUTTON_VARIANTS 仍然拿到深色那一套。
_BUTTON_VARIANTS = themes.BUTTONS[themes.DEFAULT_THEME]

# 当前进程生效的主题。AnimatedButton 的颜色是构造时抓的快照，切换主题后
# 新建的按钮要用新表，所以记在模块级状态里，由 apply_theme() 更新。
_current_theme = themes.DEFAULT_THEME


def current_theme():
    return _current_theme


def accent_color():
    """当前主题的强调色（手绘部分用：焦点环、发光、连线）。

    QSS 里的 $accent 由 build_qss 替换，但 paintEvent 里手绘的颜色拿不到 QSS，
    历史上这些地方直接写死 ACCENT="#4c8dff"，切到浅色主题就会画出一层深色
    光晕。统一从这里取。
    """
    return themes.resolve(_current_theme)["accent"]


def links_color():
    """并行连线的颜色：比强调色更亮一档，深色背景上才看得清。"""
    return themes.resolve(_current_theme)["accent_hover"]


def apply_theme(app, theme):
    """切换全局主题：记状态 + 重设 QSS 与调色板。

    已存在的 AnimatedButton 不会自动换色（颜色在 paintEvent 里按构造时的快照
    画），需要调用方重建控件；MainWindow 在切换后会重建项目卡。
    """
    global _current_theme
    _current_theme = themes.normalize_theme(theme)
    if app is not None:
        app.setStyleSheet(build_qss(_current_theme))
        apply_palette(app, _current_theme)
    return _current_theme


def button_spec(variant):
    """按当前主题取按钮配色；未知 variant 回退 default。"""
    table = themes.button_variants(_current_theme)
    return table.get(variant) or table["default"]


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
        self._variant_name = variant
        self._variant = button_spec(variant)
        self._hover = 0.0
        self._press = 0.0
        self._ripple_center = QPointF()
        self._ripple = -1.0  # <0 表示无涟漪
        self.setCursor(Qt.PointingHandCursor)

        self._anim_hover = self._make_anim(self.HOVER_MS, self._set_hover)
        self._anim_press = self._make_anim(self.PRESS_IN_MS, self._set_press)
        self._anim_ripple = self._make_anim(self.RIPPLE_MS, self._set_ripple)

    def set_variant(self, variant):
        """换一套配色（主题切换后由界面调用，避免重建整个控件）。"""
        spec = button_spec(variant)
        if spec is not self._variant:
            self._variant = spec
            self._variant_name = variant
            self.update()

    @property
    def variant_name(self):
        return self._variant_name

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
            focus_color = QColor(accent_color())
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
        glow = QColor(accent_color())
        glow.setAlpha(70)
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawRoundedRect(
            QRectF(bar_rect.left(), bar_rect.top() - 1.5, bar_rect.width(), bar_rect.height() + 1.5),
            2.0, 2.0,
        )
        painter.setBrush(QColor(accent_color()))
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

        token = themes.resolve(_current_theme)
        border = _mix(QColor(token["card_border"]), QColor(accent_color()), self._hover * 0.75)
        painter.setPen(QPen(border, 1.2))
        painter.drawPath(path)

        highlight = QColor(token["text_strong"])
        highlight.setAlpha(int(9 * self._hover))
        top = QPainterPath()
        top.addRoundedRect(QRectF(rect.left() + 14.0, rect.top() + 0.5, rect.width() - 28.0, 1.6), 0.8, 0.8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(highlight)
        painter.drawPath(top)


class CardBackground(QWidget):
    """界面背景：纯色（QSS 的 $window）之上可选铺一张自定义背景图。

    为什么不做成 central 的 paintEvent：central 上叠着导航栏、卡片、页签，
    在它自己身上画图要先铺底再让子控件透明，Qt 的 QSS 背景与手绘混在一起
    很容易出现「一半黑一半图」。改成在 central 里垫一层独立控件、放在最底层
    （raise 之后再用 stackUnder），主题切换只改这一层的颜色。

    ``opacity`` 是背景图的不透明度（0-100）：压得太实字就看不清了，所以要能调。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._pixmap = None
        self._opacity = 100
        self._fill = QColor(themes.DARK["window"])

    def set_image(self, path):
        """设置背景图路径；空/不存在/解码失败都按「没有背景图」处理。"""
        self._pixmap = None
        text = str(path or "").strip()
        if text:
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = Path(__file__).resolve().parents[1] / text
            if candidate.is_file():
                pix = QPixmap(str(candidate))
                if not pix.isNull():
                    self._pixmap = pix
        self.update()

    def image_path_loaded(self):
        return self._pixmap is not None

    def set_opacity(self, percent):
        try:
            self._opacity = max(0, min(100, int(percent)))
        except (TypeError, ValueError):
            self._opacity = 100
        self.update()

    def set_fill(self, color):
        self._fill = QColor(color)
        self.update()

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._fill)
        if self._pixmap is None or self._opacity <= 0:
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setOpacity(self._opacity / 100.0)
        # 等比铺满（裁掉超出的部分），不拉伸变形
        scaled = self._pixmap.scaled(
            self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        x = (scaled.width() - self.width()) // 2
        y = (scaled.height() - self.height()) // 2
        painter.drawPixmap(-x, -y, scaled)

