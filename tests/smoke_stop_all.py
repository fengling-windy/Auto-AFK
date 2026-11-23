"""`Supervisor.stop_all()` 的单元冒烟：空闲时不该把状态改成「已停止」。

历史缺陷：stop_all() 无条件 `chain_status = "stopped"` 并落盘。空闲时点
「全部停止」（或关窗口走 _quit）会把界面上正常的「空闲」改成一个从没发生过
的「已停止」，state.json 里也留下 stopped —— 下次开窗读回来还是「已停止」，
用户以为上次是异常中断的。整个过程也没有任何事件，点了看不出反应。

只构造 Supervisor、不启动任何真实进程：用对象替身伪装「在跑的项目」。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (("  -- " + str(detail)) if detail and not ok else ""))
    return ok


class _FakeProc:
    """够 ProjectRun.alive 与 kill_tree 用的最小进程替身。"""

    pid = 0

    def poll(self):
        return None

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def _drain(queue):
    events = []
    while True:
        try:
            events.append(queue.get_nowait())
        except Exception:
            return events


def _run():
    from afk_core.events import EventBus
    from afk_core.supervisor import ProjectRun, Supervisor

    ok = True
    bus = EventBus()
    bus.start_server()
    queue = bus.subscribe()
    try:
        sup = Supervisor(bus)
        # 造一个「在跑」的项目：process.poll() 返回 None 即 alive
        fake = ProjectRun("FakeProject", sup.registry.get("projects", {}).get("BAAH") or {})
        fake.status = "running"
        fake.process = _FakeProc()
        sup.runs["FakeProject"] = fake

        # -- 1. 空闲时点「全部停止」------------------------------------------
        sup.runs.clear()
        sup.chain_status = "idle"
        _drain(queue)
        stopped = sup.stop_all()
        ok &= check("空闲时 stop_all 不返回任何被停的项目", stopped == 0, stopped)
        ok &= check("空闲时 stop_all 不改链状态（仍是 idle）", sup.chain_status == "idle", sup.chain_status)
        idle_events = _drain(queue)
        ok &= check(
            "空闲时 stop_all 至少留一条「没什么可停」的日志",
            any("nothing running" in str(e.get("message") or "") for e in idle_events),
            [e.get("message") for e in idle_events],
        )
        persisted = config.load_yaml(config.STATE_FILE, {}) or {}
        ok &= check(
            "空闲时 stop_all 没把 stopped 写进 state.json（若此刻写的是 idle 则通过）",
            persisted.get("chain_status") in (None, "", "idle"),
            persisted.get("chain_status"),
        )

        # -- 2. 有项目在跑时点「全部停止」------------------------------------
        sup.runs["FakeProject"] = fake
        sup.chain_status = "running"
        _drain(queue)
        stopped = sup.stop_all()
        ok &= check("有项目在跑时 stop_all 报告停掉了 1 个", stopped == 1, stopped)
        ok &= check("有项目在跑时链状态改成 stopped", sup.chain_status == "stopped", sup.chain_status)
        ok &= check("被停的项目标了 stopped", fake.stopped is True, fake.stopped)
        busy_events = _drain(queue)
        ok &= check(
            "有项目在跑时写一条「停了几个」的 warn",
            any("stop all" in str(e.get("message") or "") and e.get("level") == "warn" for e in busy_events),
            [(e.get("level"), e.get("message")) for e in busy_events],
        )
        persisted = config.load_yaml(config.STATE_FILE, {}) or {}
        ok &= check("有项目在跑时把 stopped 落盘", persisted.get("chain_status") == "stopped", persisted.get("chain_status"))

        # -- 3. 只有挂起的关机计划时，取消它但不改链状态 ----------------------
        sup.runs.clear()
        sup.chain_status = "success"
        sup._post_action_timer = type(
            "T", (), {"cancel": lambda self: setattr(self, "cancelled", True), "cancelled": False}
        )()
        _drain(queue)
        stop_hit = sup.stop_all()
        ok &= check("只有挂起的关机计划时 stop_all 不报告停项目", stop_hit == 0, stop_hit)
        ok &= check("挂起的关机计划被取消", sup._post_action_timer is None or getattr(sup._post_action_timer, "cancelled", False))
        ok &= check("取消关机计划不改动已成功的链状态", sup.chain_status == "success", sup.chain_status)
    finally:
        bus.unsubscribe(queue) if hasattr(bus, "unsubscribe") else None
        try:
            bus.stop_server()
        except Exception:
            pass
    return ok


def main():
    # 隔离运行数据：state.json / events.jsonl 不能写进用户真实 logs
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_stopall_")
    try:
        ok = _run()
    finally:
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()
    print("STOP-ALL", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
