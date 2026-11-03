"""小工具：验证 MuMuController.update_check() 的 Qt GUI 崩溃护栏行为。

三种场景各跑一次，重点看 GUI 更新器进程有没有被拉起：

1. updater_mode=none                 -> 一次都不许启动 MuMuNxUpdater.exe
2. updater_mode=gui + 更新可用 + silent 崩溃 0xC0000409
                                     -> 不许启动 --manual-check（护栏生效）
3. updater_mode=gui + 更新可用 + silent 正常
                                     -> 仍然要启动 --manual-check（护栏不越权）

运行方式：`.venv/Scripts/python.exe tools/mumu_update_guard_check.py`
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

import afk_core.emulator as emulator  # noqa: E402
from afk_core.emulator import MuMuController  # noqa: E402

CRASH = 3221226505  # 0xC0000409 FAST_FAIL_FATAL_APP_EXIT


class FakeBus:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event, **_kwargs):
        self.events.append(event)
        print("    [%s] %s" % (event.get("level"), event.get("message")))


class FakeProc:
    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None


def load_settings():
    with open(ROOT / "configs" / "core" / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def run_case(title, mode, silent_code):
    print("\n=== %s ===" % title)
    settings = load_settings()
    cfg = settings["pre_run"]["emulator"]
    cfg["updater_mode"] = mode
    cfg["always_open"] = True
    cfg["close_main_after_update"] = False

    launched = []

    def fake_popen(args, **kwargs):
        launched.append(list(args))
        return FakeProc()

    original = emulator.proc_popen
    emulator.proc_popen = fake_popen
    try:
        controller = MuMuController(FakeBus(), settings)
        controller._run = lambda *a, **k: (silent_code, "")
        controller.version = lambda: "6.8.4.0"
        controller._main_app_running = lambda: False
        result = controller.update_check()
    finally:
        emulator.proc_popen = original

    manual = [c for c in launched if any("--manual-check" in str(x) for x in c)]
    print("    result:", json.dumps(result, ensure_ascii=False))
    print("    launched:", launched)
    print("    manual-check launched:", len(manual))
    return launched


def main() -> int:
    failures = []

    c1 = run_case("updater_mode=none (崩溃机器上的推荐配置)", "none", CRASH)
    if c1:
        failures.append("case1: updater_mode=none 仍然启动了外部进程 %r" % (c1,))

    c2 = run_case("updater_mode=gui + silent 崩溃 0xC0000409", "gui", CRASH)
    if any("--manual-check" in str(x) for c in c2 for x in c):
        failures.append("case2: 护栏失效，崩溃机上仍拉起 --manual-check")

    c3 = run_case("updater_mode=gui + silent 正常（护栏不应越权）", "gui", 0)
    if not any("--manual-check" in str(x) for c in c3 for x in c):
        failures.append("case3: 正常机器上反而不再弹更新窗口，护栏过度")

    print()
    if failures:
        for item in failures:
            print("FAIL:", item)
        return 1
    print("ALL GUARD CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
