import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_window_registry.yaml"

from afk_core.events import EventBus
from afk_core.supervisor import Supervisor

# fake game window: visible for the first WINDOW_SECS of the test, then gone
WINDOW_SECS = 7.0
_STARTED = time.time()


def _fake_window_visible(self, keywords):
    return time.time() - _STARTED < WINDOW_SECS


def main():
    Supervisor._window_titles_contain = _fake_window_visible
    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings())
    run = supervisor.start_project("WindowProject")
    deadline = time.time() + 120
    while time.time() < deadline:
        if run is not None and run.terminal and not run.alive:
            break
        time.sleep(0.5)
    if run is None:
        print("WINDOW-FINISH FAIL (run is None)")
        bus.stop_server()
        return 1
    print("status:", run.status, "exit:", run.exit_code, "window_seen:", run.window_seen)
    durations = ROOT / "logs" / "durations.jsonl"
    has_duration = False
    try:
        has_duration = "WindowProject" in durations.read_text(encoding="utf-8")
    except OSError:
        pass
    print("duration recorded:", has_duration)
    ok = run.status == "success" and run.finished_by_pattern and run.window_seen and has_duration
    print("WINDOW-FINISH", "PASS" if ok else "FAIL")
    supervisor.stop_all()
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
