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
    print("CLI-REMOTE", "PASS" if ok else "FAIL")
    supervisor.stop_all()
    bus.stop_server()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
