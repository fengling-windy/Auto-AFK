import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_guiopen_registry.yaml"

from afk_core.events import EventBus
from afk_core.supervisor import Supervisor


def main():
    marker = ROOT / "logs" / "gui_open_marker.txt"
    if marker.exists():
        marker.unlink()
    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings())

    # no task should be started by opening the GUI
    ok = supervisor.open_project_gui("GuiProject")
    print("open_project_gui:", ok)
    deadline = time.time() + 30
    while time.time() < deadline and not marker.exists():
        time.sleep(0.3)

    gui_events = [e for e in bus.history() if e.get("kind") == "gui" and e.get("project") == "GuiProject"]
    run_alive = "GuiProject" in supervisor.runs and supervisor.runs["GuiProject"].alive
    marker_ok = marker.exists()
    content = marker.read_text(encoding="utf-8") if marker_ok else ""
    print("marker:", marker_ok, content.strip())
    print("gui event:", len(gui_events) == 1)
    print("task NOT started:", not run_alive and "GuiProject" not in supervisor.runs)

    # unknown project -> False
    ok2 = supervisor.open_project_gui("NoSuchProject")
    print("unknown project rejected:", ok2 is False)

    ok_all = ok and marker_ok and len(gui_events) == 1 and "GuiProject" not in supervisor.runs and ok2 is False
    print("GUI-OPEN", "PASS" if ok_all else "FAIL")
    bus.stop_server()
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
