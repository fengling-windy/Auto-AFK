"""左侧导航栏：滑动指示条 + 分组导航项。"""

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter
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
    """导航项左侧的滑动高亮条。

    注意：不能一构造就 show()。QWidget 子对象的默认几何是 100x30 @ (0,0)，
    在任何导航项被选中之前就可见，会画出一块蓝色压在左上角的品牌 Logo 上
    （真实启动时的首帧、以及离屏预览截图里都能看到）。改为由
    NavRail._move_indicator() 在拿到真实位置后再 show。
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

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
        self._anim = None

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
            # finish() 之后动态追加（如运行中注册的新项目）。
            # 注意这里不能再次 connect buttonClicked：__init__ 里已经连过一次，
            # 重复连接会让一次点击发出两遍 navigate。
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
        # 拿到真实位置后才显示（见 _NavIndicator 的说明）
        if item is not None and not self._indicator.isVisible():
            self._indicator.show()

    def _animate_indicator_to(self, y):
        if abs(y - self._indicator_y) < 0.5:
            self._move_indicator(y)
            return
        # 同一时间只留一个动画：快速连点导航项时，旧动画还在跑并被 valueChanged
        # 持续推进，新动画也从 _indicator_y 起步，两条交错会让指示条抖动/跳回。
        self._stop_indicator_anim()
        anim = QVariantAnimation(self)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self._indicator_y)
        anim.setEndValue(y)
        anim.valueChanged.connect(lambda v: self._move_indicator(float(v)))
        anim.finished.connect(anim.deleteLater)
        anim.finished.connect(self._clear_indicator_anim)
        self._anim = anim
        anim.start()

    def _stop_indicator_anim(self):
        anim = self._anim
        self._anim = None
        if anim is not None:
            anim.stop()
            # stop() 不会发 finished（finished 只在自然跑到终点时发），
            # 所以这里要自己回收，否则快速连点会攒下一堆孤儿动画对象。
            anim.deleteLater()

    def _clear_indicator_anim(self):
        self._anim = None

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        active = self._group.checkedButton()
        if active is not None:
            self._indicator_y = float(active.y())
            self._move_indicator(self._indicator_y)
