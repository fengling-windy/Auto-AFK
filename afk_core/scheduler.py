import threading
import time


class Scheduler:
    def __init__(self, get_settings, on_trigger, bus=None):
        self.get_settings = get_settings
        self.on_trigger = on_trigger
        self.bus = bus
        self._stop = threading.Event()
        self._thread = None
        self._last_date = None

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

    def _tick(self):
        cfg = (self.get_settings() or {}).get("schedule") or {}
        if not cfg.get("enabled"):
            return
        target = str(cfg.get("time", "04:00"))
        try:
            hour, minute = [int(x) for x in target.split(":")[:2]]
        except Exception:
            return
        now = time.localtime()
        days = [str(d).lower() for d in (cfg.get("days") or ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])]
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
        except Exception:
            pass
