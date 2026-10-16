"""Fixture for smoke_retry_round.py: a project that fails (or hangs) N times.

The run counter is persisted in logs/_smoke_flaky/<tag>.count so it survives
across the separate processes the supervisor launches.

argv: <tag> <ok_after> [hang_first] [hang_seconds]
  tag          counter name
  ok_after     the run number that first succeeds (use a big number to always fail)
  hang_first   how many leading runs sleep instead of exiting (卡死)
  hang_seconds how long the hanging runs sleep (must exceed hang_timeout)
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "logs" / "_smoke_flaky"


def bump(tag):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "{0}.count".format(tag)
    current = 0
    if path.exists():
        try:
            current = int(path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            current = 0
    current += 1
    path.write_text(str(current), encoding="utf-8")
    return current


def mark_done(message):
    try:
        import afk_hook

        afk_hook.mark_done(message)
    except Exception:
        pass
    print(message, flush=True)


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "X"
    ok_after = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    hang_first = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    hang_seconds = float(sys.argv[4]) if len(sys.argv) > 4 else 3600.0
    try:
        import afk_hook

        afk_hook.install(project=tag)
    except Exception:
        pass
    run = bump(tag)
    print("flaky {0} run #{1}".format(tag, run), flush=True)
    if run <= hang_first:
        print("flaky {0} hanging on purpose".format(tag), flush=True)
        time.sleep(hang_seconds)
        return 0
    if run >= ok_after:
        mark_done("flaky {0} done".format(tag))
        return 0
    print("flaky {0} failing on purpose (run {1} < {2})".format(tag, run, ok_after), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
