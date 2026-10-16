"""空闲自动挂机 / 开机自启 / 桌面小组件 / 三选一提示框 的冒烟测试。

覆盖：
  1. IdleWatcher 阈值、武装/解除武装、冷却、is_busy 短路（不依赖真实空闲时间）
  2. 三选一提示框：三个按钮的决策、超时默认动作、Esc/关闭=停止、快捷键
  3. autostart：命令行拼装、status 结构（只读，不写注册表）
  4. supervisor：background_group_ids / chain_running / start_chain(background_only)
     只跑 background 组，远程 /command 的 chain 动作
  5. gui.widget.DesktopWidget.tick() 的状态与倒计时文案

用法（离屏、无需真实空闲时间）：
    QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR="C:\\Windows\\Fonts" \
        .venv/Scripts/python.exe tests/smoke_idle.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import autostart, config, idle as idle_mod
from afk_core.idle import IdleWatcher, format_idle

FAILURES = []


def check(name, ok):
    print("{0} {1}".format("PASS" if ok else "FAIL", name))
    if not ok:
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# 1. IdleWatcher
# ---------------------------------------------------------------------------

def test_idle_watcher():
    fired = []
    fake = {"value": 0.0}
    original = idle_mod.idle_seconds
    idle_mod.idle_seconds = lambda: fake["value"]

    settings = {"idle": {"enabled": True, "minutes": 30, "prompt_sec": 30, "cooldown_min": 30}}
    watcher = IdleWatcher(lambda: settings, lambda sec: fired.append(sec), interval=0.05)

    check("阈值换算正确", watcher.threshold_seconds() == 1800.0)
    check("提示秒数读取正确", watcher.prompt_seconds() == 30)
    check("冷却秒数读取正确", watcher.cooldown_seconds() == 1800.0)

    # 未达阈值：不触发
    fake["value"] = 100.0
    watcher._tick()
    check("未达阈值不触发", fired == [])

    # 达到阈值：触发一次
    fake["value"] = 2000.0
    watcher._tick()
    check("达到阈值触发一次", len(fired) == 1)
    check("触发时回传空闲秒数", fired and fired[0] == 2000.0)

    # 继续空闲：已解除武装 + 冷却中，不再重复触发
    fake["value"] = 2100.0
    watcher._tick()
    watcher._tick()
    check("同一次离开不重复触发", len(fired) == 1)

    # 用户回来：重新武装
    fake["value"] = 5.0
    watcher._tick()
    fake["value"] = 3000.0
    watcher._tick()
    check("用户回来后再次触发", len(fired) == 2)

    # 冷却期未过：即使重新武装也不触发
    watcher2 = IdleWatcher(lambda: settings, lambda sec: fired.append(sec), interval=0.05)
    fired.clear()
    fake["value"] = 2000.0
    watcher2._tick()
    check("冷却期内首次仍触发", len(fired) == 1)
    watcher2.rearm()
    fake["value"] = 2500.0
    watcher2._tick()
    check("rearm 但冷却未过不触发", len(fired) == 1)
    watcher2._next_allowed_at = 0.0
    fake["value"] = 2600.0
    watcher2._tick()
    check("冷却清零后可再次触发", len(fired) == 2)

    # is_busy 短路：任务在跑时不提醒，并解除武装（避免任务刚结束又自动重开链）
    fired.clear()
    busy = {"value": True}
    watcher3 = IdleWatcher(lambda: settings, lambda sec: fired.append(sec),
                           interval=0.05, is_busy=lambda: busy["value"])
    fake["value"] = 2000.0
    watcher3._tick()
    check("任务进行中不触发", fired == [])

    # 任务结束但用户仍离开：本次离开不提醒（否则任务刚跑完就再次自动开链）
    watcher3._next_allowed_at = 0.0
    busy["value"] = False
    watcher3._tick()
    check("任务结束后同一次离开仍不触发", fired == [])

    # 用户回来（空闲回落）后重新武装并清零冷却
    fake["value"] = 3.0
    watcher3._tick()
    fake["value"] = 2000.0
    watcher3._tick()
    check("用户回来再离开后才触发", len(fired) == 1)

    # 未启用
    fired.clear()
    off = {"idle": {"enabled": False, "minutes": 30}}
    watcher4 = IdleWatcher(lambda: off, lambda sec: fired.append(sec), interval=0.05)
    fake["value"] = 5000.0
    watcher4._tick()
    check("未启用时不触发", fired == [])

    # status 结构
    snap = watcher3.status()
    check("status 含必要字段", {"supported", "enabled", "idle_sec", "threshold_sec", "armed"} <= set(snap))

    # 格式化
    check("format_idle 小时", format_idle(3700) == "1 小时 1 分")
    check("format_idle 分钟", format_idle(125) == "2 分 5 秒")
    check("format_idle 秒", format_idle(42) == "42 秒")

    idle_mod.idle_seconds = original


# ---------------------------------------------------------------------------
# 2. 三选一提示框
# ---------------------------------------------------------------------------

def test_idle_prompt(app):
    from gui.idle_prompt import (
        DECISION_ALL,
        DECISION_BACKGROUND,
        DECISION_NONE,
        DECISION_STOP,
        IdlePromptDialog,
        decide_on_timeout,
    )

    check("超时默认动作 = 运行全部任务", decide_on_timeout() == DECISION_ALL)

    d1 = IdlePromptDialog(idle_text="31 分 5 秒", prompt_sec=30)
    d1.stop_btn.click()
    check("点击停止得到 stop", d1.decision == DECISION_STOP)

    d2 = IdlePromptDialog(idle_text="31 分 5 秒", prompt_sec=30)
    d2.all_btn.click()
    check("点击立刻开始得到 all", d2.decision == DECISION_ALL)

    d3 = IdlePromptDialog(idle_text="31 分 5 秒", prompt_sec=30)
    d3.bg_btn.click()
    check("点击仅后台任务得到 background", d3.decision == DECISION_BACKGROUND)

    # 倒计时归零 → 自动按超时默认动作
    d4 = IdlePromptDialog(idle_text="30 分", prompt_sec=2)
    d4._tick()
    check("倒计时第一秒未决策", d4.decision == DECISION_NONE)
    d4._tick()
    check("倒计时归零自动运行全部任务", d4.decision == DECISION_ALL)

    # 关闭窗口 = 停止
    d5 = IdlePromptDialog(idle_text="30 分", prompt_sec=30)
    d5.reject()
    check("关闭窗口视为停止", d5.decision == DECISION_STOP)

    # 决策幂等：先点停止再点立刻开始，仍是停止
    d6 = IdlePromptDialog(idle_text="30 分", prompt_sec=30)
    d6.stop_btn.click()
    d6.all_btn.click()
    check("决策幂等（首次生效）", d6.decision == DECISION_STOP)

    # 快捷键
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    d7 = IdlePromptDialog(idle_text="30 分", prompt_sec=30)
    d7.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_B, Qt.NoModifier, "b"))
    check("快捷键 B = 仅后台任务", d7.decision == DECISION_BACKGROUND)

    d8 = IdlePromptDialog(idle_text="30 分", prompt_sec=30)
    d8.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    check("Esc = 停止", d8.decision == DECISION_STOP)


# ---------------------------------------------------------------------------
# 3. autostart（只读，不写注册表）
# ---------------------------------------------------------------------------

def test_autostart():
    check("autostart supported() 在 Windows 上为真", autostart.supported() == (sys.platform == "win32"))

    cmd_tray = autostart.build_command(autostart.MODE_TRAY, ("--tray",))
    check("tray 模式用 launch_gui.vbs", "launch_gui.vbs" in cmd_tray)
    check("tray 模式带 --tray", cmd_tray.endswith("--tray"))
    check("tray 模式经 wscript 启动", cmd_tray.startswith("wscript.exe"))

    cmd_admin = autostart.build_command(autostart.MODE_ADMIN, ())
    check("admin 模式用 launch_admin.vbs", "launch_admin.vbs" in cmd_admin)

    unknown = autostart.build_command("nonsense", ())
    check("未知模式回落 tray", "launch_gui.vbs" in unknown)

    info = autostart.status()
    for key in ("supported", "enabled", "mode", "mode_label", "command", "value_name", "run_key"):
        check("status 含 {0}".format(key), key in info)
    check("run_key 指向 HKCU Run", info["run_key"] == "HKCU\\" + autostart.RUN_KEY)
    check("当前未启用时 enabled=False", info["enabled"] == (autostart.current() is not None))
    # 关键：本测试绝不写入注册表
    if not info["enabled"]:
        check("未启用时 command 为空", info["command"] == "")


# ---------------------------------------------------------------------------
# 4. supervisor：background 组与远程命令
# ---------------------------------------------------------------------------

def test_supervisor_background():
    config.PROJECTS_YAML = ROOT / "tests" / "smoke_idle_registry.yaml"

    import afk_core.emulator as emulator_mod
    import afk_core.updater as updater_mod
    from afk_core.events import EventBus
    from afk_core.repair import RepairEngine
    from afk_core.supervisor import Supervisor

    # 隔离运行数据：本测试会真实跑成功一条 background 链，链成功会写
    # logs/last_chain_success.json（今日已完成）；写进真实目录会让用户当天
    # 空闲自动挂机被误跳过。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_idle_bg_")

    # 隔离外部副作用：Supervisor.start_chain() 会从磁盘重新加载 settings（有意设计，
    # 让 GUI 改完配置立即生效），所以在内存里改 pre_run 无效。这里直接把 MuMu 更新
    # 检查和项目 git 更新打桩掉，否则一旦 MuMu 判定有新版本就会打开更新器并阻塞 600s。
    orig_emu_check = emulator_mod.MuMuController.update_check
    orig_update_all = updater_mod.Updater.update_all_projects
    emulator_mod.MuMuController.update_check = lambda self: {"mode": "stubbed"}
    updater_mod.Updater.update_all_projects = lambda self, should_stop=None: []

    bus = EventBus()
    bus.start_server()
    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    supervisor = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))

    check("识别 background 组", supervisor.background_group_ids() == ["bg_group"])

    # 未跑时不算忙
    check("空闲时 chain_running=False", supervisor.chain_running() is False)

    started = []
    original = supervisor.run_chain

    def fake_run_chain(group_ids=None, on_finish=None):
        started.append(group_ids)
        return True

    supervisor.run_chain = fake_run_chain
    ok = supervisor.start_chain(background_only=True)
    check("仅后台模式返回成功", ok is True)
    check("仅后台模式只传 background 组", started == [["bg_group"]])

    started.clear()
    supervisor.start_chain(background_only=False)
    check("全量模式不传组过滤", started == [None])

    # 远程命令：chain 动作
    result = supervisor._handle_command({"action": "chain", "background": True})
    check("远程 chain 命令走 background", result.get("ok") is True and started[-1] == ["bg_group"])

    result = supervisor._handle_command({"action": "status"})
    check("远程 status 返回快照", result.get("ok") is True and "chain_status" in result.get("status", {}))

    supervisor.run_chain = original

    # 真正跑一次 background-only，验证 fg_group 从未启动
    ok = supervisor.start_chain(background_only=True)
    check("真实 background 链启动", ok is True)
    deadline = time.time() + 120
    while time.time() < deadline:
        if supervisor.chain_status in ("success", "failed", "stopped") and not any(
            r.alive for r in supervisor.runs.values()
        ):
            break
        time.sleep(0.3)
    # 等链线程真正退出，避免 guard.restore() 抢在写完成标记之前（见 smoke_isolation）
    smoke_isolation.wait_chain_idle(supervisor, timeout=60)
    bga = supervisor.runs.get("BgA")
    fgb = supervisor.runs.get("FgB")
    check("background 组成员已运行", bga is not None and bga.status == "success")
    check("前台组从未启动（不占用鼠标）", fgb is None)
    check("链结果 success", supervisor.chain_status == "success")

    supervisor.stop_all()
    bus.stop_server()

    emulator_mod.MuMuController.update_check = orig_emu_check
    updater_mod.Updater.update_all_projects = orig_update_all
    guard.restore()


# ---------------------------------------------------------------------------
# 5. 桌面小组件
# ---------------------------------------------------------------------------

def test_desktop_widget(app):
    from gui.widget import DesktopWidget

    widget = DesktopWidget(on_chain=lambda: None, on_stop=lambda: None, on_show=lambda: None)
    snapshot = {
        "chain_status": "running",
        "runs": {"March7thAssistant": {"status": "running"}},
    }
    idle_status = {"enabled": True, "supported": True, "idle_sec": 300.0, "cooldown_left_sec": 0}
    widget.tick(snapshot, idle_status, 1800.0)
    check("小组件显示运行中状态的胶囊", widget.chain_pill._text == "运行中")
    check("小组件列出正在运行的项目", "March7thAssistant" in widget.run_label.text())
    check("小组件显示空闲倒计时", "自动开始" in widget.idle_label.text())

    widget.tick({"chain_status": "idle", "runs": {}}, {"enabled": False}, 0)
    check("未启用空闲时提示未启用", widget.idle_label.text() == "空闲自动挂机：未启用")
    check("无运行项目时提示未运行", "未运行" in widget.run_label.text())

    widget.tick(
        {"chain_status": "idle", "runs": {}},
        {"enabled": True, "supported": True, "idle_sec": 2000.0, "cooldown_left_sec": 600},
        1800.0,
    )
    check("冷却期显示恢复时间", "分后恢复监听" in widget.idle_label.text())

    # 今日已完成：小组件应显示「不再自动执行」，优先于倒计时
    widget.tick(
        {"chain_status": "idle", "runs": {}},
        {"enabled": True, "supported": True, "idle_sec": 300.0, "cooldown_left_sec": 0},
        1800.0,
        completed_today=True,
    )
    check("今日已完成时小组件提示不再自动执行", "今日已完成" in widget.idle_label.text())
    widget.tick(
        {"chain_status": "idle", "runs": {}},
        {"enabled": True, "supported": True, "idle_sec": 300.0, "cooldown_left_sec": 0},
        1800.0,
        completed_today=False,
    )
    check("未完成时小组件恢复显示倒计时", "自动开始" in widget.idle_label.text())

    check("位置可读回", isinstance(widget.current_position(), list))
    widget.restore_position([10, 20])
    check("位置可恢复", widget.current_position() == [10, 20])
    widget.toggle_collapsed()
    check("可折叠", widget._collapsed and not widget._actions.isVisibleTo(widget))
    widget.toggle_collapsed()
    check("可展开", not widget._collapsed and widget._actions.isVisibleTo(widget))
    widget.hide()
    widget.deleteLater()


# ---------------------------------------------------------------------------

def main():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    test_idle_watcher()
    test_idle_prompt(app)
    test_autostart()
    test_supervisor_background()
    test_desktop_widget(app)

    print()
    if FAILURES:
        print("IDLE {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("IDLE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
