import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_chain_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    supervisor.run_chain()
    deadline = time.time() + 300
    while time.time() < deadline:
        if supervisor.chain_status in ("success", "failed", "stopped") and not any(r.alive for r in supervisor.runs.values()):
            break
        time.sleep(0.5)
    runs = supervisor.runs
    a = runs.get("SmokeA")
    b = runs.get("SmokeB")
    c = runs.get("SmokeC")
    print("chain:", supervisor.chain_status)
    for key in ("SmokeA", "SmokeB", "SmokeC"):
        run = runs.get(key)
        if run:
            print("{0}: status={1} started={2:.2f} finished={3:.2f}".format(key, run.status, run.started_at or 0, run.finished_at or 0))
    ok = supervisor.chain_status == "success" and all(r and r.status == "success" for r in (a, b, c))
    if ok and a and b and c:
        parallel_overlap = abs((a.started_at or 0) - (b.started_at or 0)) < 2.0
        c_after = (c.started_at or 0) >= max(a.finished_at or 0, b.finished_at or 0) - 1.0
        print("parallel A/B:", parallel_overlap, "C after A+B:", c_after)
        ok = parallel_overlap and c_after
    print("CHAIN", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
