"""配置里的数值兜底：坏值告警 + 回退默认，绝不抛。

为什么需要：settings.yaml / afk_projects.yaml 里的数值项用户随手就能改坏
（``idle.minutes: 半小时``、``chain.retry: 两次``、``delay_sec: abc``），
而读它们的地方大量直接 ``int(...)`` / ``float(...)``：

* 在 ``SettingsPage.__init__`` 里 → 设置页造不出来，主窗口跟着起不来，
  用户只能手工去改 yaml（2026-11-23 实测 ``post_actions.delay_sec: abc``
  与 ``idle.minutes: abc`` 都能把整个界面打死）。
* 在 ``supervisor._supervise`` 这个后台线程里 → 线程抛异常直接退出，
  那次运行永远停在「运行中」，界面看不出任何线索。

所以统一走这里：能转就转，转不了写一条 warning 并用默认值，永远返回数值。

``None`` 当作「这个键根本没写」处理，静默回退（各处的默认值本来就兜住），
只有写了但写不成数字的那种才值得提醒。
"""

import logging

log = logging.getLogger("afk.numval")


def as_float(value, default, low=None, high=None, what=""):
    """把 ``value`` 转成 float；坏值告警后回退 ``default``，再按 low/high 夹取。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        if what and value is not None:
            log.warning("%s 不是数字（读到 %r），已按 %s 处理", what, value, default)
        number = float(default)
    if low is not None and number < low:
        number = float(low)
    if high is not None and number > high:
        number = float(high)
    return number


def as_int(value, default, low=None, high=None, what=""):
    """同 :func:`as_float`，但返回 int（"30.0" 这类也认）。"""
    number = as_float(value, default, low, high, what)
    return int(number)
