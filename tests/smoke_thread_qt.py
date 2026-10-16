"""跨线程 Qt 调用回归测试（针对空闲挂机同类缺陷）。

真实缺陷背景：后台线程直接操作 Qt 控件会崩溃或静默失效，排查困难。
已修复：ProjectPage._spawn_and_locate（线程 afk-embed-*）曾直接调用
`self._log(...)`，而 `_log` 是 MainWindow._append_log，会写 log_view 控件。
现在改为 log_requested Signal 投递回 GUI 线程。

本测试证明：
  1. 从后台线程调 ProjectPage._log 时，日志不会立刻写控件（只发信号）
  2. 信号经 Qt 事件循环投递后，才在 GUI 主线程真正执行 log_fn
  3. 投递确实跨线程（若改回直接调用，本测试会失败）
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

from gui.project_page import ProjectPage

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

    records = []

    def log_fn(key, level, message):
        records.append({
            "thread": threading.current_thread().name,
            "is_main": threading.current_thread() is threading.main_thread(),
            "level": level,
            "message": message,
        })

    spec = {"launch_type": "python", "gui": {"web": False}, "path": "."}
    page = ProjectPage("ThreadProbe", spec, "#4fc3f7", supervisor=None, log_fn=log_fn)

    # 1) 从后台线程调用 _log：不应立刻执行 log_fn
    def bg_log():
        page._log("ThreadProbe", "warn", "来自后台线程的日志")

    t = threading.Thread(target=bg_log, name="fake-embed", daemon=True)
    t.start()
    t.join(timeout=5)
    check("后台线程调用 _log 后未立刻写日志（仅发信号）", records == [])

    # 2) 事件循环跑起来后，才在 GUI 主线程执行 log_fn
    pump(app, 500)
    print("   records =", records)
    check("信号投递后日志才被执行", len(records) == 1)
    check("日志在 GUI 主线程执行（关键）", records and records[0]["is_main"] is True)
    check("日志线程不是发起调用的线程", records and records[0]["thread"] != "fake-embed")
    check("日志内容正确传递", records and records[0]["level"] == "warn"
          and records[0]["message"] == "来自后台线程的日志")

    # 3) 主线程直接调用也照常工作
    records.clear()
    page._log("ThreadProbe", "info", "主线程日志")
    pump(app, 300)
    check("主线程调用同样正常", len(records) == 1 and records[0]["message"] == "主线程日志")

    # 4) log_fn 抛异常时不影响 _log 调用方（后台线程不应被异常打断）
    def boom(key, level, message):
        raise RuntimeError("log sink failed")

    page2 = ProjectPage("BoomProbe", spec, "#4fc3f7", supervisor=None, log_fn=boom)
    raised = {"v": False}
    try:
        page2._log("BoomProbe", "info", "会触发异常的日志")
    except Exception:
        raised["v"] = True
    check("后台线程调用 _log 不因 log_fn 异常而抛出", raised["v"] is False)
    pump(app, 300)   # 槽内异常被吞掉，不应崩溃

    page.deleteLater()
    page2.deleteLater()
    pump(app, 100)

    config.SETTINGS_YAML = _REAL_SETTINGS
    shutil.rmtree(_TMP, ignore_errors=True)

    print()
    if FAILURES:
        print("THREAD-QT {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("THREAD-QT PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
