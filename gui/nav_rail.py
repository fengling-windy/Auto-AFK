"""左侧导航栏：滑动指示条 + 分组导航项。"""

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


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
        # finish() 之后新增的导航项要插到尾部弹性空隙之前，这里记下它的位置
        self._stretch_index = 0

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
        self._anim = None

        # 导航项放在可滚动区域里。用普通布局时，侧栏的最小高度等于所有导航项
        # 高度之和，项目一多就把主窗口最小高度顶上去——实测注册表里有 25 个
        # 带界面的项目时 minimumSizeHint 变成 1122px，1080p 屏上「设置」入口
        # 直接跑到屏幕外点不到。套滚动区后最小高度不再随项目数增长。
        self._scroll = QScrollArea()
        self._scroll.setObjectName("navScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("QScrollArea#navScroll { background: transparent; border: none; }")
        self._list = QWidget()
        self._list.setObjectName("navList")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._scroll.setWidget(self._list)
        root.addWidget(self._scroll, 1)
        # 指示条挂在滚动内容上（不是侧栏本身）：item.y() 是内容坐标系，
        # 挂在外面的话滚动之后就指错位置了。
        self._indicator = _NavIndicator(self._list)

    def _layout(self):
        """导航项所在的布局（滚动区里的那一层）。"""
        return self._list_layout

    def add_item(self, key, label, section=False):
        """按添加顺序追加导航项；section=True 渲染为分组小标题。"""
        layout = self._layout()
        if section:
            cap = QLabel(label)
            cap.setObjectName("navSection")
            layout.addSpacing(8)
            layout.addWidget(cap)
            return
        item = NavItem(key, label)
        self._items[key] = item
        self._group.addButton(item)
        if self._finished:
            # finish() 之后动态追加（如运行中在注册表里新增了项目）。
            # 必须插到尾部弹性空隙之前：直接 addWidget 会落到 stretch 后面，
            # 新项被顶到侧栏最底部、和分组内容脱节。项目入口还要排到「设置」
            # 前面——用户新加的项目出现在「设置」下方会让人以为它不属于项目列表。
            anchor = self._items.get("settings") if key.startswith("page:") else None
            index = self._index_of(anchor) if anchor is not None else -1
            if index < 0:
                index = self._stretch_index
            layout.insertWidget(index, item)
            self._stretch_index = layout.count() - 1
            # 注意这里不能再次 connect buttonClicked：__init__ 里已经连过一次，
            # 重复连接会让一次点击发出两遍 navigate。
            if self._group.checkedButton() is item:
                self._move_indicator(float(item.y()))
        else:
            layout.addWidget(item)

    def finish(self):
        """在所有导航项之后补上尾部弹性空隙；必须最后调用。"""
        self._layout().addStretch(1)
        self._stretch_index = self._layout().count() - 1
        self._finished = True
        active = self._group.checkedButton()
        if active is not None:
            self._indicator_y = float(active.y())
            self._move_indicator(self._indicator_y)

    def _index_of(self, item):
        """导航项在当前布局里的下标（找不到返回 -1）。

        不用 QLayout.indexOf(widget)：PySide6 里它对 QWidget 重载的返回不可靠
        （实测已有的导航项也返回 -1，新增项会被插到「设置」后面）。
        """
        if item is None:
            return -1
        layout = self._layout()
        for index in range(layout.count()):
            entry = layout.itemAt(index)
            if entry is not None and entry.widget() is item:
                return index
        return -1

    def remove_item(self, key):
        """移除一个导航项（注册表里删除了某个项目）。"""
        item = self._items.pop(key, None)
        if item is None:
            return
        was_checked = self._group.checkedButton() is item
        self._group.removeButton(item)
        self._layout().removeWidget(item)
        item.setParent(None)
        item.deleteLater()
        if self._finished:
            self._stretch_index = self._layout().count() - 1
        if was_checked:
            # 选中项被删掉了：指示条无处可落，先藏起来，等 set_active 重新定位
            self._indicator.hide()
            self._stop_indicator_anim()

    def item_keys(self):
        return list(self._items.keys())

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
        self._ensure_item_visible(item)

    def _ensure_item_visible(self, item):
        """把选中的导航项滚进视口。

        历史缺陷：导航项放进 QScrollArea（项目一多时不再顶高窗口最小高度）之后，
        选中一个靠后的项目，侧栏滚动位置仍停在顶部，高亮和指示条都落在视口外，
        用户只看得到「项目00…项目15」——看起来像点了没反应。实测注册表 25 个
        带界面项目时导航到 P20，scroll value 仍是 0，item 在视口内 y=882。
        """
        if item is None:
            return
        try:
            self._scroll.ensureWidgetVisible(item, 0, 8)
        except RuntimeError:
            # 项刚被 deleteLater 的情况，忽略即可
            return

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
        # 高度必须跟着导航项走，但 item.height() 在布局跑过之前是不可信的
        # （QWidget 默认高度可能几百像素，实测一次进来是 480，指示条就被画成
        # 464px 一根贯穿整个侧栏的竖条）。超过 minimumHeight 太多就退回最小高度。
        target = item.minimumHeight() if item is not None else 24
        if item is not None and 0 < item.height() <= target + 8:
            target = item.height()
        height = max(16.0, float(target) - 16.0)
        self._indicator.setGeometry(
            QRectF(4, y + 8, 3.0, height).toAlignedRect()
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
        anim.finished.connect(self._snap_indicator_to_active)
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

    def _snap_indicator_to_active(self):
        """动画结束时把指示条对齐到当前选中项的**实际**位置。

        valueChanged 的终点是动画启动那一刻记下的 item.y()，而导航项在动画
        运行期间还会因为布局重排（窗口 resize、分组标题出现）改变位置：实测
        出现过动画停在 182、选中项实际已在 132——指示条落在两个导航项之间，
        看起来像「高亮错了」。收尾时按当前几何重新定位一次。
        """
        self._clear_indicator_anim()
        active = self._group.checkedButton()
        if active is None:
            return
        self._move_indicator(float(active.y()))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        active = self._group.checkedButton()
        if active is not None:
            self._indicator_y = float(active.y())
            self._move_indicator(self._indicator_y)
