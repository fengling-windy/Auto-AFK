r"""日志维护：截断必须能在「文件还被打开着」的时候真正生效。

历史缺陷（2026-11-06 发现）：`afk_core/housekeeping.py` 的 events.jsonl 截断
原来是「写临时文件 + `os.replace` 替换」。Windows 上 `os.replace` 需要对目标
文件的 DELETE 权限，而 EventBus 为 events.jsonl 与每个项目的滚动日志都长期
开着追加句柄（`_jsonl_fh` / `_project_fhs`，GUI 进程整个生命周期不关），于是：

  * 一次都截断不成功 —— 实测 42 MB 的 events.jsonl 稳稳超过 50 MB 阈值前
    就已经卡住，`events_max_mb` 这个配置项等于不存在；
  * 每次尝试还会在 logs/ 留下一个 `events.jsonl.tmp` 垃圾；
  * 失败只写一条 warning 进 stderr，界面上完全看不出来。

判据：
  1. 文件被追加句柄打开时，清理也要真的把 events.jsonl 缩短；
  2. 截断后保留的内容仍然是完整的 JSONL（每一行都能 json.loads）；
  3. 截断后同一个句柄继续追加的新事件仍能落盘（不能把文件写坏）；
  4. 可以反复截断（不是一次性的）；
  5. 不残留 .tmp 临时文件，也不留旧的 .tmp 垃圾；
  6. 项目滚动日志（<project>.log）同样在开着句柄时能截断；
  7. 没超过阈值时一个字节都不动。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, detail=""):
    if ok:
        print("ok   {0}".format(name))
    else:
        print("FAIL {0}  <- {1}".format(name, detail))
        FAILS.append(name)


def _isolate(tmp):
    from afk_core import config

    config.LOGS_DIR = tmp / "logs"
    config.PROJECT_LOGS = config.LOGS_DIR / "projects"
    config.REPORTS_DIR = tmp / "reports"
    config.EVENTS_FILE = config.LOGS_DIR / "events.jsonl"
    config.STATE_FILE = config.LOGS_DIR / "state.json"
    config.DURATIONS_FILE = config.LOGS_DIR / "durations.jsonl"
    config.ERROR_INDEX = config.LOGS_DIR / "errors.jsonl"
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    config.PROJECT_LOGS.mkdir(parents=True, exist_ok=True)
    return config


def _settings(events_max_mb=1):
    return {
        "housekeeping": {
            "enabled": True,
            "events_max_mb": events_max_mb,
            "durations_max_mb": 1,
            "log_days": 14,
            "report_days": 30,
        }
    }


def _valid_jsonl(raw):
    bad = 0
    total = 0
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        total += 1
        try:
            json.loads(line)
        except Exception:
            bad += 1
    return bad, total


def main():
    tmp = Path(tempfile.mkdtemp(prefix="afk_housekeeping_"))
    try:
        return _run(tmp)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp):
    config = _isolate(tmp)
    from afk_core import housekeeping
    from afk_core.events import EventBus

    bus = EventBus()
    # 让 events.jsonl 超过 1 MB 阈值（每条约 250 字节）
    for _ in range(6000):
        bus.emit({"project": "core", "kind": "log", "level": "info", "message": "filler " * 30})

    events = config.EVENTS_FILE
    before = events.stat().st_size
    check("准备：events.jsonl 已超过阈值", before > 1 * 1024 * 1024, "{0} bytes".format(before))
    check("准备：追加句柄确实是开着的（复现前提）", bus._jsonl_fh is not None, "handle is None")

    housekeeping.cleanup_logs(bus, _settings(events_max_mb=1))
    after = events.stat().st_size
    check("文件被打开着时，截断也要真的生效", after < before, "{0} -> {1}".format(before, after))
    check("截断后不超过一半阈值", after <= 1 * 1024 * 1024 // 2 + 4096, "{0} bytes".format(after))

    raw = events.read_bytes()
    bad, total = _valid_jsonl(raw)
    check("截断后每一行仍是完整 JSON", bad == 0 and total > 0, "bad={0} total={1}".format(bad, total))
    check("截断不会从半行开始（首行就是完整记录）", raw.startswith(b"{"), repr(raw[:40]))
    check("截断不残留 .tmp", not (events.parent / (events.name + ".tmp")).exists())

    marker = "SMOKE-AFTER-TRUNC-{0}".format(round(time.time(), 3))
    bus.emit({"project": "core", "kind": "log", "level": "info", "message": marker})
    time.sleep(0.2)
    raw = events.read_bytes()
    check("截断后同一个句柄仍能继续追加（新事件落盘）", marker.encode("utf-8") in raw, "marker missing")
    bad, _ = _valid_jsonl(raw)
    check("追加之后文件仍然是合法 JSONL", bad == 0, "bad={0}".format(bad))

    for _ in range(6000):
        bus.emit({"project": "core", "kind": "log", "level": "info", "message": "filler2 " * 30})
    second_before = events.stat().st_size
    housekeeping.cleanup_logs(bus, _settings(events_max_mb=1))
    second_after = events.stat().st_size
    check(
        "可以反复截断（不是一次性）",
        second_before > 1 * 1024 * 1024 and second_after < second_before,
        "{0} -> {1}".format(second_before, second_after),
    )

    # 项目滚动日志：<project>.log 由 EventBus 为每个项目开着句柄
    plog = config.PROJECT_LOGS / "SmokeRoll.log"
    for _ in range(30000):
        bus.emit({"project": "SmokeRoll", "kind": "log", "level": "info", "message": "y" * 200})
    pbefore = plog.stat().st_size
    housekeeping.cleanup_logs(bus, _settings(events_max_mb=1000))
    pafter = plog.stat().st_size
    check("项目滚动日志超过 5MB 时会被截断", pbefore > housekeeping.PROJECT_LOG_MAX_BYTES, "{0} bytes".format(pbefore))
    check(
        "项目滚动日志在开着句柄时也能截断",
        pafter < pbefore and pafter <= housekeeping.PROJECT_LOG_KEEP_BYTES + 4096,
        "{0} -> {1}".format(pbefore, pafter),
    )
    praw = plog.read_bytes()
    check("项目日志截断后不含 NUL（文件没被写坏）", praw.count(b"\x00") == 0, "nul={0}".format(praw.count(b"\x00")))
    pm = "SMOKE-ROLL-MARKER"
    bus.emit({"project": "SmokeRoll", "kind": "log", "level": "info", "message": pm})
    time.sleep(0.2)
    check("项目日志截断后仍能继续追加", pm.encode("utf-8") in plog.read_bytes(), "marker missing")

    # 没超过阈值时不该动文件
    small = config.LOGS_DIR / "small.jsonl"
    small.write_bytes(b'{"a":1}\n' * 10)
    payload = small.read_bytes()
    housekeeping._keep_tail(small, 10 * 1024 * 1024)
    check("没超过阈值时一个字节都不动", small.read_bytes() == payload, "file changed")

    # 旧版本 os.replace 失败留下的 .tmp 会被顺手清掉
    stale = config.LOGS_DIR / "events.jsonl.tmp"
    stale.write_bytes(b"stale")
    housekeeping.cleanup_logs(bus, _settings(events_max_mb=1000))
    check("旧版本残留的 events.jsonl.tmp 会被清理", not stale.exists(), "still there")

    bus.close_handles()
    print()
    if FAILS:
        print("FAILED {0}: {1}".format(len(FAILS), "; ".join(FAILS)))
        print("HOUSEKEEPING FAIL")
        return 1
    print("HOUSEKEEPING PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
