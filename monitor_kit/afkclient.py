import json
import os
import threading
import time
import urllib.request


def _now():
    return time.time()


class AfkClient:
    def __init__(self, project=None, run_id=None):
        self.project = project or os.environ.get("AFK_PROJECT", "unknown")
        self.run_id = run_id or os.environ.get("AFK_RUN_ID", "")
        try:
            self.port = int(os.environ.get("AFK_EVENT_PORT", "0") or 0)
        except Exception:
            self.port = 0
        self.spool = os.environ.get("AFK_EVENT_SPOOL", "")
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, kind, message="", level="info", data=None):
        self._seq += 1
        event = {
            "ts": _now(),
            "project": self.project,
            "run_id": self.run_id,
            "kind": kind,
            "level": level,
            "message": str(message)[:4000],
        }
        if data:
            try:
                json.dumps(data)
                event["data"] = data
            except Exception:
                event["data"] = {"repr": repr(data)[:2000]}
        payload = json.dumps(event, ensure_ascii=False)
        with self._lock:
            if self.port and self._post(payload):
                return event
            if self.spool:
                self._spool_write(payload)
        return event

    def _post(self, payload):
        req = urllib.request.Request(
            "http://127.0.0.1:{0}/event".format(self.port),
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def _spool_write(self, payload):
        try:
            parent = os.path.dirname(self.spool)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.spool, "a", encoding="utf-8") as f:
                f.write(payload + "\n")
        except Exception:
            pass


_client = None
_client_lock = threading.Lock()


def get_client(project=None):
    global _client
    with _client_lock:
        if _client is None or (project and _client.project != project):
            _client = AfkClient(project)
        return _client
