"""探针桩程序：故意抛出未处理异常，供监管器的异常捕获链路以子进程方式驱动。

直接单独运行会以退出码 1 结束，这是预期行为，不是回归失败。
"""

import logging
import sys
from pathlib import Path

# afk_hook 位于 monitor_kit/，运行时由监管器通过 PYTHONPATH 注入；测试里显式补上。
_MONITOR_KIT = Path(__file__).resolve().parents[1] / "monitor_kit"
if str(_MONITOR_KIT) not in sys.path:
    sys.path.insert(0, str(_MONITOR_KIT))

import afk_hook


def main():
    afk_hook.install(project="SmokeProject")
    logging.getLogger("smoke").info("starting smoke project")
    afk_hook.mark_stage("running")
    print("smoke project output", flush=True)
    raise ModuleNotFoundError("No module named 'afk_nonexistent_package'")


if __name__ == "__main__":
    sys.exit(main())
