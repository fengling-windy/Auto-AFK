"""模拟器管理集成测试 —— ⚠️ 需要真实 MuMu 环境，不要放进无人值守的回归套件。

它会真的**启动并关闭本机 MuMu 模拟器**（run.managed_emulator 为真表示编排器
接管了模拟器生命周期），因此：
  * 必须有可用的 MuMu 安装与足够的启动时间（ready_timeout_sec 被放宽到 300s，
    整体可能跑十几分钟，超时属正常）；
  * 会在屏幕上真实拉起模拟器窗口，可能影响正在进行的其它工作；
  * 请在确认可以占用模拟器时手动运行。

需要模拟器相关回归但不想动真机时，请用 tests/smoke_update_parallel.py 之类
的替身测试，或把 MuMuController.update_check / is_running 打桩。
"""

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
