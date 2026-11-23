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

    # 统一用隔离工具：同时覆盖 7 个运行数据路径并设置 AFK_ROOT，
    # 避免以后改成真跑任务链时子进程写回真实 logs（见 smoke_isolation 文档）。
    from tests import smoke_isolation
    guard = smoke_isolation.isolate_runtime(prefix="afk_guilogev_")

    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    settings["pre_run"]["at_startup"] = False
    settings["housekeeping"] = {"enabled": False}
    config.save_yaml(config.SETTINGS_YAML, settings)

    window = gm.MainWindow()
    window.show()
    pump(app, 200)

    # 1) GUI 自身动作应进入事件流
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

    # 5) 自己发的 GUI 事件不能回流再画一遍（界面里同一行日志出现两次）
    window.log_view.clear()
    window.event_buffer.clear()
    marker = "GUIMARK-" + str(int(time.time() * 1000) % 100000)
    window._append_log("core", "info", marker)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        window._poll()
        pump(app, 40)
    shown = window.log_view.toPlainText().count(marker)
    print("   同一行日志出现次数 =", shown)
    check("GUI 自身动作只显示一次（不回声）", shown == 1)

    # 6) 「清空显示」必须真的清空：切过滤下拉框会 _rerender_log 重建视图，
    #    历史缺陷是只 clear 了控件、event_buffer 还留着，日志又被画回来。
    window.filter_combo.setCurrentIndex(0)
    pump(app, 100)
    window._clear_log()
    pump(app, 100)
    check("清空显示后视图为空", window.log_view.document().blockCount() <= 1)
    window.filter_combo.setCurrentIndex(max(0, window.filter_combo.count() - 1))
    pump(app, 150)
    check("清空显示后切过滤不会把日志画回来", window.log_view.document().blockCount() <= 1)

    # 7) 日志很多时只重画最近若干行：_rerender_log 原来把 event_buffer 里所有匹配
    #    事件逐条 appendHtml，实测 5000 行要 422ms，切一下过滤下拉框界面就僵半秒。
    #    这里把上限临时调小验证截断逻辑与提示文案（真实上限 2000 行）。
    window.event_buffer.clear()
    window.log_view.clear()
    for i in range(120):
        window.event_buffer.append(
            {"project": "core", "level": "info", "kind": "core", "message": "BULK-{0}".format(i)}
        )
    _orig_limit = gm.LOG_RERENDER_LIMIT
    gm.LOG_RERENDER_LIMIT = 50
    try:
        window._rerender_log()
        pump(app, 100)
        text = window.log_view.toPlainText()
    finally:
        gm.LOG_RERENDER_LIMIT = _orig_limit
    check("日志过多时只重画最近若干行（保留 BULK-70，丢掉 BULK-69）",
          "BULK-70" in text and "BULK-69" not in text)
    check("截断时说明更早的日志去了哪里", "更早的 70 条" in text and "events.jsonl" in text)
    check("event_buffer 本身没有被截断", len(window.event_buffer) == 120)

    # 8) 事件洪峰。订阅队列上限 5000，而 events.jsonl 实测峰值 14901 条/秒
    #    （2026-11-11  MaaEnd 的 GetWindowRect failed 刷屏）。原实现一轮只取
    #    500 条、且每条都单独 appendHtml：300ms 一轮只能消化 1667 条/秒，
    #    队列几秒就被灌满，之后的新事件直接丢弃——界面上停在几分钟前的日志，
    #    用户以为程序卡死。
    window._clear_log()
    window.event_buffer.clear()
    window._skipped_since_notice = 0
    window._skip_notice_at = 0.0
    _orig_batch = gm.LOG_RENDER_BATCH_LIMIT
    gm.LOG_RENDER_BATCH_LIMIT = 100
    try:
        for i in range(1000):
            window.queue.put_nowait(
                {"project": "core", "level": "info", "kind": "log",
                 "message": "FLOOD-{0}".format(i)}
            )
        window._poll()
        pump(app, 80)
        text = window.log_view.toPlainText()
        drawn = text.count("FLOOD-")
        check("洪峰时一轮只画最近若干行（实测 {0} 行）".format(drawn), 0 < drawn <= 101)
        check("一行日志一个文本块（不是全挤成一块）",
              window.log_view.document().blockCount() >= drawn)
        check("没画的行仍留在 event_buffer 里", len(window.event_buffer) == 1000)
        check("跳过时说明日志刷得太快", "日志刷得太快" in text)
        check("这条说明只写一次（洪峰期间不自己刷屏）",
              text.count("日志刷得太快") == 1)
        # 队列必须能在有限轮次内收干：原实现每轮 500 条的上限追不上洪峰。
        rounds = 0
        while window.queue.qsize() and rounds < 20:
            window._poll()
            pump(app, 20)
            rounds += 1
        check("积压能被收干（{0} 轮后队列为空）".format(rounds), window.queue.qsize() == 0)
        check("收干用的轮次远小于 20", rounds < 5)
    finally:
        gm.LOG_RENDER_BATCH_LIMIT = _orig_batch

    window.hide()
    window._quit()
    config.SETTINGS_YAML = _REAL
    guard.restore()
    shutil.rmtree(_TMP, ignore_errors=True)

    print()
    if FAILURES:
        print("GUI-LOG {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("GUI-LOG PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
