"""端到端用户场景验证：开机自启 -> 空闲 -> 提示框 -> 自动挂机 -> 今日完成。

把用户实际会走的完整链路串起来跑一遍（影子根 + 存量替身，不碰真实项目）：

  1. 以 `--tray` 启动（这正是开机自启的启动方式），主窗口不显示；
  2. IdleWatcher 判定空闲达阈值 -> 触发 -> Signal 投递到 GUI 线程 -> 弹提示框；
  3. 提示框倒计时结束默认「运行全部任务」-> 走真实 _handle_idle_decision；
  4. 链成功 -> 写「今日已完成」标记 -> GUI 的 _is_done_today 能读到；
  5. 再次空闲 -> 因今日已完成而跳过（不再打扰用户）；
  6. 清除标记后恢复自动挂机（对应设置页「今天还没完成（恢复自动挂机）」按钮）。

这条链路跨越 idle/completion/gui 三个模块，是「空闲自动挂机」功能的整体验收。
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

_TMP = Path(tempfile.mkdtemp(prefix="afk_e2e_"))
_REAL = config.SETTINGS_YAML
shutil.copy(_REAL, _TMP / "settings.yaml")
config.SETTINGS_YAML = _TMP / "settings.yaml"

from PySide6.QtWidgets import QApplication

from afk_core import completion
from afk_core import idle as idle_mod
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
    guard = smoke_isolation.isolate_runtime(prefix="afk_user_flow_")

    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    settings["pre_run"]["at_startup"] = False
    settings["housekeeping"] = {"enabled": False}
    settings.setdefault("idle", {}).update(
        {"enabled": True, "minutes": 0.02, "prompt_sec": 30, "cooldown_min": 0}
    )
    config.save_yaml(config.SETTINGS_YAML, settings)

    # 1) 用 --tray 启动（开机自启的启动方式）
    saved_argv = list(sys.argv)
    sys.argv = ["main.py", "--tray", "--no-widget"]
    try:
        window = gm.MainWindow()
    finally:
        sys.argv = saved_argv
    pump(app, 250)
    check("1) --tray 启动（开机自启方式）且主窗口未显示", window.start_in_tray and not window.isVisible())

    # 2) 打桩：真实 IdleWatcher 线程 + 恒定空闲时间 + 提示框自动选「全部任务」
    import gui.idle_prompt as ip
    started = {"chain": 0}
    orig_start_chain = window.start_chain
    window.start_chain = lambda: started.__setitem__("chain", started["chain"] + 1)

    # 提示框：exec 时立刻接受（模拟倒计时结束 -> 默认 all）
    orig_exec = ip.IdlePromptDialog.exec
    ip.IdlePromptDialog.exec = lambda self: 0
    # 决策函数保持真实：超时默认 ALL（decide_on_timeout 的语义）
    decision_seen = {}
    orig_handle = window._handle_idle_decision

    def spy_handle(decision):
        decision_seen["d"] = decision
        return orig_handle(decision)

    window._handle_idle_decision = spy_handle

    original_idle_seconds = idle_mod.idle_seconds
    idle_mod.idle_seconds = lambda: 9999.0
    watcher = None
    try:
        # 真实 IdleWatcher 线程，短轮询
        watcher = idle_mod.IdleWatcher(
            config.settings, window._on_idle_trigger, window.bus,
            is_busy=window._chain_is_busy, is_done_today=window._is_done_today,
            interval=0.2,
        )
        watcher.start()
        pump(app, 1500)
    finally:
        idle_mod.idle_seconds = original_idle_seconds
        if watcher:
            watcher.stop()
        ip.IdlePromptDialog.exec = orig_exec

    print("   决策 =", decision_seen.get("d"), "| start_chain 调用 =", started["chain"])
    check("2) 空闲达阈值触发并进入提示框", decision_seen.get("d") is not None)
    check("3) 提示框超时默认「运行全部任务」", decision_seen.get("d") == ip.DECISION_ALL)
    check("3b) 决策后真的启动了任务链", started["chain"] == 1)

    # 4) 模拟链成功 -> 写「今日已完成」标记
    completion.mark_chain_success(["March7thAssistant", "BAAH"])
    check("4) 链成功后写入今日完成标记", completion.completed_today() is True)
    check("4b) GUI 能读到该标记（_is_done_today）", window._is_done_today() is True)

    # 5) 再次空闲 -> 因今日已完成而跳过
    fired2 = {"n": 0}
    orig2 = window._on_idle_trigger
    window._on_idle_trigger = lambda s: fired2.__setitem__("n", fired2["n"] + 1)
    idle_mod.idle_seconds = lambda: 9999.0
    try:
        w2 = idle_mod.IdleWatcher(
            config.settings, window._on_idle_trigger, window.bus,
            is_busy=window._chain_is_busy, is_done_today=window._is_done_today,
            interval=0.2,
        )
        w2.start()
        pump(app, 1200)
        w2.stop()
        print("   今日已完成后再次空闲的触发次数 =", fired2["n"], "| skip 原因 =", repr(w2.last_skip_reason))
        check("5) 今日已完成时再次空闲不再触发（核心需求）", fired2["n"] == 0)
        check("5b) 跳过被记录原因", w2.last_skip_reason == "already completed today")
    finally:
        idle_mod.idle_seconds = original_idle_seconds
        window._on_idle_trigger = orig2
        window.start_chain = orig_start_chain
        window._handle_idle_decision = orig_handle

    # 6) 清除标记后应恢复触发（用户手动「今天还没完成」）
    completion.clear()
    fired3 = {"n": 0}
    window._on_idle_trigger = lambda s: fired3.__setitem__("n", fired3["n"] + 1)
    idle_mod.idle_seconds = lambda: 9999.0
    try:
        w3 = idle_mod.IdleWatcher(
            config.settings, window._on_idle_trigger, window.bus,
            is_busy=window._chain_is_busy, is_done_today=window._is_done_today,
            interval=0.2,
        )
        w3.start()
        pump(app, 1200)
        w3.stop()
        print("   清除标记后触发次数 =", fired3["n"])
        check("6) 清除「今日已完成」后恢复自动挂机", fired3["n"] >= 1)
    finally:
        idle_mod.idle_seconds = original_idle_seconds

    window.hide()
    window._quit()
    config.SETTINGS_YAML = _REAL
    completion.clear()
    guard.restore()
    shutil.rmtree(_TMP, ignore_errors=True)

    print()
    print("E2E-USER-FLOW", "PASS" if not FAILURES else "FAIL ({0})".format(", ".join(FAILURES)))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
