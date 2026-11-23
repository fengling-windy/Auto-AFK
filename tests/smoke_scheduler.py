"""定时启动（`afk_core.scheduler.Scheduler`）的冒烟：坏时间必须留痕。

历史上这里完全没有测试，`_tick` 解析 `schedule.time` 失败时是 `except: return`
——一句话都不记。用户在 settings.yaml 里把时间写成 `abc` / `25:99` / `0830`，
「定时启动」界面打着勾，程序每 20 秒看一眼、永远不触发，日志里没有任何线索。

本测试不启线程、不碰真实配置：直接调 `_tick()`，用注入的 get_settings 造场景。
"""

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core.scheduler import Scheduler

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (("  -- " + str(detail)) if detail and not ok else ""))
    return ok


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def clear(self):
        self.records.clear()

    def messages(self, level=None):
        return [r.getMessage() for r in self.records if level is None or r.levelno == level]


def _scheduler(holder, fired):
    def get_settings():
        return {"schedule": dict(holder, days=DAY_KEYS)}

    return Scheduler(get_settings, lambda: fired.append(1))


def _run():
    ok = True
    grab = _Grab()
    logger = logging.getLogger("afk.scheduler")
    old_level = logger.level
    logger.addHandler(grab)
    logger.setLevel(logging.DEBUG)
    try:
        now = time.localtime()
        current = "{0:02d}:{1:02d}".format(now.tm_hour, now.tm_min)

        # -- 1. 正常值就该触发 ------------------------------------------------
        holder = {"enabled": True, "time": current}
        fired = []
        sched = _scheduler(holder, fired)
        grab.clear()
        sched._tick()
        ok &= check("设定时间就是现在时会触发", fired == [1], fired)
        ok &= check("正常值不写任何警告", grab.messages(logging.WARNING) == [], grab.messages(logging.WARNING))

        # -- 2. 各种坏值：不触发、不抛、写一条 warning ------------------------
        for bad in ("abc", "25:99", "", "0830", "-1:30", "2000"):
            holder = {"enabled": True, "time": bad}
            fired = []
            sched = _scheduler(holder, fired)
            grab.clear()
            try:
                sched._tick()
                raised = None
            except Exception as exc:  # noqa: BLE001 - 冒烟里要把它当成失败断言
                raised = exc
            ok &= check("时间写成 {0!r} 时 _tick 不抛异常".format(bad), raised is None, raised)
            ok &= check("时间写成 {0!r} 时不触发".format(bad), fired == [], fired)
            ok &= check(
                "时间写成 {0!r} 时写一条 warning".format(bad),
                len(grab.messages(logging.WARNING)) == 1,
                grab.messages(logging.WARNING),
            )

        # -- 3. 同一个坏值不逐轮刷告警（_tick 每 20 秒跑一轮）-----------------
        holder = {"enabled": True, "time": "abc"}
        fired = []
        sched = _scheduler(holder, fired)
        grab.clear()
        for _ in range(5):
            sched._tick()
        ok &= check("同一个坏值连读五轮只告警一次", len(grab.messages(logging.WARNING)) == 1,
                    grab.messages(logging.WARNING))

        # 值改成另一个坏值要重新告警（用户改了文件，得让他知道还是错的）
        holder["time"] = "xyz"
        grab.clear()
        sched._tick()
        sched._tick()
        ok &= check("换成另一个坏值会重新告警一次", len(grab.messages(logging.WARNING)) == 1,
                    grab.messages(logging.WARNING))

        # -- 4. 恢复成合法值：留一条 info，并且真的能触发 ---------------------
        holder["time"] = current
        # 注意这里必须是 fired.clear()，不能重新赋值 fired = []：
        # _scheduler() 里的 lambda 绑定的是它自己的参数列表对象，调用方重新
        # 绑定局部名对它毫无影响，断言会永远看到空列表（踩过一次）。
        fired.clear()
        grab.clear()
        sched._tick()
        ok &= check("坏值改回合法值后能触发", fired == [1], fired)
        ok &= check("坏值改回合法值时写一条 info",
                    any("已恢复" in m for m in grab.messages(logging.INFO)),
                    grab.messages(logging.INFO))
        ok &= check("恢复后坏值标记清空", sched._bad_time is None, sched._bad_time)

        # -- 5. 边界：关闭时不读时间（坏值也不该告警）--------------------------
        holder = {"enabled": False, "time": "abc"}
        fired = []
        sched = _scheduler(holder, fired)
        grab.clear()
        sched._tick()
        ok &= check("定时启动关着时坏时间不告警", grab.messages(logging.WARNING) == [],
                    grab.messages(logging.WARNING))
        ok &= check("定时启动关着时不触发", fired == [], fired)

        # -- 6. 边界：今天不在允许的日子里不触发，也不告警 ---------------------
        holder = {"enabled": True, "time": current, "days": ["sun"] if now.tm_wday != 6 else ["mon"]}
        fired = []
        sched = Scheduler(lambda: {"schedule": holder}, lambda: fired.append(1))
        grab.clear()
        sched._tick()
        ok &= check("今天不在 schedule.days 里时不触发", fired == [], fired)
        ok &= check("今天不在 schedule.days 里时不写 warning", grab.messages(logging.WARNING) == [],
                    grab.messages(logging.WARNING))

        # -- 7. 同一天只触发一次 ----------------------------------------------
        holder = {"enabled": True, "time": current}
        fired = []
        sched = _scheduler(holder, fired)
        sched._tick()
        sched._tick()
        ok &= check("同一天只触发一次", fired == [1], fired)

        # -- 8. 设定时间已过太久的窗口外不补跑 ---------------------------------
        later = (now.tm_hour * 60 + now.tm_min - 30) % (24 * 60)
        holder = {"enabled": True, "time": "{0:02d}:{1:02d}".format(later // 60, later % 60)}
        fired = []
        sched = _scheduler(holder, fired)
        if later // 60 < now.tm_hour or (later // 60 == now.tm_hour and later % 60 < now.tm_min):
            sched._tick()
            ok &= check("错过设定时间 30 分钟后不补跑", fired == [], fired)
    finally:
        logger.removeHandler(grab)
        logger.setLevel(old_level)
    return ok


def main():
    ok = _run()
    print("SCHEDULER", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
