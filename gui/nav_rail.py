"""左侧导航栏：滑动指示条 + 分组导航项。"""

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QIcon, QPainter
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

from gui import icons, themes
from gui.modern import current_theme


class NavItem(QPushButton):
    # 图标与文字的间距、图标尺寸；_apply_elide 里算可用文字宽度时要一并扣掉
    ICON_SIZE = 17
    ICON_GAP = 9
    HOVER_MS = 150

    def __init__(self, key, label, parent=None):
        super().__init__("", parent)
        self.key = key
        self.setObjectName("navItem")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(36)
        self._dot = None
        self._icon_name = ""
        self._compact = False
        self._hover = 0.0
        self._anim_hover = QVariantAnimation(self)
        self._anim_hover.setDuration(self.HOVER_MS)
        self._anim_hover.setEasingCurve(QEasingCurve.OutCubic)
        self._anim_hover.valueChanged.connect(self._on_hover_value)
        # 默认图标色按当前主题取（写死 #8b97ad 时浅色主题的白侧栏上只有 2.95:1）
        self._icon_color = themes.icon_color(current_theme())
        # 文本自己裁：QPushButton 的 minimumSizeHint 等于整段文字宽度，注册表里
        # 写一个长 display_name，侧栏最小宽度就被顶到 1176px（实测），整个窗口
        # 的最小宽度跟着爆到 1927px。这里把 minimumSizeHint 压到下限，显示时
        # 按当前宽度加省略号，完整文本留在 text()/tooltip 里。
        self._full_label = ""
        self._text_drawn = ""
        self.setText(label)

    def _on_hover_value(self, value):
        self._hover = float(value)
        self.update()

    def enterEvent(self, ev):
        if self.isEnabled():
            self._anim_hover.stop()
            self._anim_hover.setStartValue(self._hover)
            self._anim_hover.setEndValue(1.0)
            self._anim_hover.start()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._anim_hover.stop()
        self._anim_hover.setStartValue(self._hover)
        self._anim_hover.setEndValue(0.0)
        self._anim_hover.start()
        super().leaveEvent(ev)

    def set_compact(self, compact):
        """切换紧凑纯图标模式（窄屏/移动端宽度下收起文字，完整名称保留在 tooltip）。"""
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self.setStyleSheet("padding: 8px 0px; text-align: center;")
        else:
            self.setStyleSheet("")
        self._apply_elide()
        self.update()

    def setText(self, text):
        self._full_label = str(text)
        self.setToolTip(self._full_label)
        self._apply_elide()

    def text(self):
        return self._full_label

    def display_text(self):
        """屏幕上真正画出来的那段（可能带省略号）。测试用它判断有没有裁。"""
        return self._text_drawn

    def set_icon(self, name, color=None):
        """设置导航图标（gui/icons.py 里的名字）；名字为空就只显示文字。"""
        self._icon_name = str(name or "")
        new_color = str(color or themes.icon_color(current_theme()))
        if self._icon_name:
            self._icon_color = new_color
            self.setIcon(icons.make(self._icon_name, self._icon_color, self.ICON_SIZE))
            self.setIconSize(QSize(self.ICON_SIZE, self.ICON_SIZE))
        else:
            self._icon_color = new_color
            self.setIcon(QIcon())
        self._apply_elide()

    def set_icon_color(self, color):
        """主题切换后重画图标（图标是位图，不能靠 QSS 换色）。"""
        if not self._icon_name:
            return
        self.set_icon(self._icon_name, color)

    def _apply_elide(self):
        if self._compact and self._icon_name:
            self._text_drawn = ""
            super().setText("")
            return
        # QSS 里 QPushButton#navItem 的左右内边距是 16px / 12px
        width = self.width() - 16 - 12 - 8
        if self._icon_name:
            width -= self.ICON_SIZE + self.ICON_GAP
        if width <= 0 or not self._full_label:
            self._text_drawn = self._full_label
            super().setText(self._full_label)
            return
        metrics = self.fontMetrics()
        self._text_drawn = metrics.elidedText(self._full_label, Qt.ElideRight, width)
        super().setText(self._text_drawn)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_elide()

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(40 if self._compact else 64, hint.height())

    def set_dot(self, color):
        """右侧状态点（None 隐藏）。"""
        target = QColor(color) if color else None
        if target != self._dot:
            self._dot = target
            self.update()

    def paintEvent(self, ev):
        if not self.isChecked() and self._hover > 0.01:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            bg = QColor(themes.resolve(current_theme())["nav_checked_bg"])
            bg.setAlpha(int(145 * self._hover))
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0), 8.0, 8.0)
            painter.end()
        super().paintEvent(ev)
        if self._dot is None or not self._dot.alpha():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._dot)
        dot_x = self.width() - 10.0 if self._compact else self.width() - 20.0
        dot_y = 10.0 if self._compact else self.height() / 2.0
        painter.drawEllipse(QPointF(dot_x, dot_y), 3.6, 3.6)


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
        self._color = QColor(themes.resolve(themes.DEFAULT_THEME)["accent"])
        self.hide()

    def set_color(self, color):
        """主题切换时改指示条颜色（QSS 管不到自绘控件）。"""
        self._color = QColor(str(color or themes.resolve(themes.DEFAULT_THEME)["accent"]))
        self.update()

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 1.5, 1.5)


class NavRail(QWidget):
    """应用左侧导航。点击项发出 navigate(key)；set_active(key) 切换高亮。

    支持响应式紧凑模式（set_compact）：桌面端保持 196px 完整导航栏，窄窗口/移动端
    尺寸下自动收缩为 56px 纯图标侧栏，给右侧卡片与日志留出充足空间。
    """

    EXPANDED_WIDTH = 196
    COMPACT_WIDTH = 56

    navigate = Signal(str)

    def __init__(self, title="Auto-AFK", parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(self.EXPANDED_WIDTH)
        self._items = {}
        self._sections = []
        self._compact = False
        self._indicator_y = 0.0
        self._finished = False
        # 图标颜色（主题切换时由 set_icon_color 更新并重画所有导航项图标）
        self._icon_color = themes.icon_color(current_theme())
        # finish() 之后新增的导航项要插到尾部弹性空隙之前，这里记下它的位置
        self._stretch_index = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 16, 12, 14)
        root.setSpacing(4)
        self._root_layout = root

        brand = QHBoxLayout()
        brand.setSpacing(9)
        from gui.main import make_icon

        logo = QLabel()
        logo.setPixmap(make_icon().pixmap(30, 30))
        # 记下 logo 控件：主题切换要重画（图标是位图，QSS 换不掉底色）
        self._logo = logo
        brand_label = QLabel(title)
        brand_label.setObjectName("navBrand")
        self._brand_label = brand_label
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

    def add_item(self, key, label, section=False, icon=""):
        """按添加顺序追加导航项；section=True 渲染为分组小标题。

        icon 取 gui/icons.py 里的名字（"home"/"status"/"settings" …）；
        名字不认识时不显示图标，只留文字，不影响点击。
        """
        layout = self._layout()
        if section:
            cap = QLabel(label)
            cap.setObjectName("navSection")
            cap.setVisible(not self._compact)
            self._sections.append(cap)
            layout.addSpacing(8)
            layout.addWidget(cap)
            return
        item = NavItem(key, label)
        if icon:
            item.set_icon(icon, self._icon_color)
        if self._compact:
            item.set_compact(True)
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

    def set_icon_color(self, color, accent=None):
        """主题切换后重画全部导航图标（QIcon 是位图，QSS 管不到）。

        accent 一并传进来改左边那条滑动指示条：它也是自绘控件，QSS 里的
        $accent 换不到它身上，不跟着改就会在浅色主题下留一条深蓝。
        """
        self._icon_color = str(color or themes.icon_color(current_theme()))
        for item in self._items.values():
            item.set_icon_color(self._icon_color)
        indicator = getattr(self, "_indicator", None)
        if indicator is not None and accent:
            indicator.set_color(accent)
        logo = getattr(self, "_logo", None)
        if logo is not None:
            from gui.main import make_icon

            logo.setPixmap(make_icon().pixmap(30, 30))

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

    def is_compact(self):
        return self._compact

    def set_compact(self, compact):
        """响应式切换紧凑图标栏（窄窗口自动收缩，宽窗口自动展开）。"""
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self.setFixedWidth(self.COMPACT_WIDTH if compact else self.EXPANDED_WIDTH)
        if compact:
            self._root_layout.setContentsMargins(8, 14, 8, 12)
        else:
            self._root_layout.setContentsMargins(12, 16, 12, 14)
        self._brand_label.setVisible(not compact)
        for cap in self._sections:
            cap.setVisible(not compact)
        for item in self._items.values():
            item.set_compact(compact)
        active = self._group.checkedButton()
        if active is not None:
            self._indicator_y = float(active.y())
            self._move_indicator(self._indicator_y)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        active = self._group.checkedButton()
        if active is not None:
            self._indicator_y = float(active.y())
            self._move_indicator(self._indicator_y)
