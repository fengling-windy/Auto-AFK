import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_hang_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    import psutil

    bus = EventBus()
    bus.start_server()
    settings = config.settings()
    settings.setdefault("repair", {})["retry_delay_sec"] = 1
    supervisor = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))
    run = supervisor.start_project("HungProject")
    deadline = time.time() + 300
    reports = []
    current = run
    # 等待条件必须包含 attempt >= 2：第一次 hang 时 run 已 terminal，
    # 但 repair 的重试（attempt=2）还在异步进行，过早跳出会读到 attempt=1。
    while time.time() < deadline:
        reports = list((ROOT / "logs" / "reports").glob("*_HungProject"))
        current = supervisor.runs.get("HungProject")
        if (
            reports
            and current is not None
            and current.terminal
            and not current.alive
            and current.attempt >= 2
        ):
            break
        time.sleep(2)
    pid = run.process.pid if run.process else None
    time.sleep(2)
    killed = not psutil.pid_exists(pid) if pid else False
    print("status:", current.status, "attempt:", current.attempt, "exit:", current.exit_code)
    print("first run process killed:", killed)
    print("report dir:", current.report_dir)
    print("report bundles:", len(reports))
    reasons = [e.get("message") for e in bus.history() if e.get("kind") == "repair"]
    print("repair events:", reasons)
    ok = current.status == "hang" and current.attempt == 2 and killed and reports and any("hang-detected" in str(r) for r in reasons)
    print("HANG-FLOW", "PASS" if ok else "FAIL")
    supervisor.stop_all()
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
