import logging
import threading
import time

from .listval import as_str_list

log = logging.getLogger("afk.scheduler")


class Scheduler:
    def __init__(self, get_settings, on_trigger, bus=None):
        self.get_settings = get_settings
        self.on_trigger = on_trigger
        self.bus = bus
        self._stop = threading.Event()
        self._thread = None
        self._last_date = None
        # 上一次读到的坏 schedule.time 原文（None 表示正常），用于「只告警一次」
        self._bad_time = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="afk-scheduler")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(20):
            try:
                self._tick()
            except Exception:
                continue

    def _parse_time(self, target):
        """把 `schedule.time` 解析成 (hour, minute)；写坏了返回 None 并留痕。

        历史缺陷：写坏时 `except: return` 一句话都不记。定时启动就这么静默地
        永不触发——用户设了 08:00、程序每 20 秒看一眼、什么都不做，界面上
        「定时启动」还打着勾。GUI 那边（`MainWindow._parse_schedule_time`）
        已经会给时间框挂提示并写 warning，这里补上日志侧的同一条线索。

        告警只写一次：`_tick` 每 20 秒跑一轮，坏值会一直被读到，逐轮告警
        会把 events.jsonl 刷满。值变了（用户改了文件）才重新告警。
        """
        raw = str(target)
        if raw == self._bad_time:
            return None
        try:
            hour, minute = [int(x) for x in raw.split(":")[:2]]
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("out of range: {0!r}".format(raw))
        except Exception as exc:
            self._bad_time = raw
            log.warning(
                "schedule.time %r 不是合法的 HH:MM（%s），定时启动不会触发", raw, exc
            )
            return None
        if self._bad_time is not None:
            # 从坏值恢复成合法值时也留一句，否则用户只记得自己改过、不知道成了
            self._bad_time = None
            log.info("schedule.time 已恢复为 %r", raw)
        return hour, minute

    def _tick(self):
        cfg = (self.get_settings() or {}).get("schedule") or {}
        if not cfg.get("enabled"):
            return
        target = str(cfg.get("time", "04:00"))
        parsed = self._parse_time(target)
        if parsed is None:
            return
        hour, minute = parsed
        now = time.localtime()
        # schedule.days 写成 "mon" 时旧代码迭代出 ['m','o','n']，与 weekday
        # 永远不相等，定时启动静默永不触发（一条日志都没有）。
        days = as_str_list(
            cfg.get("days"), what="schedule.days",
            default=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        )
        days = [day.lower() for day in days]
        weekday = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.tm_wday]
        if weekday not in days:
            return
        today = time.strftime("%Y-%m-%d", now)
        if self._last_date == today:
            return
        scheduled_minutes = hour * 60 + minute
        now_minutes = now.tm_hour * 60 + now.tm_min
        if now_minutes < scheduled_minutes or now_minutes - scheduled_minutes > 15:
            return
        self._last_date = today
        if self.bus:
            self.bus.emit(
                {
                    "project": "core",
                    "kind": "schedule",
                    "level": "info",
                    "message": "scheduled chain start at {0}".format(target),
                }
            )
        try:
            self.on_trigger()
        except Exception as exc:
            log.warning("scheduled trigger callback failed: %s", exc)
