"""GUI 动作进入事件流的可观测性测试。

背景：GUI 自身动作（开关设置、空闲决策、手动更新等 15 处）过去只写界面控件、
不进事件流，导致排查时 events.jsonl / 报告里看不到这些关键动作——空闲挂机
故障期间「日志里什么都没发生」正是这个问题放大的。

修复后 _append_log 默认同时 emit 一个 kind="gui" 事件；但渲染「已经从事件流
收到的事件」时必须 emit_event=False，否则会把收到的事件再发一遍形成回声。
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

_TMP = Path(tempfile.mkdtemp(prefix="afk_guilog_"))
_REAL = config.SETTINGS_YAML
shutil.copy(_REAL, _TMP / "settings.yaml")
config.SETTINGS_YAML = _TMP / "settings.yaml"

from PySide6.QtWidgets import QApplication

import gui.main as gm

FAILURES = []


def check(name, ok, extra=""):
    print("{0} {1}{2}".format("PASS" if ok else "FAIL", name, ("  " + extra) if extra else ""))
    if not ok:
        FAILURES.append(name)


def pump(app, ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    config.LOGS_DIR = _TMP / "logs"
    config.PROJECT_LOGS = _TMP / "logs" / "projects"
    config.EVENTS_FILE = _TMP / "logs" / "events.jsonl"
    config.STATE_FILE = _TMP / "logs" / "state.json"
    config.REPORTS_DIR = _TMP / "logs" / "reports"
    config.DURATIONS_FILE = _TMP / "logs" / "durations.jsonl"
    config.ERROR_INDEX = _TMP / "logs" / "errors.jsonl"

    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    settings["pre_run"]["at_startup"] = False
    settings["housekeeping"] = {"enabled": False}
    config.save_yaml(config.SETTINGS_YAML, settings)

    window = gm.MainWindow()
    window.show()
    pump(app, 200)

    # 1) GUI 自身动作应进入事件流
    before = len(window.bus.history(project=None))
    window._append_log("core", "info", "GUI 动作：切换空闲开关")
    pump(app, 200)
    gui_events = [e for e in window.bus.history(project=None)
                  if e.get("kind") == "gui" and "切换空闲开关" in str(e.get("message"))]
    print("   产生 kind=gui 事件数 =", len(gui_events))
    check("GUI 自身动作写入事件流", len(gui_events) >= 1)
    check("事件带有正确的 project/level",
          gui_events and gui_events[0].get("project") == "core" and gui_events[0].get("level") == "info")

    # 2) 显式禁用时不应写事件（渲染 bus 事件用的路径）
    n_before = len([e for e in window.bus.history(project=None) if e.get("kind") == "gui"])
    window._append_log("core", "info", "这条不该进事件流", emit_event=False)
    pump(app, 150)
    n_after = len([e for e in window.bus.history(project=None) if e.get("kind") == "gui"])
    check("emit_event=False 时不写事件流（防回声）", n_after == n_before)

    # 3) 真实链路：通过 HTTP 发一个事件，_poll 渲染它时不应再发回（无回声）
    port = window.bus.port
    import urllib.request
    import json as _json
    req = urllib.request.Request(
        "http://127.0.0.1:{0}/event".format(port),
        data=_json.dumps({"project": "core", "kind": "log", "level": "info",
                          "message": "外部注入事件"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        check("HTTP 注入事件成功", resp.status == 200)

    gui_before = len([e for e in window.bus.history(project=None) if e.get("kind") == "gui"])
    window._poll()
    pump(app, 300)
    gui_after = len([e for e in window.bus.history(project=None) if e.get("kind") == "gui"])
    injected_rendered = any("外部注入事件" in str(e.get("message"))
                            for e in window.event_buffer)
    print("   注入事件被渲染 =", injected_rendered, "| kind=gui 事件数变化 =", gui_after - gui_before)
    check("注入的事件被 GUI 渲染到日志区", injected_rendered is True)
    check("渲染收到的事件时不产生新事件（无回声放大）", gui_after == gui_before)

    # 4) 事件真的落盘（用户排查时能看到）
    ev = config.EVENTS_FILE
    pump(app, 300)
    try:
        window.bus._close_jsonl()
    except Exception:
        pass
    text = ev.read_text(encoding="utf-8", errors="ignore") if ev.exists() else ""
    check("事件已写入 events.jsonl（可事后排查）", "切换空闲开关" in text)

    window.hide()
    window._quit()
    config.SETTINGS_YAML = _REAL
    shutil.rmtree(_TMP, ignore_errors=True)

    print()
    if FAILURES:
        print("GUI-LOG {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("GUI-LOG PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
