r"""state.json 被写坏后必须还能启动：恢复时不能假设结构正确。

历史缺陷（2026-11-06 发现）：`Supervisor._restore_state()` 只判断「json.loads
成功没抛异常」，然后直接 `data.get(...)`。于是下面这些**结构上合法、语义上
错误**的 state.json 能把它喂成畸形内部状态：

  * `{"runs": [1,2,3]}` → `self._orphan_runs` 变成 list；随后
    `cleanup_orphans()` 里的 `self._orphan_runs.keys()` 抛
    `AttributeError: 'list' object has no attribute 'keys'`。而
    `cleanup_orphans()` 是在 `start_project()` 开头调用的 —— 用户点「启动」
    直接炸在界面线程之外，日志里只有一条线程 traceback。
  * `{"runs": {"BAAH": "x"}}` → 遍历时 `run.get("pid")` 抛
    `AttributeError: 'str' object has no attribute 'get'`。
  * `{"chain_status": 123}` → 界面上链状态变成数字，`cli.py:84` 的
    `chain_status in ("success", ...)` 永远为假，`--wait` 变成死等。

判据：
  1. 任何损坏形态下 Supervisor 都能构造成功（不能让程序起不来）；
  2. chain_status 只会是五种合法值之一（坏值退回 idle）；
  3. 残留进程记录必须是「项目名 -> 映射」，坏条目被丢掉、不影响其它条目；
  4. 坏条目不会让 cleanup_orphans() 抛异常（它跑在 start_project 里）；
  5. 恢复过程不修改 state.json（读坏了不写，避免把用户数据越改越糟）。
"""

import shutil
import sys
import tempfile
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


def main():
    tmp = Path(tempfile.mkdtemp(prefix="afk_state_"))
    try:
        return _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp):
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

    from afk_core.events import EventBus
    from afk_core.supervisor import Supervisor

    def build(content, label):
        """把 state.json 写成 content，构造 Supervisor，返回 (sup, bus, state_bytes_before)。"""
        path = config.STATE_FILE
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(content, encoding="utf-8")
        before = path.read_bytes() if path.exists() else None
        bus = EventBus()
        sup = Supervisor(bus)
        return sup, bus, before

    # 1. 彻底坏掉的数据都要能启动
    cases = [
        ("半截 JSON", '{"chain_status": "running", "runs": {'),
        ("顶层是 list", '["a", "b"]'),
        ("顶层是字符串", '"hello"'),
        ("空文件", ""),
        ("runs 是 list", '{"chain_status": "idle", "runs": [1,2,3]}'),
        ("runs 里值是 None", '{"chain_status": "running", "runs": {"BAAH": null}}'),
        ("runs 里值是字符串", '{"chain_status": "running", "runs": {"BAAH": "x"}}'),
        ("chain_status 是数字", '{"chain_status": 123, "runs": {}}'),
        ("chain_status 是未知字符串", '{"chain_status": "weird", "runs": {}}'),
        ("正常内容", '{"chain_status": "success", "runs": {}}'),
    ]
    allowed = set(Supervisor.CHAIN_STATUSES)
    for label, content in cases:
        sup, bus, before = build(content, label)
        try:
            ok = getattr(sup, "chain_status", None) in allowed
            check("state.json「{0}」时能构造出 Supervisor".format(label), True)
            check("state.json「{0}」时 chain_status 合法".format(label), ok,
                  "chain_status={0!r}".format(getattr(sup, "chain_status", None)))
            check("state.json「{0}」时残留记录是映射".format(label),
                  isinstance(sup._orphan_runs, dict),
                  type(sup._orphan_runs).__name__)
            after = config.STATE_FILE.read_bytes() if config.STATE_FILE.exists() else None
            check("state.json「{0}」时恢复过程不改文件".format(label), after == before,
                  "state.json was rewritten")
        finally:
            bus.close_handles()

    # 2. 坏条目被丢掉，好条目保留
    sup, bus, _ = build(
        '{"chain_status": "running", "runs": {"Good": {"pid": null, "status": "done"}, "Bad": "x"}}',
        "混合",
    )
    try:
        check("混合形态：好条目保留", "Good" in sup._orphan_runs, repr(sup._orphan_runs))
        check("混合形态：坏条目被丢掉", "Bad" not in sup._orphan_runs, repr(sup._orphan_runs))
        check("混合形态：chain_status 仍是 running", sup.chain_status == "running", sup.chain_status)
    finally:
        bus.close_handles()

    # 3. cleanup_orphans 在畸形数据下不抛异常（它跑在 start_project 里）
    for label, content in (
        ("runs 是 list", '{"chain_status": "idle", "runs": [1,2,3]}'),
        ("runs 里值是字符串", '{"chain_status": "running", "runs": {"BAAH": "x"}}'),
        ("runs 里值是 list", '{"chain_status": "running", "runs": {"BAAH": [1]}}'),
    ):
        sup, bus, _ = build(content, label)
        try:
            try:
                sup.cleanup_orphans()
                check("cleanup_orphans 在「{0}」下不抛异常".format(label), True)
            except Exception as exc:
                check("cleanup_orphans 在「{0}」下不抛异常".format(label), False,
                      "{0}: {1}".format(type(exc).__name__, exc))
        finally:
            bus.close_handles()

    # 4. 即使运行时把 _orphan_runs 换成非映射（模拟第三方代码/历史遗留），也不炸
    sup, bus, _ = build('{"chain_status": "idle", "runs": {}}', "运行时篡改")
    try:
        sup._orphan_runs = ["not", "a", "dict"]
        try:
            sup.cleanup_orphans()
            check("cleanup_orphans 对运行时被改成 list 的记录也能兜住", True)
        except Exception as exc:
            check("cleanup_orphans 对运行时被改成 list 的记录也能兜住", False,
                  "{0}: {1}".format(type(exc).__name__, exc))
    finally:
        bus.close_handles()

    # 5. 真实运行：坏 state.json 下 status_snapshot 仍可用（GUI/CLI 都靠它刷新）
    sup, bus, _ = build('{"chain_status": "running", "runs": [1,2,3]}', "快照可用")
    try:
        snap = sup.status_snapshot()
        check("坏 state.json 下 status_snapshot 可用",
              isinstance(snap, dict) and snap.get("chain_status") in allowed,
              repr(snap)[:80])
    except Exception as exc:
        check("坏 state.json 下 status_snapshot 可用", False,
              "{0}: {1}".format(type(exc).__name__, exc))
    finally:
        bus.close_handles()

    # 4. 并发读：state.json 一直被原子替换时，读取方不能读到空文件/半截 JSON
    #    （历史缺陷：_persist 用 write_text 先截断再写，CLI status / GUI 面板能读到
    #     空文件 → JSONDecodeError: Expecting value: line 1 column 1，然后被
    #     _restore_state 当成「状态文件损坏」按全新状态处理，用户看到链状态丢失）
    _check_concurrent_read(config)

    print()
    if FAILS:
        print("FAILED {0}: {1}".format(len(FAILS), "; ".join(FAILS)))
        print("STATE RECOVERY FAIL")
        return 1
    print("STATE RECOVERY PASS")
    return 0


def _check_concurrent_read(config):
    import json
    import threading

    path = config.STATE_FILE
    path.write_text(json.dumps({"chain_status": "idle", "runs": {}}), encoding="utf-8")
    failures = []
    stop = threading.Event()

    def writer(n):
        for i in range(120):
            payload = json.dumps(
                {"chain_status": "running",
                 "runs": {"P{0}".format(n): {"status": "running", "attempt": i}},
                 "pad": "x" * 3000},
                indent=2,
            )
            try:
                config.write_text_atomic(path, payload)
            except Exception as exc:
                failures.append("写 {0}: {1}".format(type(exc).__name__, exc))
                return

    def reader():
        while not stop.is_set():
            try:
                data = json.loads(config.read_text(path))
            except Exception as exc:
                failures.append("读 {0}: {1}".format(type(exc).__name__, exc))
                return
            if not isinstance(data, dict):
                failures.append("读到非映射内容：{0!r}".format(data))
                return

    watchers = [threading.Thread(target=reader) for _ in range(2)]
    writers = [threading.Thread(target=writer, args=(n,)) for n in range(3)]
    for t in watchers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in watchers:
        t.join()
    check("并发替换 state.json 时读取方不会读到空/半截文件", not failures, failures[:3])


if __name__ == "__main__":
    sys.exit(main())
