"""跨线程 Qt 调用回归测试（针对后台线程直接摸控件的同类缺陷）。

真实缺陷背景：后台线程直接操作 Qt 控件会崩溃或静默失效，排查困难。

历史：项目界面曾经被「嵌入」进主窗口（QWindow.fromWinId +
createWindowContainer），那条链路有 afk-embed-* 后台线程直接调用
`self._log(...)` → MainWindow._append_log → log_view 控件。嵌入已整体移除，
但「界面」按钮仍然是**后台线程**去 spawn 项目进程（不能阻塞 UI），它写日志
时用的是 `gui_log` Signal。

本测试证明：
  1. 后台线程 emit `gui_log` 后，日志不会立刻写控件（只发信号）
  2. 信号经 Qt 事件循环投递后，才在 GUI 主线程真正执行 _append_log
  3. 投递确实跨线程（若改回直接调用，本测试会失败）
  4. 打开界面失败/异常时也能从后台线程安全写日志，且不把异常抛回线程
"""

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

_TMP = Path(tempfile.mkdtemp(prefix="afk_thread_"))
_REAL_SETTINGS = config.SETTINGS_YAML
shutil.copy(_REAL_SETTINGS, _TMP / "settings.yaml")
config.SETTINGS_YAML = _TMP / "settings.yaml"

from PySide6.QtWidgets import QApplication

import gui.main as gm

FAILURES = []


def check(name, ok):
    print("{0} {1}".format("PASS" if ok else "FAIL", name))
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

    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_threadqt_")
    try:
        return _run(app)
    finally:
        config.SETTINGS_YAML = _REAL_SETTINGS
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()
        shutil.rmtree(_TMP, ignore_errors=True)


def _run(app):
    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    settings["pre_run"]["at_startup"] = False
    settings["housekeeping"] = {"enabled": False}
    settings["gui"]["silent_open_projects"] = []
    config.save_yaml(config.SETTINGS_YAML, settings)

    window = gm.MainWindow()
    window.show()
    pump(app, 200)

    # 记录每条日志是在哪个线程真正落地的（_append_log 被替换成采集器）
    records = []

    def sink(project, level, message, autoscroll=True, emit_event=True):
        records.append({
            "thread": threading.current_thread().name,
            "is_main": threading.current_thread() is threading.main_thread(),
            "project": project,
            "level": level,
            "message": message,
        })

    # 采集器接管 gui_log：先把 __init__ 里那条 to _append_log 的连接摘掉，
    # 否则同一条日志会被记录两次（一次进控件、一次进 records）。
    window.gui_log.disconnect()
    window.gui_log.connect(sink)

    reached = []

    def slow_open(key):
        reached.append(threading.current_thread().name)
        time.sleep(0.25)   # 后台线程停在这里时，控件必须还没有被写
        return True

    window.supervisor.open_project_gui = slow_open

    # 1) 后台线程拉起界面 → 期间不应有日志写控件
    window._open_project_gui("ThreadProbe")
    time.sleep(0.1)
    app.processEvents()
    check("后台线程正在打开界面时还没有写日志", records == [])
    check("打开界面确实发生在后台线程", reached and reached[0] != threading.main_thread().name)

    # 2) 事件循环跑起来后，才在 GUI 主线程执行日志
    pump(app, 800)
    print("   records =", records)
    check("信号投递后日志才被执行", len(records) == 1)
    check("日志在 GUI 主线程执行（关键）", bool(records) and records[0]["is_main"] is True)
    check("日志线程不是发起调用的线程", bool(records) and records[0]["thread"] != reached[0])
    check(
        "日志内容正确传递",
        bool(records) and records[0]["level"] == "info"
        and records[0]["project"] == "ThreadProbe"
        and "已打开" in records[0]["message"],
    )

    # 3) 打开失败（返回 False）也要在 GUI 线程写告警
    records.clear()

    def refuse_open(key):
        return False

    window.supervisor.open_project_gui = refuse_open
    window._open_project_gui("RefuseProbe")
    pump(app, 600)
    check("打开失败写入告警日志", len(records) == 1 and records[0]["level"] == "warn")
    check("打开失败的日志也在 GUI 主线程", bool(records) and records[0]["is_main"] is True)
    check("打开失败文案点明原因", bool(records) and "无法打开" in records[0]["message"])

    # 4) 后台线程里 open_project_gui 抛异常：不能从线程里漏出去（会打到线程
    #    钩子上），要变成一条 error 日志
    records.clear()

    def boom_open(key):
        raise RuntimeError("spawn failed")

    window.supervisor.open_project_gui = boom_open
    window._open_project_gui("BoomProbe")
    pump(app, 600)
    check("后台线程异常变成错误日志", len(records) == 1 and records[0]["level"] == "error")
    check("后台线程异常日志在主线程落地", bool(records) and records[0]["is_main"] is True)
    check("后台线程异常不打断线程", any("BoomProbe" in str(r.get("project")) for r in records))

    # 5) 主线程直接 emit 也照常工作
    records.clear()
    window.gui_log.emit("MainProbe", "info", "主线程日志")
    pump(app, 300)
    check("主线程调用同样正常", len(records) == 1 and records[0]["message"] == "主线程日志")

    window.close()
    pump(app, 100)

    print()
    if FAILURES:
        print("THREAD-QT {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("THREAD-QT PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
