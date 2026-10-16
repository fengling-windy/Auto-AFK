"""左侧导航栏：滑动指示条 + 分组导航项。"""

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget


class NavItem(QPushButton):
    def __init__(self, key, label, parent=None):
        super().__init__(label, parent)
        self.key = key
        self.setObjectName("navItem")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(38)
        self._dot = None

    def set_dot(self, color):
        """右侧状态点（None 隐藏）。"""
        self._dot = QColor(color) if color else None
        self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._dot is None or not self._dot.alpha():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._dot)
        painter.drawEllipse(QPointF(self.width() - 20.0, self.height() / 2.0), 4.0, 4.0)


class _NavIndicator(QWidget):
    """导航项左侧的滑动高亮条。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.show()

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#4c8dff"))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 1.5, 1.5)


class NavRail(QWidget):
    """应用左侧导航。点击项发出 navigate(key)；set_active(key) 切换高亮。"""

    navigate = Signal(str)

    def __init__(self, title="Auto-AFK", parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(196)
        self._items = {}
        self._indicator_y = 0.0
        self._finished = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 16, 12, 14)
        root.setSpacing(4)

        brand = QHBoxLayout()
        brand.setSpacing(9)
        from gui.main import make_icon

        logo = QLabel()
        logo.setPixmap(make_icon().pixmap(30, 30))
        brand_label = QLabel(title)
        brand_label.setObjectName("navBrand")
        brand.addWidget(logo)
        brand.addWidget(brand_label)
        brand.addStretch(1)
        root.addLayout(brand)
        root.addSpacing(12)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.buttonClicked.connect(lambda btn: self.navigate.emit(btn.key))
        self._indicator = _NavIndicator(self)

    def add_item(self, key, label, section=False):
        """按添加顺序追加导航项；section=True 渲染为分组小标题。"""
        layout = self.layout()
        if section:
            cap = QLabel(label)
            cap.setObjectName("navSection")
            layout.addSpacing(8)
            layout.addWidget(cap)
            return
        item = NavItem(key, label)
        self._items[key] = item
        self._group.addButton(item)
        layout.addWidget(item)
        if self._finished:
            # finish() 之后动态追加（如运行中注册的新项目）
            self._group.buttonClicked.connect(lambda btn: self.navigate.emit(btn.key))
            item_y = float(item.y())
            if self._group.checkedButton() is item:
                self._move_indicator(item_y)

    def finish(self):
        """在所有导航项之后补上尾部弹性空隙；必须最后调用。"""
        self.layout().addStretch(1)
        self._finished = True
        active = self._group.checkedButton()
        if active is not None:
            self._indicator_y = float(active.y())
            self._move_indicator(self._indicator_y)

    def set_active(self, key, animate=True):
        item = self._items.get(key)
        if item is None:
            return
        item.setChecked(True)
        y = float(item.y())
        if animate and self._finished:
            self._animate_indicator_to(y)
        else:
            self._move_indicator(y)

    def active_key(self):
        checked = self._group.checkedButton()
        for key, item in self._items.items():
            if item is checked:
                return key
        return None

    def item_for_key(self, key):
        return self._items.get(key)

    def _move_indicator(self, y):
        self._indicator_y = y
        item = self._group.checkedButton()
        height = item.height() - 16 if item is not None else 24
        self._indicator.setGeometry(
            QRectF(4, y + 8, 3.0, max(16.0, height)).toAlignedRect()
        )

    def _animate_indicator_to(self, y):
        if abs(y - self._indicator_y) < 0.5:
            self._move_indicator(y)
            return
        anim = QVariantAnimation(self)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self._indicator_y)
        anim.setEndValue(y)
        anim.valueChanged.connect(lambda v: self._move_indicator(float(v)))
        anim.finished.connect(anim.deleteLater)
        anim.start()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        active = self._group.checkedButton()
        if active is not None:
            self._indicator_y = float(active.y())
            self._move_indicator(self._indicator_y)
