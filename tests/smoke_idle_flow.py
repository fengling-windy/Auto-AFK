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


def build_window(app, show=True, tray_mode=False):
    """构造 MainWindow；tray_mode=True 时模拟 --tray 启动（不 show 主窗口）。"""
    # 用 isolate_runtime 覆盖全部运行数据路径（含 DURATIONS_FILE / ERROR_INDEX），
    # 否则测试夹具项目会写进用户真实 durations.jsonl / errors.jsonl。
    from tests import smoke_isolation

    smoke_isolation.isolate_runtime(prefix="afk_idle_flow_")
    config.PROJECT_LOGS.mkdir(parents=True, exist_ok=True)
    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    settings["pre_run"]["at_startup"] = False
    settings["housekeeping"] = {"enabled": False}
    settings.setdefault("idle", {}).update({"enabled": True, "minutes": 30, "prompt_sec": 30})
    config.save_yaml(config.SETTINGS_YAML, settings)
    saved_argv = list(sys.argv)
    if tray_mode:
        sys.argv = ["main.py", "--tray"]
    try:
        window = gm.MainWindow()
    finally:
        sys.argv = saved_argv
    if show:
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


def test_tray_mode_idle_prompt(app):
    """用户真实场景：开机自启 --tray 模式 + 空闲自动挂机 -> 提示框必须能显示。

    托盘模式下主窗口从不 show()，若空闲提示框依赖窗口可见，用户就永远不会
    看到提示、空闲挂机形同虚设。这里走真实 _prompt_idle（只把 dialog.exec
    打桩避免阻塞）验证窗口会被显示出来。
    """
    import gui.idle_prompt as ip

    window = build_window(app, show=False, tray_mode=True)
    check("--tray 启动时主窗口不显示", window.isVisible() is False)
    check("缓存了 start_in_tray 标记", window.start_in_tray is True)

    def bg_trigger():
        window._on_idle_trigger(1805.0)

    seen = {}
    captured = {}
    orig_exec = ip.IdlePromptDialog.exec

    def spy_exec(self):
        # 在真正的 exec 位置捕捉提示框状态：此时 dialog 已构造且窗口已显示，
        # 正是「用户能看到提示框」的那一刻（exec 返回后 _idle_dialog 会被置 None）。
        captured["idle_text"] = self._idle_text
        captured["remaining"] = getattr(self, "_remaining", None)
        captured["window_visible"] = window.isVisible()
        # 历史缺陷：_prompt_idle 只 exec() 不 start_countdown()，倒计时从未跑起来，
        # 界面写着「30 秒后未选择将自动运行」却会一直等人点按钮。
        captured["timer_active"] = self._timer.isActive()
        return 0

    ip.IdlePromptDialog.exec = spy_exec
    try:
        t = threading.Thread(target=bg_trigger, name="fake-idle", daemon=True)
        t.start()
        t.join(timeout=5)
        pump(app, 400)
        seen["visible_after"] = window.isVisible()
        seen["dialog_created"] = "idle_text" in captured
    finally:
        ip.IdlePromptDialog.exec = orig_exec

    print("   触发后窗口可见 =", seen.get("visible_after"),
          "| 弹窗时窗口可见 =", captured.get("window_visible"),
          "| 提示文案 =", repr(captured.get("idle_text")))
    check("空闲触发后主窗口被显示（提示框可见的前提）", seen.get("visible_after") is True)
    check("提示框确实被创建并进入弹窗流程", seen.get("dialog_created") is True)
    check("弹窗那一刻窗口已可见（用户能真正看到）", captured.get("window_visible") is True)
    check("提示框携带了空闲时长文案", "30 分" in str(captured.get("idle_text") or "")
          or "空闲" in str(captured.get("idle_text") or ""))
    check("提示框进入弹窗时倒计时已在跑（否则永远不会自动开始）",
          captured.get("timer_active") is True)
    check("托盘图标可用（用户可据此找回窗口）", window.tray is not None and window.tray.isVisible())
    window.hide()
    window._quit()


def test_idle_status_cache(app):
    """_idle_status_cached：读盘限速，但空闲秒数与冷却仍要跟手。

    历史开销：idle_watcher.status() 每次都要走 config.settings() 重新读一遍
    settings.yaml（实测 4.3ms）。桌面小组件开着时 _poll 每 300ms 取一次，
    光这一项就占掉主线程 1.4%。缓存后配置相关字段每 2 秒重算一次，空闲秒数
    每轮现算 —— 两者不能一起被缓存住，否则「空闲 5 分钟」的数字会卡住不动。
    """
    window = build_window(app)
    reads = {"n": 0}
    real_status = window.idle_watcher.status

    def counting_status():
        reads["n"] += 1
        return real_status()

    window.idle_watcher.status = counting_status
    try:
        window._idle_status_at = 0.0
        window._idle_status_cache = {}
        first = window._idle_status_cached()
        check("首次取空闲状态会读盘一次", reads["n"] == 1)
        check("首次就带上了阈值", first.get("threshold_sec") == 1800.0)

        for _ in range(5):
            window._idle_status_cached()
        check("2 秒内的连续轮询不再读盘（次数仍 {0}）".format(reads["n"]), reads["n"] == 1)

        # 空闲秒数必须每轮现算：把底层读数打桩成两个不同值
        import afk_core.idle as idle_mod

        real_idle = idle_mod.idle_seconds
        try:
            idle_mod.idle_seconds = lambda: 42.0
            window._idle_status_cached()
            got_a = window._idle_status_cached()
            idle_mod.idle_seconds = lambda: 99.0
            got_b = window._idle_status_cached()
        finally:
            idle_mod.idle_seconds = real_idle
        check("空闲秒数按轮刷新（缓存命中也要更新）",
              got_a.get("idle_sec") == 42.0 and got_b.get("idle_sec") == 99.0)
        check("空闲文案跟着秒数走", got_b.get("idle_text") == idle_mod.format_idle(99.0))

        # 过了 2 秒窗口要重读（否则改了设置永远不生效）
        window._idle_status_at = 0.0
        window._idle_status_cached()
        check("超出窗口后重新读盘", reads["n"] == 2)
    finally:
        window.idle_watcher.status = real_status
    window.hide()
    window._quit()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    try:
        test_background_thread_delivery(app)
        test_real_watcher_triggers_end_to_end(app)
        test_tray_mode_idle_prompt(app)
        test_trigger_failure_is_visible()
        test_idle_status_cache(app)
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
