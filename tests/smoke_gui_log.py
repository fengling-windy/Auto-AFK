import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_gui_log_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    # 隔离运行数据（GUI 日志区）：避免把 state.json / durations.jsonl /
    # errors.jsonl 写进用户真实 logs——用户可能正在挂机，那会污染其运行状态。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_guilog_")
    try:
        return _run()
    finally:
        # 先等监督线程（afk-supervise-*）收尾，否则其 _persist()
        # 会写进尚未隔离的真实 logs（见 smoke_isolation）
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
    import psutil

    bus = EventBus()
    bus.start_server()
    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    supervisor = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))
    run = supervisor.start_project("GuiProject")
    deadline = time.time() + 120
    while time.time() < deadline:
        current = supervisor.runs.get("GuiProject")
        if current is not None and current.terminal and not current.alive:
            break
        time.sleep(0.5)
    current = supervisor.runs.get("GuiProject")
    pid = run.process.pid if run.process else None
    time.sleep(1)
    killed = not psutil.pid_exists(pid) if pid else False
    print("status:", current.status, "by_pattern:", current.finished_by_pattern, "killed:", killed)
    ok = current.status == "success" and current.finished_by_pattern and killed
    print("GUI-LOG-STOP", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
