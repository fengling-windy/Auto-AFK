"""真实打开项目 GUI 的测试 —— ⚠️ 会真实弹出项目界面窗口，需人工在场时运行。

它会 spawn 项目自身的配置 GUI（例如 BAAH 编辑器）并等它出现，屏幕上会闪现
窗口；不适合无人值守的回归套件。只想验证编排逻辑请用 smoke_guiopen.py
（模拟入口，不弹真实窗口）。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config
from afk_core.events import EventBus
from afk_core.supervisor import Supervisor


def main():
    import psutil

    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings())
    ok = supervisor.open_project_gui("BAAH")
    print("open_project_gui(BAAH):", ok)
    time.sleep(8)
    target = config.project_dir(config.registry()["projects"]["BAAH"])
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            cwd = proc.info.get("cwd")
            if cwd and Path(cwd) == Path(target):
                found.append((proc.info["pid"], (proc.info.get("cmdline") or [""])[0]))
        except Exception:
            continue
    print("BAAH-cwd processes:", found)
    gui_alive = any("python" in str(cmd).lower() for _, cmd in found)
    print("BAAH GUI ALIVE:", gui_alive)
    for pid, _ in found:
        try:
            psutil.Process(pid).kill()
        except Exception:
            pass
    bus.stop_server()
    return 0 if ok and gui_alive else 1


if __name__ == "__main__":
    sys.exit(main())
