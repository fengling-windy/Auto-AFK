import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_seq_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    bus = EventBus()
    bus.start_server()
    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    supervisor = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))
    supervisor.run_chain()
    deadline = time.time() + 120
    last = None
    while time.time() < deadline:
        snapshot = {k: (v.status, v.terminal) for k, v in supervisor.runs.items()}
        if snapshot != last:
            print("state:", snapshot, "chain:", supervisor.chain_status)
            last = snapshot
        if supervisor.chain_status in ("success", "failed", "stopped") and not any(r.alive for r in supervisor.runs.values()):
            break
        time.sleep(0.5)
    a = supervisor.runs.get("SeqA")
    b = supervisor.runs.get("SeqB")
    if a is None or b is None:
        print("SEQUENTIAL FAIL (missing runs)")
        print("chain:", supervisor.chain_status, "runs:", list(supervisor.runs.keys()))
        supervisor.stop_all()
        bus.stop_server()
        return 1
    print("chain:", supervisor.chain_status)
    print("A started={0:.1f} finished={1:.1f}".format(a.started_at or 0, a.finished_at or 0))
    print("B started={0:.1f} finished={1:.1f}".format(b.started_at or 0, b.finished_at or 0))
    sequential = (b.started_at or 0) >= (a.finished_at or 0) - 1.0
    ok = supervisor.chain_status == "success" and a.status == "success" and b.status == "success" and sequential
    print("sequential:", sequential)
    print("SEQUENTIAL", "PASS" if ok else "FAIL")
    if not ok:
        supervisor.stop_all()
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
