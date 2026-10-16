import argparse
import json
import sys
import time
import urllib.request

from . import config, doctor, report
from .events import EventBus
from .repair import RepairEngine
from .supervisor import Supervisor


def build_runtime():
    bus = EventBus()
    bus.start_server()
    repair = RepairEngine(bus, config.settings())
    supervisor = Supervisor(bus, repair=repair)
    return bus, supervisor, repair


def _server_info():
    try:
        info = json.loads((config.LOGS_DIR / "event_server.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    pid = info.get("pid")
    if pid:
        try:
            import psutil

            if not psutil.pid_exists(int(pid)):
                return None
        except Exception:
            return None
    return info if info.get("port") else None


def _remote_get(path, timeout=3):
    info = _server_info()
    if not info:
        return None
    try:
        with urllib.request.urlopen("http://127.0.0.1:{0}{1}".format(info["port"], path), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _remote_post(path, payload, timeout=5):
    info = _server_info()
    if not info:
        return None
    req = urllib.request.Request(
        "http://127.0.0.1:{0}{1}".format(info["port"], path),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-AFK-Token": str(info.get("token", ""))},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _print_event(event):
    ts = time.strftime("%H:%M:%S", time.localtime(event.get("ts", time.time())))
    print("[{0}] [{1}] [{2}] {3}".format(ts, event.get("project", "core"), event.get("level", "info"), event.get("message", "")))


def cmd_run(args):
    bus, supervisor, _ = build_runtime()
    q = bus.subscribe()
    if getattr(args, "background", False):
        supervisor.start_chain(background_only=True)
    else:
        supervisor.run_chain(args.groups or None)
    while True:
        try:
            _print_event(q.get(timeout=1.0))
        except Exception:
            pass
        if supervisor.chain_status in ("success", "failed", "stopped") and not any(
            r.alive for r in supervisor.runs.values()
        ):
            break
    print("chain status:", supervisor.chain_status)
    return 0 if supervisor.chain_status == "success" else 1


def cmd_start(args):
    bus, supervisor, _ = build_runtime()
    run = supervisor.start_project(args.project)
    if run is None:
        return 1
    run.finished_event.wait()
    print("status:", run.status, "exit:", run.exit_code)
    return 0 if run.status in ("success", "success_unverified") else 1


def cmd_stop(args):
    remote = _remote_post("/command", {"action": "stop", "project": args.project})
    if remote is not None:
        ok = bool(remote.get("ok"))
        print("stopped" if ok else "not running ({0})".format(remote.get("error", "remote")))
        return 0 if ok else 1
    bus, supervisor, _ = build_runtime()
    ok = supervisor.stop_project(args.project)
    print("stopped" if ok else "not running")
    return 0


def cmd_stop_chain(args):
    remote = _remote_post("/command", {"action": "soft_stop"})
    if remote is None:
        print("no running Auto-AFK instance found")
        return 1
    print("soft stop requested: running projects will finish, no new groups will start")
    return 0


def cmd_stats(args):
    stats = report.duration_stats(limit=args.limit)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0
    if not stats:
        print("no duration records yet (logs\\durations.jsonl)")
        return 0
    print("{0:<28} {1:>6} {2:>10} {3:>10} {4:>10} {5:<12}".format(
        "project", "runs", "avg(s)", "max(s)", "last(s)", "last_status"))
    for key, item in sorted(stats.items()):
        print("{0:<28} {1:>6} {2:>10.1f} {3:>10.1f} {4:>10.1f} {5:<12}".format(
            key, item["runs"], item["avg_sec"], item["max_sec"], item["last_sec"],
            str(item.get("last_status") or "")))
    return 0


def cmd_gui(args):
    remote = _remote_post("/command", {"action": "open_gui", "project": args.project})
    if remote is not None:
        ok = bool(remote.get("ok"))
        print("GUI opened" if ok else "failed ({0})".format(remote.get("error", "remote")))
        return 0 if ok else 1
    bus, supervisor, _ = build_runtime()
    ok = supervisor.open_project_gui(args.project)
    print("GUI opened" if ok else "failed")
    return 0 if ok else 1


def cmd_status(args):
    remote = _remote_get("/status")
    if remote and "runs" in remote:
        snapshot = remote
        snapshot.setdefault("pending", [])
        snapshot.setdefault("event_port", (_server_info() or {}).get("port"))
    else:
        bus, supervisor, _ = build_runtime()
        snapshot = supervisor.status_snapshot()
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print("chain:", snapshot["chain_status"])
        for key, run in snapshot["runs"].items():
            print("{0:<22} {1:<18} exit={2} attempt={3}".format(key, run["status"], run["exit_code"], run["attempt"]))
    return 0


def cmd_doctor(args):
    results = doctor.run_checks()
    if args.json:
        print(json.dumps({"summary": doctor.summary(results), "checks": results}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print("[{0:<4}] {1:<28} {2}".format(item["status"], item["name"], item.get("detail", "")))
        print(json.dumps(doctor.summary(results), ensure_ascii=False))
    return 1 if any(item["status"] == "fail" for item in results) else 0


def cmd_reports(args):
    items = report.list_reports(limit=args.limit)
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        for item in items:
            print("{0:<40} {1:<20} {2}".format(item["name"], item.get("project", ""), item.get("reason", "")))
    return 0


def cmd_update(args):
    from .emulator import MuMuController
    from .updater import Updater

    bus, _, _ = build_runtime()
    if args.emulator:
        settings = config.settings()
        emu_cfg = settings.setdefault("pre_run", {}).setdefault("emulator", {})
        if getattr(args, "emulator_gui", False):
            emu_cfg["updater_mode"] = "gui"
        elif getattr(args, "emulator_silent", False):
            emu_cfg["updater_mode"] = "silence"
        print(json.dumps(MuMuController(bus, settings).update_check(), ensure_ascii=False))
    if args.projects:
        updater = Updater(bus, config.settings())
        for result in updater.update_all_projects():
            print(json.dumps(result, ensure_ascii=False))
    for event in bus.history(project=None)[-80:]:
        if event.get("kind") in ("update", "emulator"):
            _print_event(event)
    return 0


def cmd_idle(args):
    from . import completion
    from . import idle as idle_mod

    if getattr(args, "clear_completion", False):
        completion.clear()
        print("daily completion marker cleared")
    if args.on or args.off:
        settings = config.settings()
        idle_cfg = settings.setdefault("idle", {})
        idle_cfg["enabled"] = bool(args.on)
        config.save_yaml(config.SETTINGS_YAML, settings)
    watcher = idle_mod.IdleWatcher(config.settings, lambda sec: None)
    snapshot = watcher.status()
    snapshot["completion"] = completion.completion_info()
    snapshot["skip_if_completed_today"] = bool(
        (config.settings().get("idle") or {}).get("skip_if_completed_today", True)
    )
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0
    print("idle detection : {0}".format("supported" if snapshot["supported"] else "unsupported"))
    print("enabled        : {0}".format(snapshot["enabled"]))
    print("idle now       : {0}".format(snapshot["idle_text"]))
    print("threshold      : {0}".format(idle_mod.format_idle(snapshot["threshold_sec"])))
    print("armed          : {0}".format(snapshot["armed"]))
    print("completed today: {0} (skip={1})".format(
        snapshot["completion"]["completed_today"], snapshot["skip_if_completed_today"]))
    if snapshot.get("last_skip_reason"):
        print("last skip      : {0}".format(snapshot["last_skip_reason"]))
    if snapshot.get("last_error"):
        print("last error     : {0}".format(snapshot["last_error"]))
    return 0


def cmd_autostart(args):
    from . import autostart

    if args.action == "on":
        result = autostart.apply(True, args.mode or autostart.MODE_TRAY)
    elif args.action == "off":
        result = autostart.apply(False, args.mode or autostart.MODE_TRAY)
    else:
        result = autostart.status()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    if not result.get("supported", True):
        print("autostart is only supported on Windows")
        return 1
    print("enabled : {0}".format(result.get("enabled")))
    print("mode    : {0}".format(result.get("mode_label", result.get("mode", ""))))
    print("command : {0}".format(result.get("command", "")))
    print("key     : {0}".format(result.get("run_key", "")))
    if not result.get("ok", True):
        print("error   : {0}".format(result.get("error", "")))
        return 1
    return 0


def cmd_patch(args):
    import importlib.util

    spec = importlib.util.spec_from_file_location("afk_patcher", config.MONITOR_KIT / "patcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    argv = [args.action]
    for project in args.projects or []:
        argv += ["--project", project]
    if args.dry_run:
        argv.append("--dry-run")
    if args.json:
        argv.append("--json")
    return module.main(argv)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="afk", description="Auto-AFK supervisor CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the full chain")
    p_run.add_argument("--groups", nargs="*", help="specific group ids")
    p_run.add_argument(
        "--background",
        action="store_true",
        help="only run the groups marked background: true (tasks that do not grab the mouse)",
    )
    p_run.set_defaults(func=cmd_run)

    p_start = sub.add_parser("start", help="start a single project")
    p_start.add_argument("project")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="stop a single project")
    p_stop.add_argument("project")
    p_stop.set_defaults(func=cmd_stop)

    p_gui = sub.add_parser("gui", help="open a project's own config GUI")
    p_gui.add_argument("project")
    p_gui.set_defaults(func=cmd_gui)

    p_stop_chain = sub.add_parser(
        "stop-chain",
        help="soft stop: running projects finish, no new groups start",
    )
    p_stop_chain.set_defaults(func=cmd_stop_chain)

    p_stats = sub.add_parser("stats", help="per-project run duration stats")
    p_stats.add_argument("--limit", type=int, default=500)
    p_stats.add_argument("--json", action="store_true")
    p_stats.set_defaults(func=cmd_stats)

    p_status = sub.add_parser("status", help="show status")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_doctor = sub.add_parser("doctor", help="environment checks")
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=cmd_doctor)

    p_reports = sub.add_parser("reports", help="list error reports")
    p_reports.add_argument("--limit", type=int, default=50)
    p_reports.add_argument("--json", action="store_true")
    p_reports.set_defaults(func=cmd_reports)

    p_update = sub.add_parser("update", help="update projects and emulator")
    p_update.add_argument("--no-projects", action="store_false", dest="projects", default=True)
    p_update.add_argument("--no-emulator", action="store_false", dest="emulator", default=True)
    p_update.add_argument("--emulator-gui", action="store_true", help="open MuMu updater UI (default from settings)")
    p_update.add_argument("--emulator-silent", action="store_true", help="silent MuMu update check only, no UI")
    p_update.set_defaults(func=cmd_update)

    p_patch = sub.add_parser("patch", help="monitor patch manager")
    p_patch.add_argument("action", choices=["apply", "revert", "verify"])
    p_patch.add_argument("--projects", nargs="*")
    p_patch.add_argument("--dry-run", action="store_true")
    p_patch.add_argument("--json", action="store_true")
    p_patch.set_defaults(func=cmd_patch)

    p_idle = sub.add_parser("idle", help="idle-triggered auto run (status / on / off)")
    p_idle.add_argument("--on", action="store_true", help="enable idle auto start")
    p_idle.add_argument("--off", action="store_true", help="disable idle auto start")
    p_idle.add_argument(
        "--clear-completion",
        action="store_true",
        help="clear today's completion marker so idle auto-run happens again today",
    )
    p_idle.add_argument("--json", action="store_true")
    p_idle.set_defaults(func=cmd_idle)

    p_auto = sub.add_parser("autostart", help="boot autostart entry (HKCU Run key)")
    p_auto.add_argument("action", nargs="?", default="status", choices=["on", "off", "status"])
    p_auto.add_argument(
        "--mode",
        choices=["tray", "admin"],
        default=None,
        help="tray: silent, no UAC; admin: elevated (one UAC prompt at boot)",
    )
    p_auto.add_argument("--json", action="store_true")
    p_auto.set_defaults(func=cmd_autostart)

    args = parser.parse_args(argv)
    _housekeeping_once()
    return args.func(args)


# 只在会「真正产生新日志」的命令前做维护；纯查询命令不触发（避免拖慢 status 之类）
_HOUSEKEEPING_COMMANDS = {"run", "start", "update"}


def _housekeeping_once():
    """CLI 启动时做一次日志维护（截断过大的 events.jsonl / durations.jsonl 等）。

    背景：过去只有 GUI 启动时才调用 cleanup_logs，导致长期只用 CLI 时
    events.jsonl 会无限增长（实测已达 25 MB，且 events_max_mb=50 的截断永不生效）。
    """
    import sys as _sys

    argv = _sys.argv[1:]
    command = argv[0] if argv and not argv[0].startswith("-") else ""
    if command not in _HOUSEKEEPING_COMMANDS:
        return
    try:
        from .housekeeping import cleanup_logs

        cleanup_logs(None, config.settings())
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
