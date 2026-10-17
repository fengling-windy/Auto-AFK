import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_classify_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor

EXPECTED = {
    "OkProject": "success",
    "FastProject": "failed",
    "ErrProject": "failed",
}


def main():
    # 隔离运行数据（完成/失败判定）：避免把 state.json / durations.jsonl /
    # errors.jsonl 写进用户真实 logs——用户可能正在挂机，那会污染其运行状态。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_classify_")
    try:
        return _run()
    finally:
        guard.restore()


def _run():
    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    results = {}
    for key in ("OkProject", "FastProject", "ErrProject"):
        run = supervisor.start_project(key)
        run.finished_event.wait(timeout=180)
        results[key] = run.status
        print("{0}: status={1} exit={2} reason={3}".format(key, run.status, run.exit_code, (run.last_message or "")[:90]))
        time.sleep(0.5)
    stdout_events = [e for e in bus.history() if (e.get("data") or {}).get("source") == "stdout"]
    print("stdout events captured:", len(stdout_events))
    ok = all(results.get(k) == v for k, v in EXPECTED.items()) and len(stdout_events) >= 3
    print("CLASSIFY", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
