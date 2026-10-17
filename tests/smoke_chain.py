import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_chain_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    # 隔离运行数据：链成功会写 logs/last_chain_success.json（今日已完成），
    # 写进真实目录会导致用户当天空闲自动挂机被误跳过。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_chain_")
    try:
        return _run()
    finally:
        # 先等监督线程（afk-supervise-*）收尾，否则其 _persist()
        # 会写进尚未隔离的真实 logs（见 smoke_isolation）
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
    bus = EventBus()
    bus.start_server()
    settings = config.settings()
    # 关闭 pre_run：避免真的去联网更新项目、拉起重型 MuMu 更新器并阻塞等待
    # （MuMu 一旦判定有新版本会打开更新器界面，update_check 会 wait 最长 600s）
    settings.setdefault("pre_run", {})["enabled"] = False
    supervisor = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))
    supervisor.run_chain()
    deadline = time.time() + 300
    while time.time() < deadline:
        if supervisor.chain_status in ("success", "failed", "stopped") and not any(r.alive for r in supervisor.runs.values()):
            break
        time.sleep(0.5)
    # 等链线程真正退出，避免 guard.restore() 抢在写完成标记之前（见 smoke_isolation）
    from tests import smoke_isolation

    smoke_isolation.wait_chain_idle(supervisor, timeout=60)
    runs = supervisor.runs
    a = runs.get("SmokeA")
    b = runs.get("SmokeB")
    c = runs.get("SmokeC")
    print("chain:", supervisor.chain_status)
    for key in ("SmokeA", "SmokeB", "SmokeC"):
        run = runs.get(key)
        if run:
            print("{0}: status={1} started={2:.2f} finished={3:.2f}".format(key, run.status, run.started_at or 0, run.finished_at or 0))
    ok = supervisor.chain_status == "success" and all(r and r.status == "success" for r in (a, b, c))
    if not ok and supervisor.chain_status == "running":
        print("HINT: 链仍处于 running —— pre-run 阶段可能被外部调用阻塞（如 MuMu 更新器）")
    if ok and a and b and c:
        parallel_overlap = abs((a.started_at or 0) - (b.started_at or 0)) < 2.0
        c_after = (c.started_at or 0) >= max(a.finished_at or 0, b.finished_at or 0) - 1.0
        print("parallel A/B:", parallel_overlap, "C after A+B:", c_after)
        ok = parallel_overlap and c_after
    print("CHAIN", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
