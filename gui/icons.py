"""程序内绘制的线性图标（不依赖外部资源文件）。

为什么不用 SVG/PNG：图标要跟着主题换色（深色浅色两套），而 QSS 的
``url(...)`` 只能引用固定文件；用 QPainter 现画既能让颜色跟随主题，也避免
一堆小图文件散在 repo 里。每个图标都在 24x24 的网格上按 2px 描边绘制，
缩放到 16/18/20 都清晰。
"""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from gui import themes


def _canvas(size=24, scale=3):
    pix = QPixmap(size * scale, size * scale)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(scale, scale)
    return pix, painter


def _pen(painter, color, width=2.0):
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)


def _icon(pix):
    return QIcon(pix)


def default_color(subtle=False):
    """没指定颜色时用的颜色：取当前主题的图标色。

    历史缺陷：各 icon_* 的默认参数写死成 #c8d3e8 / #8b97ad（深色主题的值）。
    调用方一旦漏传颜色，浅色主题的白卡上就是一枚 1.5:1 的淡图标。改成按主题
    解析后，漏传也安全。
    """
    from gui.modern import current_theme

    return themes.icon_color(current_theme(), subtle=subtle)


def icon_home(color=None):
    """总览：房子。"""
    color = color or default_color()
    pix, p = _canvas()
    _pen(p, color)
    path = QPainterPath()
    path.moveTo(QPointF(3.5, 11.0))
    path.lineTo(QPointF(12.0, 4.0))
    path.lineTo(QPointF(20.5, 11.0))
    p.drawPath(path)
    p.drawPolyline([QPointF(6.0, 10.5), QPointF(6.0, 20.0),
                    QPointF(18.0, 20.0), QPointF(18.0, 10.5)])
    p.drawLine(QPointF(10.0, 20.0), QPointF(10.0, 14.5))
    p.drawLine(QPointF(14.0, 20.0), QPointF(14.0, 14.5))
    p.drawLine(QPointF(10.0, 14.5), QPointF(14.0, 14.5))
    p.end()
    return _icon(pix)


def icon_status(color=None):
    """项目状态：仪表盘 + 刻度。"""
    color = color or default_color()
    pix, p = _canvas()
    _pen(p, color)
    arc = QRectF(4.0, 5.5, 16.0, 16.0)
    p.drawArc(arc, 0 * 16, 180 * 16)
    p.drawLine(QPointF(4.0, 13.5), QPointF(20.0, 13.5))
    p.drawLine(QPointF(12.0, 13.5), QPointF(16.5, 8.5))
    p.drawLine(QPointF(6.0, 17.0), QPointF(6.0, 19.5))
    p.drawLine(QPointF(12.0, 17.5), QPointF(12.0, 20.0))
    p.drawLine(QPointF(18.0, 17.0), QPointF(18.0, 19.5))
    p.end()
    return _icon(pix)


def icon_settings(color=None):
    """设置：齿轮（8 齿）。"""
    color = color or default_color()
    import math

    pix, p = _canvas()
    _pen(p, color)
    p.drawEllipse(QRectF(8.6, 8.6, 6.8, 6.8))
    p.drawEllipse(QRectF(4.4, 4.4, 15.2, 15.2))
    for i in range(8):
        angle = math.radians(i * 45.0)
        inner_x = 12.0 + math.cos(angle) * 7.6
        inner_y = 12.0 + math.sin(angle) * 7.6
        outer_x = 12.0 + math.cos(angle) * 10.2
        outer_y = 12.0 + math.sin(angle) * 10.2
        p.drawLine(QPointF(inner_x, inner_y), QPointF(outer_x, outer_y))
    p.end()
    return _icon(pix)


def icon_plus(color=None):
    """添加。"""
    color = color or default_color()
    pix, p = _canvas()
    _pen(p, color)
    p.drawLine(QPointF(12.0, 5.0), QPointF(12.0, 19.0))
    p.drawLine(QPointF(5.0, 12.0), QPointF(19.0, 12.0))
    p.end()
    return _icon(pix)


def icon_close(color=None):
    """删除。"""
    color = color or default_color()
    pix, p = _canvas()
    _pen(p, color)
    p.drawLine(QPointF(7.0, 7.0), QPointF(17.0, 17.0))
    p.drawLine(QPointF(17.0, 7.0), QPointF(7.0, 17.0))
    p.end()
    return _icon(pix)


def icon_link(color=None):
    """并行连接：两个圆 + 中间链条。"""
    color = color or default_color()
    pix, p = _canvas()
    _pen(p, color)
    p.drawEllipse(QRectF(2.5, 8.5, 7.0, 7.0))
    p.drawEllipse(QRectF(14.5, 8.5, 7.0, 7.0))
    p.drawLine(QPointF(9.5, 12.0), QPointF(14.5, 12.0))
    p.end()
    return _icon(pix)


def icon_file(color=None):
    """配置文件：文档。"""
    color = color or default_color(subtle=True)
    pix, p = _canvas()
    _pen(p, color)
    path = QPainterPath()
    path.moveTo(QPointF(6.0, 3.5))
    path.lineTo(QPointF(14.0, 3.5))
    path.lineTo(QPointF(18.5, 8.0))
    path.lineTo(QPointF(18.5, 20.5))
    path.lineTo(QPointF(6.0, 20.5))
    path.closeSubpath()
    p.drawPath(path)
    p.drawPolyline([QPointF(14.0, 3.5), QPointF(14.0, 8.0), QPointF(18.5, 8.0)])
    p.drawLine(QPointF(9.0, 12.5), QPointF(15.5, 12.5))
    p.drawLine(QPointF(9.0, 16.0), QPointF(15.5, 16.0))
    p.end()
    return _icon(pix)


def icon_grip(color=None):
    """拖动把手：两列点。"""
    color = color or default_color(subtle=True)
    pix, p = _canvas()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    for row in range(3):
        for col in range(2):
            p.drawEllipse(QPointF(9.5 + col * 5.0, 7.0 + row * 5.0), 1.5, 1.5)
    p.end()
    return _icon(pix)


ICONS = {
    "home": icon_home,
    "status": icon_status,
    "settings": icon_settings,
    "plus": icon_plus,
    "close": icon_close,
    "link": icon_link,
    "file": icon_file,
    "grip": icon_grip,
}


def make(name, color=None, size=18):
    """取一个图标；未知名字返回空 QIcon（界面照常显示文字）。

    color 留空时交给各图标自己按当前主题取默认色（见 default_color）：
    把手/文档这类次要图标用更淡的一档，其余用正常图标色。
    """
    factory = ICONS.get(name)
    if factory is None:
        return QIcon()
    icon = factory(color)
    return QIcon(icon.pixmap(QSize(size * 3, size * 3)))
