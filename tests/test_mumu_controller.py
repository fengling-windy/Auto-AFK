import sys
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
    controller = MuMuController(bus, config.settings())

    print("1) update_check (suppress prompt + version)")
    print(controller.update_check())

    print("2) ensure_running while already running")
    print(controller.ensure_running())

    print("3) stop")
    print("stopped:", controller.stop())
    print("is_running after stop:", controller.is_running())

    print("4) start fresh")
    result = controller.ensure_running()
    print(result)
    print("is_running after start:", controller.is_running())
    print("adb port:", controller.adb_port())

    for event in bus.history():
        if event.get("kind") == "emulator":
            print("[{0}] {1}".format(event.get("level"), event.get("message")))
    bus.stop_server()
    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    sys.exit(main())
