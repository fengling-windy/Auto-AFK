"""「今日已正常完成则不自动执行」测试。

判据是「今天成功跑完过一整条链」（logs/last_chain_success.json 的日期 == 今天）。
必须验证的边界：
  1. 链成功 -> 记标记；空闲到点时不触发（跳过）
  2. 跨天自动失效（昨天的标记不影响今天）
  3. 链失败 / 停止 -> 不记标记，仍会触发
  4. 只有「整链成功」才算；单个项目成功（未跑链）不算
  5. 设置里 skip_if_completed_today=false 时忽略该判定
  6. 用户回来后重新武装（下次离开仍按当日完成度判定）
"""

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_retry_registry.yaml"

from afk_core import completion
from afk_core.idle import IdleWatcher

FAILURES = []


def check(name, ok):
    print("{0} {1}".format("PASS" if ok else "FAIL", name))
    if not ok:
        FAILURES.append(name)


def isolated_logs():
    """把运行数据（含 DURATIONS_FILE / ERROR_INDEX / STATE_FILE）指到临时目录。

    用 smoke_isolation.isolate_runtime() 而不是只改 LOGS_DIR——后者漏掉了
    durations.jsonl / errors.jsonl / state.json，实测仍会把测试夹具项目写进
    用户真实 logs（用户正在挂机时会污染其运行状态）。
    返回临时目录路径，调用方照旧用 shutil.rmtree 清理（ignore_errors 无害）。
    """
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_completion_")
    return guard.tmp


def test_cached_query():
    """缓存版查询（GUI _poll 每 300ms 用它）必须与实时版本一致。"""
    import time as _time

    tmp = isolated_logs()
    try:
        check("缓存：初始为未完成", completion.completed_today_cached() is False)

        completion.mark_chain_success(["A"])
        check("缓存：写入标记后立即可见", completion.completed_today_cached() is True)
        check("缓存值与实时值一致", completion.completed_today_cached() == completion.completed_today())

        # 外部改动文件（模拟另一个进程/跨天）-> mtime 变化 -> 缓存必须自动更新
        path = completion.marker_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["date"] = "2000-01-01"
        _time.sleep(0.01)
        path.write_text(json.dumps(data), encoding="utf-8")
        check("缓存：外部改动后被感知（不返回陈旧值）",
              completion.completed_today_cached() is False)

        completion.mark_chain_success(["B"])
        check("缓存：重新标记后为已完成", completion.completed_today_cached() is True)
        completion.clear()
        check("缓存：清除后为未完成", completion.completed_today_cached() is False)
    finally:
        completion.clear()
        shutil.rmtree(tmp, ignore_errors=True)


def test_cached_query_is_cheap():
    """缓存查询不应每次读磁盘（GUI 热路径）。"""
    import time as _time

    tmp = isolated_logs()
    try:
        completion.mark_chain_success(["X"])
        # 预热缓存
        completion.completed_today_cached()
        n = 2000
        start = _time.perf_counter()
        for _ in range(n):
            completion.completed_today_cached()
        cached = (_time.perf_counter() - start) / n * 1000
        start = _time.perf_counter()
        for _ in range(n):
            completion.completed_today()
        plain = (_time.perf_counter() - start) / n * 1000
        print("   缓存 {0:.4f} ms/次 vs 未缓存 {1:.3f} ms/次".format(cached, plain))
        check("缓存查询明显更快（>=2x）", cached * 2 < plain)
    finally:
        completion.clear()
        shutil.rmtree(tmp, ignore_errors=True)


def test_marker_matches_day():
    tmp = isolated_logs()
    try:
        check("初始状态：今天未完成", completion.completed_today() is False)

        completion.mark_chain_success(["A", "B"])
        check("标记后：今天已完成", completion.completed_today() is True)

        info = completion.completion_info()
        check("标记记录当天日期", info["marker_date"] == info["today"])
        check("标记记录成功项目", info["marker_projects"] == ["A", "B"])

        # 伪造为昨天 -> 应自动失效（跨天无需清理）
        path = completion.marker_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["date"] = "2000-01-01"
        path.write_text(json.dumps(data), encoding="utf-8")
        check("昨天的标记跨天自动失效", completion.completed_today() is False)
        check("状态里体现为未完成", completion.completion_info()["completed_today"] is False)

        completion.clear()
        check("clear 之后标记消失", completion.completed_today() is False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_idle_skips_when_done():
    """空闲到点但今天已完成 -> 不触发；未完成 -> 触发。"""
    import afk_core.idle as idle_mod

    tmp = isolated_logs()
    fired = []
    original_idle = idle_mod.idle_seconds
    idle_mod.idle_seconds = lambda: 9999.0        # 恒定「已空闲很久」
    try:
        settings = {"idle": {"enabled": True, "minutes": 0.02, "cooldown_min": 0},
                    "skip_if_completed_today": True}

        # 未完成 -> 触发
        watcher = IdleWatcher(
            lambda: settings,
            lambda s: fired.append("triggered"),
            is_done_today=lambda: False,
        )
        watcher._tick()
        check("今天未完成时照常触发", fired == ["triggered"])
        check("未跳过时不记 skip 原因", watcher.last_skip_reason == "")

        # 已完成 -> 跳过
        fired.clear()
        watcher2 = IdleWatcher(
            lambda: settings,
            lambda s: fired.append("triggered"),
            is_done_today=lambda: True,
        )
        watcher2._tick()
        check("今天已完成时不触发（核心需求）", fired == [])
        check("跳过被记录原因", watcher2.last_skip_reason == "already completed today")

        # 已完成后用户回来再离开：仍按当日完成度判定（不重复打扰）
        idle_mod.idle_seconds = lambda: 0.0   # 必须真低于阈值（阈值=max(1.0, 0.02*60)=1.2s）
        watcher2._tick()                     # 用户回来：重新武装
        check("用户回来后清空跳过原因", watcher2.last_skip_reason == "")
        idle_mod.idle_seconds = lambda: 9999.0
        watcher2._tick()
        check("同一天完成后再离开仍不触发", fired == [])
    finally:
        idle_mod.idle_seconds = original_idle
        shutil.rmtree(tmp, ignore_errors=True)


def test_is_done_predicate_respects_setting():
    """skip_if_completed_today=false 时即使今天完成也应触发。

    把 config.SETTINGS_YAML 指向临时文件（沿用 smoke_shell_ui.py 的隔离约定），
    这样 GUI 真实的 _is_done_today（读 config.settings()）也能被测到。
    """
    import afk_core.idle as idle_mod

    tmp = isolated_logs()
    real_settings = config.SETTINGS_YAML
    tmp_settings = tmp / "settings.yaml"
    shutil.copy(real_settings, tmp_settings)
    config.SETTINGS_YAML = tmp_settings

    original_idle = idle_mod.idle_seconds
    idle_mod.idle_seconds = lambda: 9999.0
    try:
        completion.mark_chain_success(["X"])
        check("前置条件：标记已写入", completion.completed_today() is True)

        # 与 gui/main.py 的 _is_done_today 同构
        def is_done():
            enabled = bool((config.settings().get("idle") or {}).get("skip_if_completed_today", True))
            return enabled and completion.completed_today()

        settings = config.settings()
        settings.setdefault("idle", {})["enabled"] = True
        settings["idle"]["minutes"] = 0.02
        settings["idle"]["cooldown_min"] = 0
        settings["idle"]["skip_if_completed_today"] = True
        config.save_yaml(config.SETTINGS_YAML, settings)
        check("前置条件：跳过开关为开", is_done() is True)

        fired = []
        w = IdleWatcher(config.settings, lambda s: fired.append(1), is_done_today=is_done)
        w._tick()
        check("默认设置下（开启跳过）不触发", fired == [])

        # 关掉跳过开关：写回配置后应立刻生效
        settings["idle"]["skip_if_completed_today"] = False
        config.save_yaml(config.SETTINGS_YAML, settings)
        check("前置条件：跳过开关已关", is_done() is False)

        w2 = IdleWatcher(config.settings, lambda s: fired.append(1), is_done_today=is_done)
        w2._tick()
        check("关掉跳过开关后照常触发", fired == [1])
    finally:
        idle_mod.idle_seconds = original_idle
        config.SETTINGS_YAML = real_settings
        completion.clear()
        shutil.rmtree(tmp, ignore_errors=True)


def test_supervisor_marks_only_on_success():
    """链成功才记标记；失败/停止不记。"""
    from afk_core.events import EventBus
    from afk_core.repair import RepairEngine
    from afk_core.supervisor import Supervisor

    import afk_core.emulator as emulator_mod
    import afk_core.updater as updater_mod

    orig_emu = emulator_mod.MuMuController.update_check
    orig_upd = updater_mod.Updater.update_all_projects
    emulator_mod.MuMuController.update_check = lambda self: {"mode": "stubbed"}
    updater_mod.Updater.update_all_projects = lambda self, should_stop=None: []
    try:
        # 失败场景：smoke_retry_registry 里 FlakyNever 必定失败 -> 链 failed
        tmp = isolated_logs()
        try:
            bus = EventBus()
            bus.start_server()
            settings = config.settings()
            settings["pre_run"] = {"enabled": False}
            sup = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))
            sup.run_chain()
            deadline = time.time() + 240
            while time.time() < deadline:
                if sup.chain_status in ("success", "failed", "stopped") and not any(
                    r.alive for r in sup.runs.values()
                ):
                    break
                time.sleep(0.3)
            print("   [失败场景] chain_status =", sup.chain_status)
            check("链失败时记录 failed", sup.chain_status == "failed")
            check("链失败不写完成标记", completion.completed_today() is False)
            bus.stop_server()
        finally:
            completion.clear()
            shutil.rmtree(tmp, ignore_errors=True)

        # 成功场景：smoke_chain_registry 三个项目都必定成功 -> 链 success
        config.PROJECTS_YAML = ROOT / "tests" / "smoke_chain_registry.yaml"
        tmp2 = isolated_logs()
        try:
            bus2 = EventBus()
            bus2.start_server()
            settings2 = config.settings()
            settings2["pre_run"] = {"enabled": False}
            sup2 = Supervisor(bus2, settings=settings2, repair=RepairEngine(bus2, settings2))
            sup2.run_chain()
            deadline = time.time() + 240
            while time.time() < deadline:
                if sup2.chain_status in ("success", "failed", "stopped") and not any(
                    r.alive for r in sup2.runs.values()
                ):
                    break
                time.sleep(0.3)
            print("   [成功场景] chain_status =", sup2.chain_status)
            check("链成功时记录 success", sup2.chain_status == "success")
            check("链成功写入完成标记（核心需求）", completion.completed_today() is True)
            info = completion.completion_info()
            check("标记里记录了成功的项目", set(info["marker_projects"]) == {"SmokeA", "SmokeB", "SmokeC"})
            bus2.stop_server()
        finally:
            completion.clear()
            shutil.rmtree(tmp2, ignore_errors=True)
    finally:
        emulator_mod.MuMuController.update_check = orig_emu
        updater_mod.Updater.update_all_projects = orig_upd
        config.PROJECTS_YAML = ROOT / "tests" / "smoke_retry_registry.yaml"


def test_status_implies_marker_written():
    """不变量：chain_status 公布为 success 的那一刻，完成标记必须已经写好。

    这是竞态修复的核心：过去顺序是「先设 chain_status=success，后写标记」，
    任何看到终态就去读标记/切换目录的代码（GUI 刷新、测试的隔离守卫还原）
    都会踩中间窗口——实测会让标记写进用户真实 logs（当日空闲自动挂机被误跳过）。

    这里用最坏时序验证：高频轮询 chain_status，一看到 success 就立即检查标记是否存在。
    """
    import afk_core.emulator as emulator_mod
    import afk_core.updater as updater_mod
    from afk_core.events import EventBus
    from afk_core.repair import RepairEngine
    from afk_core.supervisor import Supervisor

    orig_emu = emulator_mod.MuMuController.update_check
    orig_upd = updater_mod.Updater.update_all_projects
    emulator_mod.MuMuController.update_check = lambda self: {"mode": "stubbed"}
    updater_mod.Updater.update_all_projects = lambda self, should_stop=None: []
    real_registry = config.PROJECTS_YAML
    config.PROJECTS_YAML = ROOT / "tests" / "smoke_chain_registry.yaml"
    tmp = isolated_logs()
    try:
        bus = EventBus()
        bus.start_server()
        settings = config.settings()
        settings["pre_run"] = {"enabled": False}
        sup = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))
        sup.run_chain()

        saw_success = False
        marker_at_success = None
        deadline = time.time() + 240
        while time.time() < deadline:
            if sup.chain_status == "success":
                saw_success = True
                marker_at_success = completion.marker_path().exists()
                break
            time.sleep(0.001)
        print("   看到 success 时标记已存在 =", marker_at_success)
        check("链确实到达 success", saw_success)
        check("公布 success 时完成标记已写好（不变量）", marker_at_success is True)
        bus.stop_server()
    finally:
        emulator_mod.MuMuController.update_check = orig_emu
        updater_mod.Updater.update_all_projects = orig_upd
        config.PROJECTS_YAML = real_registry
        completion.clear()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_marker_matches_day()
    test_cached_query()
    test_cached_query_is_cheap()
    test_idle_skips_when_done()
    test_is_done_predicate_respects_setting()
    test_supervisor_marks_only_on_success()
    test_status_implies_marker_written()
    print()
    if FAILURES:
        print("COMPLETION {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("COMPLETION PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
