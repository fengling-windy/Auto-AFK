import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config
from afk_core.events import EventBus


def main():
    # 隔离运行数据（事件总线与监测注入）：EventBus 默认用 config.LOGS_DIR /
    # config.EVENTS_FILE；不隔离的话测试夹具事件会写进用户真实
    # logs/events.jsonl（实测累计 3000+ 条）。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_bus_")
    try:
        return _run()
    finally:
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
    bus = EventBus()
    bus.start_server()
    script = ROOT / "tests" / "smoke_monitor.py"
    env = os.environ.copy()
    env["AFK_MONITOR"] = "1"
    env["AFK_PROJECT"] = "SmokeTest"
    env["AFK_RUN_ID"] = "smoke-run"
    env["AFK_EVENT_PORT"] = str(bus.port)
    env["AFK_EVENT_SPOOL"] = str(config.PROJECT_LOGS / "SmokeTest_spool.jsonl")
    env["PYTHONPATH"] = str(config.MONITOR_KIT)
    proc = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    time.sleep(1.0)
    events = bus.project_events("SmokeTest")
    kinds = {}
    for event in events:
        kinds[event.get("kind")] = kinds.get(event.get("kind"), 0) + 1
    print("exit code:", proc.returncode)
    print("event kinds:", json.dumps(kinds, ensure_ascii=False))
    print("has heartbeat/stage/error/done:", all(k in kinds for k in ("stage", "error", "done")))
    print("unhandled captured:", any("smoke unhandled" in (e.get("message") or "") for e in events))
    print("stderr tail:", proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "")
    ok = proc.returncode != 0 and all(k in kinds for k in ("stage", "error", "done"))
    print("SMOKE", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
