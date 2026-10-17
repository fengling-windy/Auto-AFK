import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_guiopen_registry.yaml"

from afk_core.events import EventBus
from afk_core.supervisor import Supervisor


def main():
    # 隔离运行数据（打开项目 GUI（模拟入口））：EventBus 默认用 config.LOGS_DIR /
    # config.EVENTS_FILE；不隔离的话测试夹具事件会写进用户真实
    # logs/events.jsonl（实测累计 3000+ 条）。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_guiopen_")
    try:
        return _run()
    finally:
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
    # marker 必须用「仓库根」而不是隔离目录：子进程由 Supervisor.spawn_project_gui
    # 派生，而 _build_gui_env 会**有意剥离所有 AFK_ 环境变量**（避免向项目 GUI
    # 泄漏监控配置），所以子进程读不到 AFK_ROOT、只能按仓库根写 marker。
    # 隔离只用于事件流/状态（那部分由父进程写入，隔离有效）。
    marker = ROOT / "logs" / "gui_open_marker.txt"
    if marker.exists():
        marker.unlink()
    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings())

    # no task should be started by opening the GUI
    ok = supervisor.open_project_gui("GuiProject")
    print("open_project_gui:", ok)
    deadline = time.time() + 30
    while time.time() < deadline and not marker.exists():
        time.sleep(0.3)

    gui_events = [e for e in bus.history() if e.get("kind") == "gui" and e.get("project") == "GuiProject"]
    run_alive = "GuiProject" in supervisor.runs and supervisor.runs["GuiProject"].alive
    marker_ok = marker.exists()
    content = marker.read_text(encoding="utf-8") if marker_ok else ""
    print("marker:", marker_ok, content.strip())
    print("gui event:", len(gui_events) == 1)
    print("task NOT started:", not run_alive and "GuiProject" not in supervisor.runs)

    # unknown project -> False
    ok2 = supervisor.open_project_gui("NoSuchProject")
    print("unknown project rejected:", ok2 is False)

    ok_all = ok and marker_ok and len(gui_events) == 1 and "GuiProject" not in supervisor.runs and ok2 is False
    print("GUI-OPEN", "PASS" if ok_all else "FAIL")
    # 清理自己产生的 marker（子进程按设计写在仓库根，不清理会留在用户 logs 里）
    try:
        marker.unlink()
    except OSError:
        pass
    bus.stop_server()
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
