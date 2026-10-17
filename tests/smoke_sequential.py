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
    # 隔离运行数据：链成功会写 logs/last_chain_success.json（今日已完成），
    # 写进真实目录会导致用户当天空闲自动挂机被误跳过。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_seq_")
    try:
        return _run()
    finally:
        # 先等监督线程（afk-supervise-*）收尾，否则其 _persist()
        # 会写进尚未隔离的真实 logs（见 smoke_isolation）
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
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
    # 关键：chain_status 变终态时链线程可能仍在收尾（写完成标记）。
    # 必须等线程真正退出，否则 restore() 会把隔离目录换回真实目录，
    # 链线程随后把「今日已完成」写进用户真实 logs（实测会出现）。
    from tests import smoke_isolation

    smoke_isolation.wait_chain_idle(supervisor, timeout=60)
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
