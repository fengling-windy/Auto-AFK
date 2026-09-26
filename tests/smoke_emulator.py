import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_emulator_registry.yaml"

from afk_core.emulator import MuMuController
from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    bus = EventBus()
    bus.start_server()
    settings = config.settings()
    settings.setdefault("pre_run", {}).setdefault("emulator", {})["ready_timeout_sec"] = 300
    supervisor = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))
    run = supervisor.start_project("EmuSmoke")
    run.finished_event.wait(timeout=900)
    time.sleep(3)
    controller = MuMuController(bus, config.settings())
    stopped = not controller.is_running()
    print("run status:", run.status, "managed_emulator:", run.managed_emulator)
    print("emulator stopped after run:", stopped)
    ok = run.status == "success" and run.managed_emulator and stopped
    print("EMULATOR-MANAGE", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
