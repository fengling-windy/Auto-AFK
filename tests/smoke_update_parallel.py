"""并行更新测试：验证 update_all_projects 真的同时更新，而不是依次等待。

用「被更新的项目彼此等待」来证明并行：如果串行执行，总耗时会累加；
并行执行时总耗时约等于单个项目的耗时（关键证据用实测时间断言）。

另外覆盖：结果顺序、并发度限制、失败隔离、停止响应。
"""

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_update_parallel_registry.yaml"

from afk_core.events import EventBus
from afk_core.updater import Updater

FAILURES = []


def check(name, ok):
    print("{0} {1}".format("PASS" if ok else "FAIL", name))
    if not ok:
        FAILURES.append(name)


class FakeUpdater(Updater):
    """把真实的 git/构建全部替换成受控的 sleep，只测量调度行为。"""

    def __init__(self, bus, settings=None, hold=1.0, fail_on=()):
        super().__init__(bus, settings)
        self.hold = hold
        self.fail_on = set(fail_on)
        self.active = 0
        self.peak = 0
        self.started = []
        self.lock = threading.Lock()
        self.threads = set()

    def update_project(self, key, spec=None):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.started.append(key)
            self.threads.add(threading.current_thread().name)
        try:
            time.sleep(self.hold)
            if key in self.fail_on:
                raise RuntimeError("simulated failure for {0}".format(key))
            return {"project": key, "updated": True, "steps": []}
        finally:
            with self.lock:
                self.active -= 1


def make_bus():
    bus = EventBus()
    bus.start_server()
    return bus


def test_parallel_speedup():
    """4 个项目各 sleep 1s：串行需 ~4s，并行应 ~1s。"""
    bus = make_bus()
    updater = FakeUpdater(bus, settings={"pre_run": {"update_concurrency": 0}}, hold=1.0)
    start = time.time()
    results = updater.update_all_projects()
    elapsed = time.time() - start
    bus.stop_server()

    keys = [r["project"] for r in results]
    print("   elapsed = {0:.2f}s, peak concurrency = {1}, order = {2}".format(elapsed, updater.peak, keys))
    check("全部 4 个项目都更新了", len(results) == 4 and all(r["updated"] for r in results))
    check("并发执行（峰值并发 = 4）", updater.peak == 4)
    check("确实快于串行（< 2.5s，串行需 4s）", elapsed < 2.5)
    check("使用了不同线程", len(updater.threads) >= 2)
    check("结果顺序与注册表一致", keys == ["PjA", "PjB", "PjC", "PjD"])


def test_concurrency_limit():
    """并发度=2 时最多同时 2 个，总耗时约 2 轮。"""
    bus = make_bus()
    updater = FakeUpdater(bus, settings={"pre_run": {"update_concurrency": 2}}, hold=1.0)
    start = time.time()
    results = updater.update_all_projects()
    elapsed = time.time() - start
    bus.stop_server()

    print("   elapsed = {0:.2f}s, peak concurrency = {1}".format(elapsed, updater.peak))
    check("并发度限制生效（峰值 = 2）", updater.peak == 2)
    check("仍快于串行（< 3.5s）", elapsed < 3.5)
    check("结果仍是 4 个且顺序不变", [r["project"] for r in results] == ["PjA", "PjB", "PjC", "PjD"])


def test_failure_isolation():
    """一个项目抛异常不影响其它项目继续完成。"""
    bus = make_bus()
    updater = FakeUpdater(bus, settings={"pre_run": {"update_concurrency": 0}}, hold=0.4, fail_on=("PjB",))
    results = updater.update_all_projects()
    bus.stop_server()

    by_key = {r["project"]: r for r in results}
    print("   results =", {k: (v["updated"], v["steps"][:1]) for k, v in by_key.items()})
    check("4 个项目都返回了结果", len(results) == 4)
    check("失败项目被记录（updated=False）", by_key["PjB"]["updated"] is False)
    check("同批次其它项目正常完成", all(by_key[k]["updated"] for k in ("PjA", "PjC", "PjD")))


def test_stop_respected():
    """should_stop 返回 True 后不再启动新项目。"""
    bus = make_bus()
    updater = FakeUpdater(bus, settings={"pre_run": {"update_concurrency": 1}}, hold=0.2)
    flag = {"stop": False}

    def should_stop():
        # 第一个项目开始后立刻请求停止
        return len(updater.started) >= 1 or flag["stop"]

    results = updater.update_all_projects(should_stop=should_stop)
    bus.stop_server()
    started_unique = sorted(set(updater.started))
    print("   started =", started_unique, "results =", len(results))
    check("停止后未启动全部项目", len(started_unique) < 4)
    check("结果仍包含已启动的项目", len(results) == len(started_unique))


def test_disabled_projects_skipped():
    registry = config.registry()
    disabled = [k for k, s in registry.get("projects", {}).items() if not s.get("enabled", True)]
    bus = make_bus()
    updater = FakeUpdater(bus, settings={"pre_run": {"update_concurrency": 0}}, hold=0.3)
    results = updater.update_all_projects()
    bus.stop_server()
    started = [r["project"] for r in results]
    print("   disabled projects =", disabled, "started =", started)
    check("enabled=false 的项目没有更新", all(k not in started for k in disabled))


def main():
    # 隔离运行数据（项目并行更新）：EventBus 默认用 config.LOGS_DIR /
    # config.EVENTS_FILE；不隔离的话测试夹具事件会写进用户真实
    # logs/events.jsonl（实测累计 3000+ 条）。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_updpar_")
    try:
        return _run()
    finally:
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
    test_parallel_speedup()
    test_concurrency_limit()
    test_failure_isolation()
    test_stop_respected()
    test_disabled_projects_skipped()
    print()
    if FAILURES:
        print("UPDATE-PARALLEL {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("UPDATE-PARALLEL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
