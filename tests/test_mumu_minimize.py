import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config
from afk_core.emulator import MuMuController
from afk_core.events import EventBus


def main():
    bus = EventBus()
    bus.start_server()
    settings = config.settings()
    settings.setdefault("pre_run", {}).setdefault("emulator", {})["ready_timeout_sec"] = 300
    controller = MuMuController(bus, settings)
    print("cleanup before test:", controller.stop())
    time.sleep(3)
    print("ensure_running:", controller.ensure_running())
    time.sleep(3)
    state = controller.window_state()
    print("window state:", state)
    events = [e.get("message", "") for e in bus.history() if e.get("kind") == "emulator"]
    shown = any("window shown" in m for m in events)
    minimized = any("minimized to taskbar" in m for m in events)
    print("show event:", shown, "minimize event:", minimized)
    ok = bool(state.get("found") and state.get("minimized"))
    print("MINIMIZE", "PASS" if ok else "FAIL")
    print("stop:", controller.stop())
    for event in bus.history():
        if event.get("kind") == "emulator":
            print("[{0}] {1}".format(event.get("level"), event.get("message")))
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
