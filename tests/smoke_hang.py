import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config
from afk_core.monitor import RunWatcher


def main():
    spec = {
        "success_patterns": ["done"],
        "error_patterns": ["boom"],
        "finish_patterns": ["finished"],
        "warn_patterns": [],
    }
    watcher = RunWatcher("StuckTest", "run1", spec)
    watcher.last_activity = time.time() - 7200
    for _ in range(5):
        watcher.feed({"run_id": "run1", "kind": "heartbeat", "level": "debug", "message": ""})
    after_heartbeats = watcher.hang_seconds()
    print("hang seconds after heartbeats:", int(after_heartbeats))
    watcher.feed({"run_id": "run1", "kind": "log", "level": "info", "message": "工作日志一行"})
    after_log = watcher.hang_seconds()
    print("hang seconds after real log:", int(after_log))
    print("classify:", watcher.classify(0, hang=after_heartbeats > 900))
    ok = after_heartbeats > 900 and after_log < 5
    print("HANG-DETECT", "PASS" if ok else "FAIL")
    rules = (config.repair_rules() or {}).get("rules", [])
    ids = [r.get("id") for r in rules]
    print("hang rule present:", "hang-detected" in ids)
    return 0 if ok and "hang-detected" in ids else 1


if __name__ == "__main__":
    sys.exit(main())
