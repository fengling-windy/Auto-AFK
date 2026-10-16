import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_chain_registry.yaml"

import afk_core.updater as updater_mod
from afk_core.events import EventBus
from afk_core.supervisor import Supervisor

CALLS = {"projects": 0, "emulator": 0}


def fake_update_all(self, should_stop=None):
    CALLS["projects"] += 1
    return []


def fake_emu_check(self):
    CALLS["emulator"] += 1


updater_mod.Updater.update_all_projects = fake_update_all
updater_mod.MuMuController_update_check = None
try:
    from afk_core.emulator import MuMuController

    MuMuController.update_check = fake_emu_check
except Exception:
    pass


def settings_with(at_startup):
    s = config.settings()
    s["pre_run"] = {
        "enabled": True,
        "at_startup": at_startup,
        "update_projects": True,
        "update_emulator": False,
    }
    return s


def wait_chain(supervisor, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if supervisor.chain_status in ("success", "failed", "stopped") and not any(
            r.alive for r in supervisor.runs.values()
        ):
            # 还要等链线程退出：chain_status 变终态时线程可能仍在写完成标记，
            # 若此时 restore() 隔离目录，标记会落到真实 logs（见 smoke_isolation）。
            from tests import smoke_isolation

            smoke_isolation.wait_chain_idle(supervisor, timeout=max(10, timeout // 4))
            return True
        time.sleep(0.3)
    return False


def main():
    # 隔离运行数据：本测试真实跑链（smoke_chain_registry 全成功），链成功会写
    # logs/last_chain_success.json；写进真实目录会让用户当天空闲自动挂机被误跳过。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_startup_")
    try:
        return _run()
    finally:
        guard.restore()


def _run():
    # case 1: at_startup=True -> projects update once at startup, chain skips it
    bus = EventBus()
    bus.start_server()
    s1 = Supervisor(bus, settings=settings_with(True))
    started = s1.run_startup_updates()
    s1.startup_update_done.wait(timeout=15)
    after_startup = CALLS["projects"]
    print("startup update ran:", started, "calls:", after_startup)
    s1.run_chain()
    chain_ok = wait_chain(s1)
    print("chain1:", s1.chain_status, "calls after chain:", CALLS["projects"])
    ok1 = started and after_startup == 1 and chain_ok and CALLS["projects"] == 1 and CALLS["emulator"] == 0
    bus.stop_server()

    # case 2: at_startup=False -> chain pre-run updates projects itself
    CALLS["projects"] = 0
    bus2 = EventBus()
    bus2.start_server()
    s2 = Supervisor(bus2, settings=settings_with(False))
    started2 = s2.run_startup_updates()
    s2.run_chain()
    chain_ok2 = wait_chain(s2)
    print("startup refused (at_startup=False):", started2 is False,
          "chain2:", s2.chain_status, "calls:", CALLS["projects"])
    ok2 = (started2 is False) and chain_ok2 and CALLS["projects"] == 1
    bus2.stop_server()

    # case 3: pre_run disabled -> startup refuses
    s3_settings = settings_with(True)
    s3_settings["pre_run"]["enabled"] = False
    bus3 = EventBus()
    bus3.start_server()
    s3 = Supervisor(bus3, settings=s3_settings)
    print("startup refused (pre_run disabled):", s3.run_startup_updates() is False)
    ok3 = s3.run_startup_updates() is False
    bus3.stop_server()

    ok = ok1 and ok2 and ok3
    print("STARTUP-UPDATE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
