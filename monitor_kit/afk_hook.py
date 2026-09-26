import atexit
import builtins
import logging
import os
import sys
import threading
import time
import traceback

from afkclient import get_client

_state = {
    "installed": False,
    "project": None,
    "input_guard_warned": False,
    "heartbeat_stop": None,
}
_lock = threading.Lock()


def enabled():
    return os.environ.get("AFK_MONITOR") == "1"


class _EventLogHandler(logging.Handler):
    def __init__(self, client):
        super().__init__(level=logging.INFO)
        self.client = client
        self._seen = {}
        self._seen_lock = threading.Lock()
        self._seen_full_warned = False

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if record.levelno >= logging.ERROR:
            level = "error"
        elif record.levelno >= logging.WARNING:
            level = "warn"
        else:
            level = "info"
        key = (record.levelno, msg[:300])
        now = time.time()
        with self._seen_lock:
            if now - self._seen.get(key, 0.0) < 1.0:
                return
            if len(self._seen) > 20000:
                self._seen.clear()
            self._seen[key] = now
        self.client.emit(
            "log",
            msg,
            level=level,
            data={"logger": record.name, "module": record.module, "lineno": record.lineno},
        )


def _excepthook(exc_type, exc_value, exc_tb):
    try:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        get_client().emit("error", text, level="error", data={"source": "excepthook"})
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _thread_excepthook(args):
    try:
        text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        get_client().emit("error", text, level="error", data={"source": "threading", "thread": args.thread.name if args.thread else ""})
    except Exception:
        pass


def _install_input_guard():
    original = builtins.input

    def guarded_input(prompt=""):
        if not _state["input_guard_warned"]:
            _state["input_guard_warned"] = True
            try:
                get_client().emit("log", "AFK monitor intercepted input() call", level="warn", data={"source": "input_guard"})
            except Exception:
                pass
        return ""

    guarded_input._afk_original = original
    builtins.input = guarded_input


def _heartbeat_loop(stop_event, interval):
    client = get_client()
    while not stop_event.wait(interval):
        try:
            client.emit("heartbeat", "", level="debug")
        except Exception:
            pass


def install(project=None):
    with _lock:
        if _state["installed"]:
            return
        if not enabled():
            return
        _state["installed"] = True
        _state["project"] = project or os.environ.get("AFK_PROJECT", "unknown")
    client = get_client(_state["project"])
    sys.excepthook = _excepthook
    try:
        threading.excepthook = _thread_excepthook
    except Exception:
        pass
    root = logging.getLogger()
    root.addHandler(_EventLogHandler(client))
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    _install_input_guard()
    try:
        interval = float(os.environ.get("AFK_HEARTBEAT_SEC", "20"))
    except Exception:
        interval = 20.0
    stop_event = threading.Event()
    _state["heartbeat_stop"] = stop_event
    threading.Thread(target=_heartbeat_loop, args=(stop_event, interval), daemon=True, name="afk-heartbeat").start()
    atexit.register(_on_exit)
    client.emit("start", "process started", data={"pid": os.getpid(), "cwd": os.getcwd(), "python": sys.version.split()[0]})


def _on_exit():
    try:
        stop_event = _state.get("heartbeat_stop")
        if stop_event is not None:
            stop_event.set()
        get_client().emit("exit", "process exiting", data={"pid": os.getpid(), "exitcode": getattr(sys, "exitcode", None)})
    except Exception:
        pass


def mark_stage(name, **data):
    if not enabled():
        return
    get_client().emit("stage", str(name), data=data or None)


def mark_error(message, **data):
    if not enabled():
        return
    get_client().emit("error", str(message), level="error", data=data or None)


def mark_done(message="task finished", **data):
    if not enabled():
        return
    get_client().emit("done", str(message), data=data or None)
