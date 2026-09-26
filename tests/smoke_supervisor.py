import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    bus = EventBus()
    bus.start_server()
    settings = config.settings()
    settings.setdefault("repair", {})["enabled"] = True
    settings["repair"]["max_rounds"] = 1
    repair = RepairEngine(bus, settings)
    supervisor = Supervisor(bus, settings=settings, repair=repair)
    run = supervisor.start_project("SmokeProject")
    run.finished_event.wait(timeout=600)
    time.sleep(1.0)
    print("status:", run.status, "exit:", run.exit_code)
    print("report dir:", run.report_dir)
    reports = list((ROOT / "logs" / "reports").glob("*_SmokeProject"))
    print("report bundles:", len(reports))
    ok = run.status == "failed" and reports
    print("SUPERVISOR", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
