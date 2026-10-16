"""空闲自动挂机端到端链路测试（针对真实故障：空闲到点后任务没跑起来）。

真实故障复盘：IdleWatcher 在后台线程回调 _on_idle_trigger，而那里用
`QTimer.singleShot(0, ...)` —— QTimer 在没有事件循环的线程里创建的定时器
永远不会触发，于是提示框永不出现、任务永不启动，异常还被静默吞掉。
日志现象：发出 "idle ... prompting" 事件后再无任何事件。

本测试用「极短阈值 + 真实时间推进」验证完整链路：
    IdleWatcher._tick -> 触发回调 -> Signal 投递到 GUI 线程 -> _prompt_idle
    -> 决策 -> start_chain / start_chain_background
并验证跨线程投递真的发生（不是同线程假通过）。
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

_TMP = Path(tempfile.mkdtemp(prefix="afk_idle_flow_"))
_REAL_SETTINGS = config.SETTINGS_YAML
shutil.copy(_REAL_SETTINGS, _TMP / "settings.yaml")
config.SETTINGS_YAML = _TMP / "settings.yaml"

from PySide6.QtWidgets import QApplication

from afk_core.idle import IdleWatcher
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


def build_window(app):
    tmp_logs = _TMP / "logs"
    config.LOGS_DIR = tmp_logs
    config.PROJECT_LOGS = tmp_logs / "projects"
    config.EVENTS_FILE = tmp_logs / "events.jsonl"
    config.STATE_FILE = tmp_logs / "state.json"
    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    settings["pre_run"]["at_startup"] = False
    settings["housekeeping"] = {"enabled": False}
    settings.setdefault("idle", {}).update({"enabled": True, "minutes": 30, "prompt_sec": 30})
    config.save_yaml(config.SETTINGS_YAML, settings)
    window = gm.MainWindow()
    window.show()
    pump(app, 150)
    return window


# ---------------------------------------------------------------------------

def test_background_thread_delivery(app):
    """核心回归：从后台线程触发时，提示框必须真的到达 GUI 线程。"""
    window = build_window(app)
    reached = {}

    # 拦掉真正弹窗（会阻塞事件循环），只记录「GUI 线程收到了请求」
    def fake_prompt(idle_sec):
        reached["idle_sec"] = idle_sec
        reached["thread"] = threading.current_thread().name
        reached["is_main"] = (threading.current_thread() is threading.main_thread())
        # 走真实决策分发
        window._handle_idle_decision("all")

    window._prompt_idle = fake_prompt
    window.idle_triggered.disconnect()
    window.idle_triggered.connect(fake_prompt)

    calls = []

    def start_chain():
        calls.append("all")

    window.start_chain = start_chain

    # 在后台线程发出信号（模拟 IdleWatcher 的真实调用场景）
    def bg():
        reached["bg_thread"] = threading.current_thread().name
        window._on_idle_trigger(1805.0)

    t = threading.Thread(target=bg, name="fake-idle", daemon=True)
    t.start()
    t.join(timeout=5)
    pump(app, 400)

    print("   bg thread = {0} | delivered on = {1} | main={2}".format(
        reached.get("bg_thread"), reached.get("thread"), reached.get("is_main")))
    check("后台线程的触发被投递到 GUI 主线程", reached.get("is_main") is True)
    check("投递发生在不同线程（证明跨线程生效）", reached.get("thread") != reached.get("bg_thread"))
    check("投递携带了空闲秒数", reached.get("idle_sec") == 1805.0)
    check("决策 all 触发了 start_chain", calls == ["all"])
    window.hide()
    window._quit()


def test_real_watcher_triggers_end_to_end(app):
    """真实 IdleWatcher 线程跑完整链路到 GUI 线程（把系统空闲时间打桩为恒定超阈值）。

    真实空闲秒数取决于「测试时人是否在动鼠标」，通常是 0，无法作为测试依据，
    因此只替换 idle_seconds()，其余（后台线程 + 信号投递 + 决策分发）全走真实实现。
    """
    import afk_core.idle as idle_mod

    window = build_window(app)
    fired = {"count": 0}

    def fake_prompt(idle_sec):
        fired["count"] += 1

    window.idle_triggered.disconnect()
    window.idle_triggered.connect(fake_prompt)

    original_idle = idle_mod.idle_seconds
    idle_mod.idle_seconds = lambda: 9999.0        # 恒定「已空闲很久」
    try:
        # 阈值取默认之外的短值（分钟数很小 -> 阈值被 max(1.0) 兜到 1 秒）
        watcher = IdleWatcher(
            lambda: {"idle": {"enabled": True, "minutes": 0.02, "prompt_sec": 30, "cooldown_min": 0}},
            window._on_idle_trigger,
            window.bus,
            interval=0.2,
        )
        watcher.start()
        pump(app, 1500)
        watcher.stop()
        arrived = fired["count"]
        last_error = watcher.last_error
    finally:
        idle_mod.idle_seconds = original_idle

    print("   window.idle_triggered 投递次数 =", arrived, "| last_error =", repr(last_error))
    check("真实 IdleWatcher 后台线程触发并送达 GUI 线程", arrived >= 1)
    check("整条链路未发生触发失败", last_error == "")
    window.hide()
    window._quit()


def test_trigger_failure_is_visible():
    """触发回调抛异常时必须留下可见痕迹（过去是静默 pass，故障查不出来）。"""
    import afk_core.idle as idle_mod

    from afk_core.events import EventBus

    bus = EventBus()
    bus.start_server()

    def boom(_sec):
        raise RuntimeError("prompt could not be shown")

    original_idle = idle_mod.idle_seconds
    idle_mod.idle_seconds = lambda: 9999.0
    try:
        watcher = IdleWatcher(
            lambda: {"idle": {"enabled": True, "minutes": 0.02, "cooldown_min": 0}},
            boom,
            bus,
            interval=0.05,
        )
        watcher._tick()
        time.sleep(0.2)
    finally:
        idle_mod.idle_seconds = original_idle

    seen = [
        e.get("message")
        for e in bus.history(project="core")
        if e.get("kind") == "idle" and e.get("level") == "error"
    ]
    bus.stop_server()

    print("   last_error =", repr(watcher.last_error))
    print("   error events =", seen)
    check("失败被记录到 last_error", "prompt could not be shown" in watcher.last_error)
    check("失败被写入事件流（不再是静默）", len(seen) >= 1)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    try:
        test_background_thread_delivery(app)
        test_real_watcher_triggers_end_to_end(app)
        test_trigger_failure_is_visible()
    finally:
        config.SETTINGS_YAML = _REAL_SETTINGS
        shutil.rmtree(_TMP, ignore_errors=True)
    print()
    if FAILURES:
        print("IDLE-FLOW {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("IDLE-FLOW PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
