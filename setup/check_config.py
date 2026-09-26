import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config
from afk_core.events import EventBus
from afk_core.monitor import RunWatcher
from afk_core.supervisor import Supervisor


def main():
    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings())
    for key, spec in (config.registry().get("projects", {}) or {}).items():
        cmd, cwd = supervisor._build_command(key, spec)
        print("{0}: {1}".format(key, cmd))

    maaend_spec = config.registry()["projects"]["MaaEnd"]
    watcher = RunWatcher("MaaEnd", "check", maaend_spec)
    log_path = ROOT / "MaaEnd" / "install" / "debug" / "maafw.log"
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]
    watcher.feed_text("\n".join(lines))
    status = watcher.classify(0, require_finish=True, min_runtime_sec=60, runtime=3600)
    print("MaaEnd log check: warns={0} finish={1} errors={2} classify={3}".format(
        watcher.warn_count(), watcher.finish_hit, len(watcher.error_messages), status))
    print("first warn:", (watcher.warn_messages[0][:160] if watcher.warn_messages else ""))

    zzz_spec = config.registry()["projects"]["ZenlessZoneZero-OneDragon"]
    zzz_project = config.project_dir(zzz_spec)
    from afk_core.supervisor import ProjectRun

    zzz_run = ProjectRun("ZenlessZoneZero-OneDragon", zzz_spec)
    zzz_env = supervisor._build_env("ZenlessZoneZero-OneDragon", zzz_run, zzz_project)
    pythonpath = zzz_env.get("PYTHONPATH", "")
    env_ok = "monitor_kit" in pythonpath and str(zzz_project / "src") in pythonpath
    print("ZZZ PYTHONPATH ok:", env_ok)

    ok_watcher = RunWatcher("ZenlessZoneZero-OneDragon", "ok", zzz_spec)
    ok_watcher.feed_text("INFO 一条龙 开始运行\nINFO one dragon finished success=True")
    ok_status = ok_watcher.classify(0, require_finish=True, min_runtime_sec=120, runtime=1800)
    err_watcher = RunWatcher("ZenlessZoneZero-OneDragon", "err", zzz_spec)
    err_watcher.feed_text("ERROR 运行应用 ZzzOneDragon 失败")
    err_status = err_watcher.classify(0, require_finish=True, min_runtime_sec=120, runtime=1800)
    print("ZZZ success case:", ok_status, "| failure case:", err_status)

    bettergi_spec = config.registry()["projects"]["BetterGI"]
    bg_ok = RunWatcher("BetterGI", "ok", bettergi_spec)
    bg_ok.feed_text("[18:00:00.000] [INF] [test] 一条龙任务将从 每日奖励 开始执行\n[18:30:00.000] [INF] [test] 一条龙和配置组任务结束")
    bg_ok_status = bg_ok.classify(0, require_finish=True, min_runtime_sec=120, runtime=1800)
    bg_err = RunWatcher("BetterGI", "err", bettergi_spec)
    bg_err.feed_text("[18:00:00.000] [FTL] [test] UnHandle Exception [FATAL]")
    bg_err_status = bg_err.classify(0, require_finish=True, min_runtime_sec=120, runtime=1800)
    bg_warn = RunWatcher("BetterGI", "warn", bettergi_spec)
    bg_warn.feed_text("[18:00:05.000] [ERR] [test] 未找到传送点\n[18:30:00.000] [INF] [test] 一条龙和配置组任务结束")
    bg_warn_status = bg_warn.classify(0, require_finish=True, min_runtime_sec=1, runtime=1800)
    print("BetterGI finish case:", bg_ok_status, "| fatal case:", bg_err_status, "| warn case:", bg_warn_status, "warns:", bg_warn.warn_count())

    ok = (
        ok_status == "success"
        and err_status == "failed"
        and env_ok
        and bg_ok_status == "success"
        and bg_err_status == "failed"
        and bg_warn_status == "success"
        and bg_warn.warn_count() > 0
    )
    print("ZZZ/BetterGI", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
