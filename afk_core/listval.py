# -*- coding: utf-8 -*-
"""配置里「列表型」项的取值兜底。

为什么单独一个模块：``settings.yaml`` 里的 ``chain.order``、
``gui.hidden_projects``、``gui.silent_open_projects``、``schedule.days``
本该是字符串列表。写成标量时旧代码不会抛异常——``str`` 和 ``dict`` 都可迭代——
于是现象全是「不报错但行为完全不对」，比抛异常更难查：

* ``chain.order: 3`` → ``TypeError: 'int' object is not iterable``，
  设置页与主窗口都起不来（实测 2026-11-23）。
* ``chain.order: "BAAH,March7thAssistant"`` → 迭代出 ``['B','A','A','H',...]``
  这种字符列表，一个项目键都匹配不上，卡片顺序静默全乱、垫底。
* ``schedule.days: "mon"`` → ``['m','o','n']``，与 ``weekday`` 永远不相等，
  定时启动静默永不触发。
* ``gui.hidden_projects: "BAAH"`` → ``['B','A','A','H']``，等于什么都没隐藏。

统一在这里取，坏值告警一次并按默认值处理。``None``（键没写）静默回退。
"""

import logging

log = logging.getLogger("afk.listval")


def _warn(what, value, hint):
    if not what:
        return
    log.warning("%s 不是字符串列表（读到 %s %r），%s", what, type(value).__name__, value, hint)


def as_str_list(value, default=None, what=""):
    """把配置里的一项取成 ``list[str]``。

    * ``None`` / 键缺失 → 返回 ``default`` 的副本（不告警，各处默认值兜住）。
    * 列表/元组/集合 → 逐个 ``str()``，非标量元素（dict/list）跳过。
    * 单个字符串/数字 → 当作只有一个元素，并告警（别指望它被逗号切分）。
    * 其它（bool/dict/float） → 回退 ``default``，并告警。
    """
    fallback = list(default or [])
    if value is None:
        return fallback
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return fallback
        _warn(what, value, "已当作只有一个元素；要写多个请用「- 项目键」逐行列出")
        return [text]
    if isinstance(value, bool):
        _warn(what, value, "已按默认值处理")
        return fallback
    if isinstance(value, (int, float)):
        _warn(what, value, "已当作只有一个元素；要写多个请用「- 项目键」逐行列出")
        return [str(value)]
    if isinstance(value, (list, tuple, set)):
        items = []
        dropped = 0
        for item in value:
            if isinstance(item, bool):
                dropped += 1
                continue
            if isinstance(item, (int, float)):
                items.append(str(item))
            elif isinstance(item, str):
                text = item.strip()
                if text:
                    items.append(text)
            else:
                dropped += 1
        if dropped and what:
            log.warning("%s 里有 %d 项不是字符串，已跳过", what, dropped)
        return items
    _warn(what, value, "已按默认值处理")
    return fallback


def as_str_groups(value, default=None, min_len=2, what=""):
    """把配置里的一项取成 ``list[list[str]]``（并行组那种「列表的列表」）。

    每个内层组的长度不足 ``min_len`` 时整组丢弃（``merge_parallel_blocks``
    本来也不认单元素组）。
    """
    fallback = [list(group) for group in (default or [])]
    if value is None:
        return fallback
    if not isinstance(value, (list, tuple)):
        _warn(what, value, "已按默认值处理")
        return fallback
    groups = []
    dropped = 0
    for raw in value:
        if not isinstance(raw, (list, tuple)):
            dropped += 1
            continue
        group = as_str_list(raw, default=[], what="")
        if len(group) >= min_len:
            groups.append(group)
        else:
            dropped += 1
    if dropped and what:
        log.warning("%s 里有 %d 组不是「至少 %d 个项目键的列表」，已忽略", what, dropped, min_len)
    return groups
