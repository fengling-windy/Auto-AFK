import threading
import time


class Notifier:
    def __init__(self, bus, settings=None):
        self.bus = bus
        self.settings = settings or {}
        self._lock = threading.Lock()
        self._last = {}

    def _cfg(self):
        return (self.settings.get("notifications") or {})

    def send(self, title, content, level="info", key=None):
        cfg = self._cfg()
        if not cfg.get("enabled"):
            return False
        if key:
            with self._lock:
                now = time.time()
                last = self._last.get(key, 0)
                if now - last < float(cfg.get("dedupe_sec", 30)):
                    return False
                self._last[key] = now
        sent = False
        provider = cfg.get("provider")
        params = dict(cfg.get("params") or {})
        if provider:
            try:
                from onepush import notify as onepush_notify

                resp = onepush_notify(provider, title=title, content=content, **params)
                sent = resp is not None
            except Exception as exc:
                self.bus.emit(
                    {
                        "project": "core",
                        "kind": "notify",
                        "level": "warn",
                        "message": "onepush failed: {0}".format(exc),
                    }
                )
        if cfg.get("toast", True):
            try:
                from winotify import Notification

                toast = Notification(app_id="Auto-AFK", title=title, msg=content[:500], duration="short")
                toast.show()
                sent = True
            except Exception:
                pass
        self.bus.emit(
            {
                "project": "core",
                "kind": "notify",
                "level": "info",
                "message": "{0}: {1}".format(title, content[:200]),
                "data": {"sent": sent, "provider": provider},
            }
        )
        return sent
