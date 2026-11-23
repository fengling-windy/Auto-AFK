"""配置里「本该是列表」的项写成标量时，必须告警回退而不是静默错乱。

为什么单独立一条冒烟：``afk_core/listval.py`` 存在的理由不是「别抛异常」——
大部分坏值根本不抛（``str`` 和 ``dict`` 都可迭代），现象全是「不报错但行为完全
不对」，比抛异常难查得多：

* ``chain.order: 3`` → ``TypeError: 'int' object is not iterable``，设置页与
  主窗口都起不来（2026-11-23 实测）。
* ``chain.order: "BAAH,March7thAssistant"`` → 迭代出字符列表，一个项目都匹配
  不上，卡片顺序静默全乱。
* ``schedule.days: "mon"`` → ``['m','o','n']``，与 weekday 永不相等，定时启动
  静默永不触发。
* ``gui.hidden_projects: "BAAH"`` → ``['B','A','A','H']``，等于什么都没隐藏。

判据：
1. ``as_str_list`` / ``as_str_groups`` 对各类坏值的返回与告警行为。
2. 真的把坏值写进 settings.yaml 后，SettingsPage 与 MainWindow 仍能构造，
   并且坏值没有变成「逐字符的列表」。
3. ``schedule.days`` 写成 "mon" 时仍能在周一触发（不能静默永不触发）。
"""

import logging
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(r"E:\GameAFK\Auto-AFK")
sys.path.insert(0, str(ROOT))

from afk_core import config, listval

FAILED = []


def check(name, ok):
    if ok:
        print("ok   {0}".format(name))
    else:
        print("FAIL {0}".format(name))
        FAILED.append(name)


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capture():
    handler = _Collect()
    logging.getLogger("afk.listval").addHandler(handler)
    return handler


def test_conversions():
    handler = _capture()
    try:
        # -- 正常输入 --------------------------------------------------------
        check("列表原样返回", listval.as_str_list(["A", "B"], what="demo") == ["A", "B"])
        check("元组也能用", listval.as_str_list(("A", "B"), what="demo") == ["A", "B"])
        check("数字元素转成字符串", listval.as_str_list([1, 2], what="demo") == ["1", "2"])
        check("两端空白被去掉", listval.as_str_list(["  A  "], what="demo") == ["A"])
        check("空串元素被跳过", listval.as_str_list(["A", "", "B"], what="demo") == ["A", "B"])
        check("正常输入不告警", handler.records == [])

        # -- None / 缺省 ----------------------------------------------------
        handler.records.clear()
        check("None 回退默认值", listval.as_str_list(None, ["X"], what="demo") == ["X"])
        check("None 不告警", handler.records == [])
        check("默认值返回的是副本", listval.as_str_list(None, ["X"], what="demo") is not ["X"])

        # -- 标量：这是最坑的一类 -------------------------------------------
        handler.records.clear()
        check("单字符串当成一个元素", listval.as_str_list("BAAH", what="demo") == ["BAAH"])
        check("单字符串要告警", len(handler.records) == 1)
        handler.records.clear()
        check("带逗号的字符串不会被切分", listval.as_str_list("A,B", what="demo") == ["A,B"])
        check("带逗号的字符串要告警", len(handler.records) == 1)
        handler.records.clear()
        check("单个数字当成一个元素", listval.as_str_list(7, what="demo") == ["7"])
        check("单个数字要告警", len(handler.records) == 1)
        handler.records.clear()
        check("空字符串回退默认值", listval.as_str_list("", ["D"], what="demo") == ["D"])
        check("纯空白也回退默认值", listval.as_str_list("   ", ["D"], what="demo") == ["D"])
        check("空字符串不告警（等同没写）", handler.records == [])

        # -- 无法解释的类型 --------------------------------------------------
        handler.records.clear()
        check("bool 回退默认值", listval.as_str_list(True, ["D"], what="demo") == ["D"])
        check("bool 要告警", len(handler.records) == 1)
        handler.records.clear()
        check("映射回退默认值", listval.as_str_list({"A": 1}, ["D"], what="demo") == ["D"])
        check("映射要告警", len(handler.records) == 1)

        # -- 列表里混了非字符串 ----------------------------------------------
        handler.records.clear()
        check("列表里的映射被跳过", listval.as_str_list(["A", {"x": 1}, "B"], what="demo") == ["A", "B"])
        check("跳过时要告警", len(handler.records) == 1)
        handler.records.clear()
        check("列表里的 bool 被跳过", listval.as_str_list(["A", True], what="demo") == ["A"])

        # -- as_str_groups（并行组那种「列表的列表」）------------------------
        handler.records.clear()
        check(
            "两层列表原样返回",
            listval.as_str_groups([["A", "B"], ["C", "D"]], what="demo") == [["A", "B"], ["C", "D"]],
        )
        check("单元素组被丢掉", listval.as_str_groups([["A"], ["C", "D"]], what="demo") == [["C", "D"]])
        check("丢掉单元素组要告警", len(handler.records) == 1)
        handler.records.clear()
        check("两层列表写成一层时全丢", listval.as_str_groups(["A", "B"], what="demo") == [])
        check("写成一层要告警", len(handler.records) == 1)
        handler.records.clear()
        check("标量回退默认值", listval.as_str_groups("A", [["X", "Y"]], what="demo") == [["X", "Y"]])
        check("标量要告警", len(handler.records) == 1)
        handler.records.clear()
        check("None 静默回退", listval.as_str_groups(None, [["X", "Y"]], what="demo") == [["X", "Y"]])
        check("None 不告警", handler.records == [])
    finally:
        logging.getLogger("afk.listval").removeHandler(handler)
        handler.close()


def test_bad_settings_still_opens_gui():
    """把列表型坏值写进 settings.yaml 后，设置页与主窗口仍然能起来。"""
    tmp = Path(tempfile.mkdtemp(prefix="afk_listval_"))
    for sub in ("logs", "logs/projects", "reports"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    saved = {
        name: getattr(config, name)
        for name in (
            "SETTINGS_YAML",
            "PROJECTS_YAML",
            "REPAIR_YAML",
            "LOGS_DIR",
            "PROJECT_LOGS",
            "REPORTS_DIR",
            "EVENTS_FILE",
            "STATE_FILE",
            "DURATIONS_FILE",
            "ERROR_INDEX",
        )
    }
    try:
        config.SETTINGS_YAML = tmp / "settings.yaml"
        config.PROJECTS_YAML = tmp / "afk_projects.yaml"
        config.REPAIR_YAML = tmp / "repair_rules.yaml"
        config.LOGS_DIR = tmp / "logs"
        config.PROJECT_LOGS = tmp / "logs" / "projects"
        config.REPORTS_DIR = tmp / "reports"
        config.EVENTS_FILE = tmp / "logs" / "events.jsonl"
        config.STATE_FILE = tmp / "state.json"
        config.DURATIONS_FILE = tmp / "logs" / "durations.jsonl"
        config.ERROR_INDEX = tmp / "logs" / "errors.jsonl"
        for name in ("settings.yaml", "afk_projects.yaml", "repair_rules.yaml"):
            (tmp / name).write_bytes((ROOT / "configs" / "core" / name).read_bytes())
        config.invalidate_cache()

        data = dict(config.load_yaml(config.SETTINGS_YAML, {}))
        data["pre_run"] = dict(data.get("pre_run") or {})
        data["pre_run"]["at_startup"] = False
        data["housekeeping"] = {"enabled": False}
        data["gui"] = {
            "widget_enabled": False,
            "window_size": [],
            "hidden_projects": "BAAH",
            "silent_open_projects": "BAAH",
        }
        # 最狠的一种：写成整数时旧代码直接 TypeError，连窗口都开不出来。
        data["chain"] = {"order": 3, "parallel_blocks": "BAAH"}
        data["schedule"] = {"enabled": False, "time": "04:00", "days": "mon"}
        config.save_yaml(config.SETTINGS_YAML, data)
        config.invalidate_cache()

        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        from gui.settings_page import SettingsPage

        try:
            page = SettingsPage(log_fn=lambda *a, **k: None)
            page.reload_from_disk()
            order_keys = [
                page.order_list.item(i).data(0x0100) or page.order_list.item(i).text()
                for i in range(page.order_list.count())
            ]
            page.hide()
            check("坏列表值下设置页仍能构造", True)
            check(
                "chain.order 写成 3 没有变成字符列表",
                all(len(str(k)) > 1 for k in order_keys),
            )
        except Exception:
            print(traceback.format_exc())
            check("坏列表值下设置页仍能构造", False)

        import gui.main as gm

        try:
            window = gm.MainWindow()
            window.show()
            app.processEvents()
            window._navigate("settings")
            app.processEvents()
            hidden = list(window._hidden_projects)
            order = window._ordered_project_items()
            window.close()
            check("坏列表值下主窗口仍能打开", True)
            check("hidden_projects 写成标量时不会逐字符拆开", hidden == ["BAAH"])
            check(
                "卡片顺序没被字符列表打乱（项目数不变）",
                len(order) == len(config.registry().get("projects") or {}),
            )
            check(
                "并行组写成标量时不会逐字符拆开",
                list(window._parallel_blocks) == [],
            )
        except Exception:
            print(traceback.format_exc())
            check("坏列表值下主窗口仍能打开", False)
    finally:
        config.invalidate_cache()
        for name, value in saved.items():
            setattr(config, name, value)


def test_scheduler_bad_days():
    """schedule.days 写成 "mon" 时，周一仍然要能触发（不能静默永不触发）。"""
    from afk_core.scheduler import Scheduler

    fired = []
    settings = {"schedule": {"enabled": True, "time": "04:00", "days": "mon"}}
    scheduler = Scheduler(lambda: settings, lambda: fired.append(1))

    # 直接把「今天」伪造成周一 04:00 之后：days 的解析与 weekday 比较才是被测对象。
    real_localtime = time.localtime

    def fake_localtime(secs=None):
        base = real_localtime(secs)
        # struct_time: (tm_year, tm_mon, tm_mday, tm_hour, tm_min, tm_sec,
        #               tm_wday, tm_yday, tm_isdst)；把 wday 改成 0 = 周一
        return time.struct_time(
            (base.tm_year, base.tm_mon, base.tm_mday, 4, 10, 0, 0, base.tm_yday, -1)
        )

    time.localtime = fake_localtime
    try:
        scheduler._tick()
        check("schedule.days 写成单字符串时周一仍会触发", fired == [1])
    finally:
        time.localtime = real_localtime


def main():
    test_conversions()
    test_bad_settings_still_opens_gui()
    test_scheduler_bad_days()
    print()
    if FAILED:
        print("LISTVAL FAIL: {0}".format(", ".join(FAILED)))
        return 1
    print("LISTVAL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
