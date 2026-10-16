"""Fault-tolerant chain scheduling: a failing project is skipped, the rest of
the chain still runs, the failures are re-run at the END of the chain, and no
project is ever launched more than max_attempts_per_project times in total.

Registry (tests/smoke_retry_registry.yaml) has four sequential groups:
  FlakyOnce  fails run #1, succeeds run #2   -> retried at the end, 2 launches
  FlakyNever always fails                    -> exactly 3 launches (cap), chain fails
  OkC        succeeds first time             -> exactly 1 launch, never retried
  HangOnce   hangs run #1, succeeds run #2   -> repaired + retried, 2 launches
"""

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_retry_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor

STATE_DIR = ROOT / "logs" / "_smoke_flaky"


def launches(tag):
    path = STATE_DIR / "{0}.count".format(tag)
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        return 0


def main():
    # 隔离运行数据：本测试的链虽然必定失败（FlakyNever 永远失败），
    # 但仍不应向真实 logs 目录写入任何状态。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_retry_")
    try:
        return _run()
    finally:
        guard.restore()


def _run():
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    bus = EventBus()
    bus.start_server()
    settings = config.settings()
    settings.setdefault("pre_run", {})["enabled"] = False
    settings.setdefault("chain", {})["max_attempts_per_project"] = 3
    supervisor = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))
    supervisor.run_chain()

    deadline = time.time() + 300
    while time.time() < deadline:
        if supervisor.chain_status in ("success", "failed", "stopped") and not any(
            r.alive for r in supervisor.runs.values()
        ):
            break
        time.sleep(0.5)

    counts = {tag: launches(tag) for tag in ("FlakyOnce", "FlakyNever", "OkC", "HangOnce")}
    runs = supervisor.runs
    print("chain:", supervisor.chain_status)
    for tag in ("FlakyOnce", "FlakyNever", "OkC", "HangOnce"):
        run = runs.get(tag)
        print(
            "{0}: launches={1} status={2} attempt={3}".format(
                tag, counts[tag], run.status if run else "n/a", run.attempt if run else "-"
            )
        )

    once = runs.get("FlakyOnce")
    never = runs.get("FlakyNever")
    okc = runs.get("OkC")
    hang = runs.get("HangOnce")

    checks = [
        ("chain ends failed (FlakyNever exhausted the cap)", supervisor.chain_status == "failed"),
        ("FlakyOnce failed then succeeded on the retry round", counts["FlakyOnce"] == 2 and once and once.status == "success"),
        ("FlakyOnce retry was attempt 2", bool(once) and once.attempt == 2),
        ("cap honoured: FlakyNever launched exactly 3 times", counts["FlakyNever"] == 3),
        ("FlakyNever last run is attempt 3", bool(never) and never.attempt == 3),
        ("FlakyNever finished failed/hang", bool(never) and never.status in ("failed", "hang")),
        ("OkC succeeded first time and was not retried", counts["OkC"] == 1 and okc and okc.status == "success"),
        ("HangOnce was retried after the hang and succeeded", counts["HangOnce"] == 2 and hang and hang.status == "success"),
        (
            "retry round ran after the first round finished (OkC was ahead of FlakyOnce's retry)",
            bool(once and okc) and (once.started_at or 0) > (okc.finished_at or 0) - 0.5,
        ),
    ]
    ok = True
    for label, passed in checks:
        print("  [{0}] {1}".format("PASS" if passed else "FAIL", label))
        ok = ok and bool(passed)

    print("RETRY-ROUND", "PASS" if ok else "FAIL")
    supervisor.stop_all()
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
