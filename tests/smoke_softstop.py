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
    # 隔离运行数据：避免把「今日已完成」标记写进真实 logs 目录
    # （会使用户当天的空闲自动挂机被误跳过）。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_softstop_")
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
    # （会把「软停」测试卡在 pre-run 阶段，使 SmokeC 判定逻辑无从验证）
    settings.setdefault("pre_run", {})["enabled"] = False
    supervisor = Supervisor(bus, settings=settings, repair=RepairEngine(bus, settings))
    supervisor.run_chain()
    # 软停语义是「跑完当前组即停」，所以必须在组 A 还在跑的时候就置标志。
    # 之前等 A、B 都 terminal 再软停，链线程可能已经越过 _chain_soft 检查启动
    # 组 B 的 SmokeC（实测偶发 started=True 失败）——这是测试自身的竞态，
    # 不是软停逻辑错误。
    deadline = time.time() + 120
    armed = False
    while time.time() < deadline:
        a = supervisor.runs.get("SmokeA")
        b = supervisor.runs.get("SmokeB")
        if a and b and (a.started_at is not None or b.started_at is not None):
            supervisor.stop_chain_soon()
            armed = True
            break
        time.sleep(0.05)
    # wait until the parallel first group (SmokeA + SmokeB) has finished
    group_done = False
    deadline = time.time() + 120
    while time.time() < deadline:
        a = supervisor.runs.get("SmokeA")
        b = supervisor.runs.get("SmokeB")
        if a and b and a.terminal and b.terminal:
            group_done = True
            break
        time.sleep(0.2)
    print("first group terminal:", group_done, "soft armed early:", armed)
    # chain must settle with SmokeC never started
    deadline = time.time() + 120
    while time.time() < deadline:
        if supervisor.chain_status in ("success", "failed", "stopped") and not any(
            r.alive for r in supervisor.runs.values()
        ):
            break
        time.sleep(0.3)
    # 等链线程真正退出后再让 guard.restore() 还原隔离目录（见 smoke_isolation）
    from tests import smoke_isolation

    smoke_isolation.wait_chain_idle(supervisor, timeout=60)
    c = supervisor.runs.get("SmokeC")
    c_started = c is not None and c.started_at is not None
    print("chain:", supervisor.chain_status, "SmokeC started:", c_started)
    ok = group_done and supervisor.chain_status == "stopped" and not c_started
    print("SOFT-STOP", "PASS" if ok else "FAIL")
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
