"""探针桩程序：故意在末尾抛出未处理异常，供 tests/smoke_bus.py 以子进程方式驱动。

直接单独运行会以退出码 1 结束并打印 traceback —— 这是预期行为（用于验证
monitor_kit 能捕获未处理异常），不是回归失败；回归入口是 smoke_bus。
"""

import logging
import sys
import traceback
from pathlib import Path

# afk_hook 位于 monitor_kit/，运行时由监管器通过 PYTHONPATH 注入；测试里显式补上。
_MONITOR_KIT = Path(__file__).resolve().parents[1] / "monitor_kit"
if str(_MONITOR_KIT) not in sys.path:
    sys.path.insert(0, str(_MONITOR_KIT))

import afk_hook


def main():
    afk_hook.install(project="SmokeTest")
    afk_hook.mark_stage("stage-1")
    print("smoke stdout line", flush=True)
    logging.getLogger("smoke").warning("smoke warning line")
    try:
        1 / 0
    except ZeroDivisionError:
        afk_hook.mark_error("handled division error", detail=traceback.format_exc()[-200:])
    afk_hook.mark_done("smoke done")
    raise RuntimeError("smoke unhandled exception")


if __name__ == "__main__":
    sys.exit(main())
