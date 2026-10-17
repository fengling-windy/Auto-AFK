import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_spawn_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    # 隔离运行数据（进程清理）：避免把 state.json / durations.jsonl /
    # errors.jsonl 写进用户真实 logs——用户可能正在挂机，那会污染其运行状态。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_cleanup_")
    try:
        return _run()
    finally:
        # 先等监督线程（afk-supervise-*）收尾，否则其 _persist()
        # 会写进尚未隔离的真实 logs（见 smoke_isolation）
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
    import psutil

    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    run = supervisor.start_project("SpawnProject")
    run.finished_event.wait(timeout=120)
    time.sleep(2.0)
    text = ""
    try:
        text = Path(run.out_log).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    match = re.search(r"child pid (\d+)", text)
    child_pid = int(match.group(1)) if match else None
    alive = psutil.pid_exists(child_pid) if child_pid else None
    print("run status:", run.status, "child pid:", child_pid, "alive after run:", alive)
    ok = run.status == "success" and child_pid is not None and not alive
    print("CLEANUP", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
