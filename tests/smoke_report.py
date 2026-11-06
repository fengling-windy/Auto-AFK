r"""报告模块对脏数据必须健壮。

历史缺陷（2026-11-06 发现）：`afk_core/report.py:duration_stats()` 直接
`float(i.get("runtime_sec") or 0)` 与 `entry.get(...)`，而 `logs/durations.jsonl`
是纯文本外部文件，磁盘写满、断电、多进程并发都可能留下半行。实测三种坏数据
把 `afk_core.cli stats` 与 GUI 统计页整块打不开：

  * `{"project": "A", "runtime_sec": "abc"}` →
    `ValueError: could not convert string to float: 'abc'`；
  * 条目是数组 `[1,2,3]` → `AttributeError: 'list' object has no attribute 'get'`；
  * 条目是字符串 `"hello"` → `AttributeError: 'str' object has no attribute 'get'`。

判据：
  1. 任何脏数据下 `duration_stats()` 都返回 dict，不抛异常；
  2. 能解析的条目照常统计（不能因为一行坏就整份放弃）；
  3. `runtime_sec` 缺失/非数字时按 0 计，字符串形式的数字要认；
  4. 非映射条目（数组/字符串/数字）直接跳过；
  5. `list_reports()` 的 snapshot.json 坏掉时照样列出目录；
  6. `find_recent()` 的 errors.jsonl 有坏行时不能漏掉后面的好记录。
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILS = []

GOOD = '{"ts": 1, "project": "A", "runtime_sec": 12.5, "status": "success"}\n'


def check(name, ok, detail=""):
    if ok:
        print("ok   {0}".format(name))
    else:
        print("FAIL {0}  <- {1}".format(name, detail))
        FAILS.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="afk_report_"))
    try:
        _isolate(tmp)
        _check_durations()
        _check_reports()
        _check_error_index()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print("FAILED {0}: {1}".format(len(FAILS), "; ".join(FAILS)))
        print("REPORT ROBUST FAIL")
        return 1
    print("REPORT ROBUST PASS")
    return 0


def _isolate(tmp):
    from afk_core import config

    config.LOGS_DIR = tmp / "logs"
    config.PROJECT_LOGS = config.LOGS_DIR / "projects"
    config.REPORTS_DIR = tmp / "reports"
    config.EVENTS_FILE = config.LOGS_DIR / "events.jsonl"
    config.STATE_FILE = config.LOGS_DIR / "state.json"
    config.DURATIONS_FILE = config.LOGS_DIR / "durations.jsonl"
    config.ERROR_INDEX = config.LOGS_DIR / "errors.jsonl"
    for d in (config.LOGS_DIR, config.PROJECT_LOGS, config.REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _stats(text):
    """写入 durations.jsonl 后取统计，异常转成字符串返回。"""
    from afk_core import config, report

    config.DURATIONS_FILE.write_text(text, encoding="utf-8")
    try:
        return report.duration_stats(), None
    except Exception as exc:
        return None, "{0}: {1}".format(type(exc).__name__, exc)


def _check_durations():
    from afk_core import config, report

    # 1. 正常数据基线
    stats, err = _stats(GOOD * 3)
    check("正常数据能统计", not err and stats["A"]["runs"] == 3, err or stats)
    check("正常数据平均时长算对", not err and stats["A"]["avg_sec"] == 12.5,
          err or (stats or {}).get("A"))

    # 2. 历史崩溃形态与其它畸形都不能抛
    cases = [
        ("runtime_sec 是 abc", '{"project": "A", "runtime_sec": "abc"}\n'),
        ("条目是数组", "[1, 2, 3]\n"),
        ("条目是字符串", '"hello"\n'),
        ("runtime_sec 是列表", '{"project": "A", "runtime_sec": [1]}\n'),
        ("project 是列表", '{"project": [1, 2], "runtime_sec": 1}\n'),
        ("整行是数字", "12345\n"),
        ("整行是 null", "null\n"),
        ("半截 JSON", '{"project": "A", "runtime_s\n'),
    ]
    for label, text in cases:
        stats, err = _stats(text)
        check("「{0}」不抛异常".format(label), err is None, err)
        check("「{0}」返回字典".format(label), isinstance(stats, dict), type(stats).__name__)

    # 3. 数值兜底语义
    stats, err = _stats('{"project": "A", "runtime_sec": "12.5"}\n')
    check("字符串形式的数字要认出来", not err and stats["A"]["avg_sec"] == 12.5,
          err or (stats or {}).get("A"))
    stats, err = _stats('{"project": "A", "runtime_sec": null}\n')
    check("runtime_sec 是 null 按 0 计", not err and stats["A"]["avg_sec"] == 0.0,
          err or (stats or {}).get("A"))
    stats, err = _stats('{"project": "A"}\n')
    check("runtime_sec 缺失按 0 计", not err and stats["A"]["avg_sec"] == 0.0,
          err or (stats or {}).get("A"))
    stats, err = _stats('{"project": "A", "runtime_sec": true}\n')
    check("runtime_sec 是布尔按 0 计（不当 1）", not err and stats["A"]["avg_sec"] == 0.0,
          err or (stats or {}).get("A"))

    # 4. 一行坏不能连累整份
    stats, err = _stats(GOOD + '{"project": "A", "runtime_sec": "abc"}\n' + GOOD)
    check("坏行不影响同一项目的好行", not err and stats["A"]["runs"] == 3,
          err or (stats or {}).get("A"))
    stats, err = _stats(GOOD + "[1,2]\n" + '{"project": "B", "runtime_sec": 5}\n')
    check("坏条目跳过、其他项目照常统计",
          not err and set(stats) == {"A", "B"} and stats["B"]["runs"] == 1,
          err or stats)

    # 5. limit 生效（只统计尾部 N 行）
    many = "".join('{"project": "A", "runtime_sec": %d}\n' % i for i in range(10))
    config.DURATIONS_FILE.write_text(many, encoding="utf-8")
    stats = report.duration_stats(limit=3)
    check("limit 只取尾部若干行", stats["A"]["runs"] == 3, stats.get("A"))

    # 6. durations.jsonl 不存在时返回空字典
    config.DURATIONS_FILE.unlink()
    check("durations.jsonl 不存在时返回空字典", report.duration_stats() == {}, "不是空字典")


def _check_reports():
    from afk_core import config, report

    good_dir = config.REPORTS_DIR / "20260103_000000_Good"
    good_dir.mkdir()
    (good_dir / "snapshot.json").write_text(
        json.dumps({"project": "Good", "reason": "boom"}), encoding="utf-8")
    bad_dir = config.REPORTS_DIR / "20260102_000000_Bad"
    bad_dir.mkdir()
    (bad_dir / "snapshot.json").write_text("{bad json", encoding="utf-8")
    list_dir = config.REPORTS_DIR / "20260101_000000_List"
    list_dir.mkdir()
    (list_dir / "snapshot.json").write_text('["a"]', encoding="utf-8")
    try:
        rows = report.list_reports()
        names = [r.get("name") for r in rows]
        check("坏 snapshot.json 也照样列出报告",
              set(names) == {"20260103_000000_Good", "20260102_000000_Bad",
                             "20260101_000000_List"}, names)
        good_row = [r for r in rows if r.get("name", "").endswith("_Good")]
        check("好 snapshot.json 的内容能读出来",
              bool(good_row) and good_row[0].get("project") == "Good", good_row)
    except Exception as exc:
        check("list_reports 对坏 snapshot 不抛异常", False,
              "{0}: {1}".format(type(exc).__name__, exc))


def _check_error_index():
    from afk_core import config, report

    now = time.time()
    config.ERROR_INDEX.write_text(
        "not json\n[1, 2]\n"
        + json.dumps({"signature": "sig-good", "ts": now, "dir": "D1"}) + "\n",
        encoding="utf-8",
    )
    try:
        found = report.find_recent("sig-good", hours=1)
        check("errors.jsonl 有坏行时仍能找到好记录", found == "D1", found)
    except Exception as exc:
        check("find_recent 对坏行不抛异常", False,
              "{0}: {1}".format(type(exc).__name__, exc))
    check("find_recent 找不到时返回 None", report.find_recent("nope", hours=1) is None,
          report.find_recent("nope", hours=1))

    old = time.time() - 48 * 3600
    config.ERROR_INDEX.write_text(
        json.dumps({"signature": "sig-old", "ts": old, "dir": "D2"}) + "\n",
        encoding="utf-8",
    )
    check("超期记录不算最近", report.find_recent("sig-old", hours=1) is None,
          report.find_recent("sig-old", hours=1))

    config.ERROR_INDEX.write_text(
        json.dumps({"signature": "sig-ts", "ts": "not-a-number", "dir": "D3"}) + "\n",
        encoding="utf-8",
    )
    check("ts 不是数字时不抛异常", report.find_recent("sig-ts", hours=1) is None,
          report.find_recent("sig-ts", hours=1))


if __name__ == "__main__":
    sys.exit(main())
