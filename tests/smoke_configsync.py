"""配置同步失败必须变成一次「失败的运行」，而不是把启动线程打死。

真实缺陷：`Supervisor.start_project` 里 `configsync.ensure_links()` /
`sync_in()` 是裸调用。项目目录不存在、盘没挂、路径写错时 `mkdir` 抛
`FileNotFoundError`，异常沿着 `afk-start-<key>` 线程冒到 stderr 就结束了：
没有事件、没有日志、`runs[key]` 里的 run 永远停在 pending。界面上表现是
用户点「启动」后毫无反应——卡片一直「等待 / 等待启动」，日志区一个字都没有，
只能在控制台里看到一段 traceback。

判据：
1. `run.status == "failed"`、`exit_code == -4`；
2. `run.last_message` 里带「配置同步失败」和真实原因；
3. 事件流里有一条该项目的 error 级事件（GUI 日志区靠它显示）；
4. 界面上那张卡片「不是」等待态（防止回归成静默不动）。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_configsync_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  <- " + str(detail)))
    if not ok:
        FAILED.append(name)


def main():
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_cfg_sync_")
    try:
        return _run()
    finally:
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
    bus = EventBus()
    bus.start_server()
    queue = bus.subscribe()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    try:
        run = supervisor.start_project("BrokenPathProject")
        check("配置同步失败时返回了一个 run", run is not None)
        if run is None:
            return 1
        run.finished_event.wait(timeout=30)
        check("配置同步失败的项目不会一直挂着", run.finished_event.is_set())
        check("状态是 failed", run.status == "failed", run.status)
        check("退出码是 -4（配置同步阶段失败）", run.exit_code == -4, run.exit_code)
        message = run.last_message or ""
        check("失败原因说明是配置同步失败", "配置同步失败" in message, message)
        check("失败原因带上了底层错误", "WinError" in message or "No such" in message or "不存在" in message, message)
        check("进程没有被拉起来", run.process is None)

        events = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                events.append(queue.get_nowait())
            except Exception:
                time.sleep(0.02)
        errors = [e for e in events if e.get("project") == "BrokenPathProject" and e.get("level") == "error"]
        check("事件流里有 error 级事件（界面日志才不会空白）", bool(errors), [e.get("message") for e in events])
        bus.unsubscribe(queue)
    finally:
        supervisor.stop_all()
        bus.stop_server()

    print("CONFIGSYNC", "PASS" if not FAILED else "FAIL")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
