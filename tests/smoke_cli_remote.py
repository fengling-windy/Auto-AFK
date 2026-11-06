import argparse
import io
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import cli, config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_cli_registry.yaml"

from afk_core.events import EventBus
from afk_core.repair import RepairEngine
from afk_core.supervisor import Supervisor


def main():
    # 隔离运行数据（CLI 远程 stop/status）：避免把 state.json / durations.jsonl /
    # errors.jsonl 写进用户真实 logs——用户可能正在挂机，那会污染其运行状态。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_cli_remote_")
    try:
        return _run()
    finally:
        # 先等监督线程（afk-supervise-*）收尾，否则其 _persist()
        # 会写进尚未隔离的真实 logs（见 smoke_isolation）
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
    import psutil

    bus = EventBus()
    bus.start_server()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    run = supervisor.start_project("RemoteStop")
    time.sleep(2)

    status_buf = io.StringIO()
    with redirect_stdout(status_buf):
        code = cli.cmd_status(argparse.Namespace(json=True))
    out = status_buf.getvalue()
    status_ok = code == 0 and '"running"' in out and "RemoteStop" in out
    print("remote status ok:", status_ok)

    stop_buf = io.StringIO()
    with redirect_stdout(stop_buf):
        code = cli.cmd_stop(argparse.Namespace(project="RemoteStop"))
    stop_out = stop_buf.getvalue().strip()
    time.sleep(1.5)
    pid = run.process.pid if run.process else None
    killed = not psutil.pid_exists(pid) if pid else False
    print("remote stop output:", stop_out, "| killed:", killed)
    ok = status_ok and code == 0 and "stopped" in stop_out and killed

    ok = _check_broken_server_info(supervisor) and ok

    print("CLI-REMOTE", "PASS" if ok else "FAIL")
    supervisor.stop_all()
    bus.stop_server()
    return 0 if ok else 1


def _check_broken_server_info(supervisor):
    """event_server.json 被写坏时 CLI 不能崩，只能当成「没找到实例」。

    历史缺陷（2026-11-06）：`cli._server_info()` 解析完直接 `info.get("pid")`。
    `event_server.json` 由 EventBus 启动时写入，磁盘写满、进程被强杀、手工编辑
    都可能留下非映射内容，实测 `[1,2]` / `"hello"` / `12345` / `null` 四种都会抛
    `AttributeError: 'list' object has no attribute 'get'`，把 `stop` / `status`
    这类本该只打印「没找到实例」的命令带崩。
    """
    info_path = config.LOGS_DIR / "event_server.json"
    original = info_path.read_text(encoding="utf-8") if info_path.exists() else None
    failures = []
    cases = [
        ("是数组", "[1, 2]"),
        ("是字符串", '"hello"'),
        ("是数字", "12345"),
        ("是 null", "null"),
        ("坏 json", "{bad"),
    ]
    try:
        for label, content in cases:
            info_path.write_text(content, encoding="utf-8")
            try:
                info = cli._server_info()
                if info is not None:
                    failures.append("{0}: 应该返回 None，实际 {1}".format(label, info))
            except Exception as exc:
                failures.append("{0}: {1}: {2}".format(label, type(exc).__name__, exc))
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    cli.cmd_status(argparse.Namespace(json=True))
            except Exception as exc:
                failures.append("{0}: cmd_status 抛 {1}: {2}".format(
                    label, type(exc).__name__, exc))
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    cli.cmd_stop(argparse.Namespace(project="RemoteStop"))
            except Exception as exc:
                failures.append("{0}: cmd_stop 抛 {1}: {2}".format(
                    label, type(exc).__name__, exc))
    finally:
        if original is not None:
            info_path.write_text(original, encoding="utf-8")
    ok = not failures
    print("broken event_server.json handled:", ok)
    for item in failures:
        print("  FAIL", item)
    return ok


if __name__ == "__main__":
    sys.exit(main())
