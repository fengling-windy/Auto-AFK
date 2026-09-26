import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_stop_error_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    import psutil

    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    started = time.time()
    run = supervisor.start_project("StopErrorProject")
    run.finished_event.wait(timeout=120)
    elapsed = time.time() - started
    pid = run.process.pid if run.process else None
    time.sleep(1)
    killed = not psutil.pid_exists(pid) if pid else False
    reason_ok = "运行出错" in (run.last_message or "")
    print("status:", run.status, "elapsed:", int(elapsed), "exit:", run.exit_code, "killed:", killed)
    print("reason:", (run.last_message or "")[:90])
    ok = run.status == "failed" and elapsed < 60 and killed and reason_ok
    print("STOP-ERROR", "PASS" if ok else "FAIL")
    supervisor.stop_all()
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
