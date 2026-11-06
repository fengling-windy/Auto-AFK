"""模拟器 adb 连接判定与「启动报错」回归。

真实事故（2026-11 多次出现在 logs/projects/core.log）：自动挂机的重试轮里
反复打出

    [WARN] [emulator] adb connect 127.0.0.1:16384: cannot connect to
    127.0.0.1:16384: 由于目标计算机积极拒绝，无法连接。 (10061)

随后链被判失败、模拟器被关掉，用户看到的就是「mumu 模拟器启动一直报错」。
根因有两处，都在 afk_core/emulator.py：

1. `adb_connect()` 的判定是「有输出就算成功」——
   `code == 0 and "cannot" not in out and "failed" not in out`。
   adb 的 `error: device offline`、纯中文报错都能通过；而且 `adb connect`
   在端口没开时**退出码依然是 0**（实测 127.0.0.1:59999 → code 0），所以
   退出码根本不能用来判断。它还只试一次：MuMu 刚开机时 adb 端口要几秒才
   监听，单次必然 10061。
2. `ensure_running()` 里「模拟器已经在跑」这条分支把 `adb_connect()` 的
   返回值丢掉，无条件返回 `ready: True`，于是 supervisor 的
   `if not result.get("ready")` 永不成立，子项目照常启动、进去连不上设备，
   用户只看到 BAAH 自己报错，根因被藏住。

判据（全部打桩，不需要真机）：
1. `adb devices` 里状态不是 `device`（offline / 为空）时不能算连上；
2. 纯中文失败输出不能算连上；
3. 端口晚几秒才监听时能重试到成功（而不是第一次就放弃）；
4. 我们的 adb 一直失败时，用 MuMuManager 的 adb 通道兜底；
5. 兜底也失败时返回 False 并留一条 warn 事件（界面能看到原因）；
6. `ensure_running()` 在模拟器已运行、adb 连不上时 `ready` 必须是 False；
7. adb 正常时 `ready` 是 True；
8. supervisor 拿到 adb 失败时把 run 判成 failed/-3，并说清是 adb 的问题
   （而不是把子项目拉起来让它自己报错）；
9. 启动指令已发出但 ready 超时时，supervisor 仍要把它记成「我们管理的」，
   并在失败返回前关掉——否则模拟器会自己在后台起来，下一轮变成
   「already running + adb 连不上」的循环报错。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

config.PROJECTS_YAML = ROOT / "tests" / "smoke_emulator_adb_registry.yaml"

DEVICES_EMPTY = "List of devices attached\n"
DEVICES_ONLINE = "List of devices attached\n127.0.0.1:16384\tdevice\n"
DEVICES_OFFLINE = "List of devices attached\n127.0.0.1:16384\toffline\n"
CONNECT_OK = "connected to 127.0.0.1:16384\n"
CONNECT_REFUSED = "cannot connect to 127.0.0.1:16384: 由于目标计算机积极拒绝，无法连接。 (10061)\n"

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  <- " + str(detail)))
    if not ok:
        FAILED.append(name)


class FakeBus:
    def __init__(self):
        self.events = []
        self._subs = []

    def emit(self, event):
        self.events.append(event)
        for queue in list(self._subs):
            queue.append(event)

    def subscribe(self):
        queue = []
        self._subs.append(queue)
        return queue

    def unsubscribe(self, queue):
        if queue in self._subs:
            self._subs.remove(queue)

    def start_server(self):
        return None

    def stop_server(self):
        return None

    def drain_spool(self, path):
        return None

    def messages(self, level=None):
        return [
            str(e.get("message"))
            for e in self.events
            if level is None or e.get("level") == level
        ]


def _make_controller(bus, run_impl, extra=None):
    """造一个 MuMuController，绕过真实 adb / MuMuManager，只测判定逻辑。"""
    from afk_core.emulator import MuMuController

    controller = MuMuController(bus, config.settings())
    cfg = dict(controller.settings["pre_run"]["emulator"])
    cfg.update(
        {
            "adb_connect_interval_sec": 0.0,
            "adb_connect_retries": 3,
            "manager_exe": str(Path(sys.executable)),
        }
    )
    if extra:
        cfg.update(extra)
    controller._cfg = lambda: cfg
    controller.adb_exe = lambda: str(Path(sys.executable))
    controller._run = run_impl
    return controller


def _scripted(connect_outputs, devices_sequence):
    """按脚本回放 connect / devices 输出，记录调用次数。"""
    state = {"connect": 0, "devices": 0, "manager": 0}

    def run_impl(cmd, timeout=60, cwd=None):
        if "devices" in cmd:
            index = min(state["devices"], len(devices_sequence) - 1)
            state["devices"] += 1
            return 0, devices_sequence[index]
        if "adb" in cmd:
            # MuMuManager 的 adb 通道：['<manager.exe>', 'adb', '-v', ...]
            state["manager"] += 1
            return 1, ""
        if "connect" not in cmd:
            # `_vmindex()` 会调 MuMuManager info —— 不是一次 adb 连接尝试
            return 1, ""
        index = min(state["connect"], len(connect_outputs) - 1)
        state["connect"] += 1
        return 0, connect_outputs[index]

    return run_impl, state


def _check_adb_verdicts():
    bus = FakeBus()
    run_impl, state = _scripted([CONNECT_OK], [DEVICES_ONLINE])
    controller = _make_controller(bus, run_impl)
    check("adb 正常连上时判定成功", controller.adb_connect() is True)

    # device offline：旧实现只看 cannot/failed，会误判成功
    bus = FakeBus()
    run_impl, state = _scripted([CONNECT_REFUSED], [DEVICES_OFFLINE])
    controller = _make_controller(bus, run_impl)
    check("adb devices 报 offline 时不能算连上", controller.adb_connect() is False, bus.messages())
    check("offline 时会重试到上限", state["connect"] == 3, state)
    check("重试用尽后不再多打一次 connect", state["manager"] == 0 or state["connect"] == 3, state)

    # 纯中文失败输出，既没有 cannot 也没有 failed
    bus = FakeBus()
    run_impl, _ = _scripted(["协议错误：无法建立连接\n"], [DEVICES_EMPTY])
    controller = _make_controller(bus, run_impl)
    check("纯中文失败输出不能算连上", controller.adb_connect() is False)
    warnings = bus.messages(level="warn")
    check("失败时留下 warn 事件（界面才看得到原因）", bool(warnings), bus.messages())

    # 退出码永远是 0 的坑：端口未开也必须判失败
    bus = FakeBus()
    run_impl, _ = _scripted([CONNECT_REFUSED], [DEVICES_EMPTY])
    controller = _make_controller(bus, run_impl)
    check("adb connect 退出码为 0 但端口不通时判失败", controller.adb_connect() is False)


def _check_retry_window():
    """MuMu 刚开机：前两次 10061，第三次才起来 —— 旧实现第一次就放弃。"""
    bus = FakeBus()
    run_impl, state = _scripted(
        [CONNECT_REFUSED, CONNECT_REFUSED, CONNECT_OK],
        [DEVICES_EMPTY, DEVICES_EMPTY, DEVICES_ONLINE],
    )
    controller = _make_controller(bus, run_impl)
    check("端口晚几秒才监听时能重试到成功", controller.adb_connect() is True, bus.messages())
    check("重试成功用了 3 次 connect", state["connect"] == 3, state)
    check("重试成功只报一次 ok", len([m for m in bus.messages() if ": ok" in m]) == 1, bus.messages())


def _check_manager_fallback():
    """我们的 adb 版本连不上时，用 MuMuManager 自己的 adb 通道兜底。"""
    bus = FakeBus()
    state = {"manager": 0, "connect": 0}

    def run_impl(cmd, timeout=60, cwd=None):
        if "devices" in cmd:
            if state["manager"]:
                return 0, DEVICES_ONLINE
            return 0, DEVICES_EMPTY
        if str(cmd[0]) == str(Path(sys.executable)) and "adb" in cmd:
            state["manager"] += 1
            return 0, '{"adb_host": "127.0.0.1", "adb_port": 16384, "cmd_output": "connected to 127.0.0.1:16384"}'
        state["connect"] += 1
        return 0, CONNECT_REFUSED

    controller = _make_controller(bus, run_impl)
    check("adb 一直失败时用 MuMuManager 兜底连上", controller.adb_connect() is True, bus.messages())
    check("兜底只在 adb 重试用尽后发生", state["manager"] == 1, state)
    check("兜底成功时事件里写明经 MuMuManager", any("MuMuManager" in m for m in bus.messages()), bus.messages())

    # 兜底也不通：必须返回 False，不能假装成功
    bus = FakeBus()

    def run_impl2(cmd, timeout=60, cwd=None):
        if "devices" in cmd:
            return 0, DEVICES_EMPTY
        if str(cmd[0]) == str(Path(sys.executable)) and "adb" in cmd:
            return 0, '{"cmd_output": "error: cannot connect"}'
        return 0, CONNECT_REFUSED

    controller = _make_controller(bus, run_impl2)
    check("兜底也不通时判失败", controller.adb_connect() is False)
    check("兜底失败也留 warn 事件", bool(bus.messages(level="warn")), bus.messages())

    # manager_exe 不存在：跳过兜底，不抛
    bus = FakeBus()
    run_impl, _ = _scripted([CONNECT_REFUSED], [DEVICES_EMPTY])
    controller = _make_controller(bus, run_impl, {"manager_exe": "Z:/nope/MuMuManager.exe"})
    check("manager_exe 不存在时跳过兜底且不抛", controller.adb_connect() is False)


def _check_ensure_running():
    """模拟器已经在跑这条分支：ready 必须跟随 adb 真实结果。"""
    from afk_core.emulator import MuMuController

    bus = FakeBus()
    controller = MuMuController(bus, config.settings())
    controller.is_running = lambda: True
    controller.version = lambda: '{"version": "6.8.8.0"}'
    controller.adb_connect = lambda port=None, attempts=None: False
    controller._show_then_minimize = lambda: None
    result = controller.ensure_running()
    check("模拟器在跑但 adb 连不上时 ready 是 False", result.get("ready") is False, result)
    check("结果里带 adb_ok 标记供上层区分", result.get("adb_ok") is False, result)
    check("仍然报告「原本就在跑」", result.get("was_running") is True and result.get("started") is False, result)

    bus = FakeBus()
    controller = MuMuController(bus, config.settings())
    controller.is_running = lambda: True
    controller.version = lambda: '{"version": "6.8.8.0"}'
    controller.adb_connect = lambda port=None, attempts=None: True
    controller._show_then_minimize = lambda: None
    result = controller.ensure_running()
    check("模拟器在跑且 adb 正常时 ready 是 True", result.get("ready") is True, result)


def _check_supervisor_uses_adb_flag():
    """supervisor 不能把「adb 连不上」的子项目拉起来，必须自己判失败。"""
    from afk_core import emulator as emulator_mod
    from afk_core.repair import RepairEngine
    from afk_core.supervisor import Supervisor

    bus = FakeBus()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    original = emulator_mod.MuMuController
    calls = {"stop": 0, "close_main": None}

    class StubController:
        def __init__(self, bus_arg, settings_arg=None):
            self.bus = bus_arg

        def adb_port(self):
            return 16384

        def ensure_running(self):
            return {
                "started": True,
                "was_running": False,
                "ready": True,
                "adb_ok": False,
                "main_started": True,
            }

        def stop(self, close_main=None):
            calls["stop"] += 1
            calls["close_main"] = close_main

    emulator_mod.MuMuController = StubController
    try:
        run = supervisor.start_project("EmuAdbProject")
        check("adb 连不上时仍返回一个 run", run is not None)
        if run is not None:
            run.finished_event.wait(timeout=10)
            check("adb 连不上时 run 判为失败", run.status == "failed", run.status)
            check("adb 连不上时退出码是 -3", run.exit_code == -3, run.exit_code)
            message = run.last_message or ""
            check("失败原因写明是 adb 连不上", "adb 连不上" in message, message)
            check("失败原因带上端口供排查", "16384" in message, message)
            check("不会真把子项目进程拉起来", run.process is None)
            errors = [e for e in bus.events if e.get("project") == "EmuAdbProject" and e.get("level") == "error"]
            check("事件流里有 error 级事件", bool(errors), bus.messages())
            check("启动阶段失败也要把刚拉起的模拟器关掉", calls["stop"] == 1, calls)
            check("关闭模拟器时一并收掉主程序", calls["close_main"] is True, calls)
    finally:
        emulator_mod.MuMuController = original
        supervisor.stop_all()

    # 不是我们启动的模拟器（was_running）失败时不能去关别人的东西
    bus = FakeBus()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    calls = {"stop": 0}

    class StubRunning:
        def __init__(self, bus_arg, settings_arg=None):
            self.bus = bus_arg

        def adb_port(self):
            return 16384

        def ensure_running(self):
            return {
                "started": False,
                "was_running": True,
                "ready": False,
                "adb_ok": False,
                "main_started": False,
            }

        def stop(self, close_main=None):
            calls["stop"] += 1

    emulator_mod.MuMuController = StubRunning
    try:
        run = supervisor.start_project("EmuAdbProject")
        if run is not None:
            run.finished_event.wait(timeout=10)
        check("不是我们启动的模拟器失败时不去关它", calls["stop"] == 0, calls)
    finally:
        emulator_mod.MuMuController = original
        supervisor.stop_all()

    # 「没就绪」的失败原因要说人话：卡片上就显示这一句，得能照着排查
    timeout_sec = int(
        ((config.settings().get("pre_run") or {}).get("emulator") or {}).get("ready_timeout_sec", 240)
    )
    bus = FakeBus()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    not_ready_messages = []

    class StubNotReady:
        def __init__(self, bus_arg, settings_arg=None):
            self.bus = bus_arg

        def adb_port(self):
            return 16384

        def ensure_running(self):
            return {"started": False, "was_running": True, "ready": False, "adb_ok": False, "main_started": False}

        def stop(self, close_main=None):
            return None

    emulator_mod.MuMuController = StubNotReady
    try:
        run = supervisor.start_project("EmuAdbProject")
        if run is not None:
            run.finished_event.wait(timeout=10)
            not_ready_messages.append(run.last_message or "")
    finally:
        emulator_mod.MuMuController = original
        supervisor.stop_all()

    message = not_ready_messages[0] if not_ready_messages else ""
    check("没就绪时的失败原因不再是英文占位串", "emulator not ready" not in message, message)
    check("没就绪原因写明是 MuMu 模拟器", "MuMu" in message, message)
    check("没就绪原因带上超时秒数", str(timeout_sec) in message, message)
    check("没就绪原因给出排查方向", "路径" in message or "手动" in message, message)

    # ready 超时：started=False 但启动指令已发出（模拟器正在后台起来）
    bus = FakeBus()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))
    calls = {"stop": 0, "close_main": None}

    class StubTimeout:
        def __init__(self, bus_arg, settings_arg=None):
            self.bus = bus_arg

        def adb_port(self):
            return 16384

        def ensure_running(self):
            return {
                "started": False,
                "was_running": False,
                "ready": False,
                "adb_ok": False,
                "main_started": False,
                "launch_requested": True,
            }

        def stop(self, close_main=None):
            calls["stop"] += 1
            calls["close_main"] = close_main

    emulator_mod.MuMuController = StubTimeout
    try:
        run = supervisor.start_project("EmuAdbProject")
        if run is not None:
            run.finished_event.wait(timeout=10)
            check("ready 超时也算我们启动了模拟器", run.managed_emulator is True, run.managed_emulator)
        check("ready 超时同样要把模拟器关掉（否则下一轮已在运行）", calls["stop"] == 1, calls)
        check("ready 超时不去关用户手动开的窗口", calls["close_main"] is False, calls)
    finally:
        emulator_mod.MuMuController = original
        supervisor.stop_all()

    # 模拟器准备阶段自己抛异常（MuMuManager 字段畸形、adb 缺失……）
    bus = FakeBus()
    supervisor = Supervisor(bus, settings=config.settings(), repair=RepairEngine(bus, config.settings()))

    class StubRaises:
        def __init__(self, bus_arg, settings_arg=None):
            self.bus = bus_arg

        def adb_port(self):
            return 16384

        def ensure_running(self):
            raise ValueError("invalid literal for int() with base 10: 'abc'")

        def stop(self, close_main=None):
            return None

    emulator_mod.MuMuController = StubRaises
    try:
        run = supervisor.start_project("EmuAdbProject")
        if run is not None:
            run.finished_event.wait(timeout=10)
            check("模拟器准备抛异常时 run 判为失败", run.status == "failed", run.status)
            check("模拟器准备抛异常时退出码是 -3", run.exit_code == -3, run.exit_code)
            message = run.last_message or ""
            check("模拟器准备抛异常时原因写明是模拟器准备失败", "模拟器准备失败" in message, message)
            check("模拟器准备抛异常时带上原始错误便于排查", "invalid literal" in message, message)
            check("模拟器准备抛异常时不再拉起子项目", run.process is None)
            errors = [e for e in bus.events if e.get("level") == "error"]
            check("模拟器准备抛异常要走 error 级（不是只 warn）", bool(errors), bus.messages())
    finally:
        emulator_mod.MuMuController = original
        supervisor.stop_all()


def _check_info_shapes():
    """MuMuManager 的 JSON 字段形状不受我们控制，别让它们把启动流程带崩。"""
    from afk_core.emulator import MuMuController

    bus = FakeBus()
    controller = MuMuController(bus, config.settings())
    controller._cfg = lambda: dict(controller.settings["pre_run"]["emulator"], manager_exe=str(Path(sys.executable)))
    controller._vmindex = lambda: "0"

    def _info(raw):
        controller._run = lambda cmd, timeout=60, cwd=None: (0, raw)
        return controller.info()

    for raw, label, expected in [
        ('{"adb_port": "16384"}', "端口是字符串时也能读出来", 16384),
        ('{"adb_port": 16384}', "端口是数字时照常读", 16384),
        ('{"adb_port": null}', "端口是 null 时退回配置值", 16384),
        ('{"adb_port": "abc"}', "端口是乱码时不抛异常", 16384),
    ]:
        _info(raw)
        try:
            got = controller.adb_port()
            check(label, got == expected, got)
        except Exception as exc:
            check(label, False, "{0}: {1}".format(type(exc).__name__, exc))

    for raw, label, expected in [
        ('{"is_android_started": "false"}', "布尔被写成字符串 false 时不算在跑", False),
        ('{"is_android_started": "true"}', "布尔被写成字符串 true 时算在跑", True),
        ('{"is_android_started": 0}', "布尔被写成 0 时不算在跑", False),
        ('{"is_android_started": 1}', "布尔被写成 1 时算在跑", True),
        ('{"is_process_started": true}', "只有进程起来时也算在跑", True),
        ('{}', "字段缺失时算没在跑", False),
    ]:
        _info(raw)
        try:
            check(label, controller.is_running() is expected, controller.info())
        except Exception as exc:
            check(label, False, "{0}: {1}".format(type(exc).__name__, exc))


def main():
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_emu_adb_")
    try:
        _check_adb_verdicts()
        _check_retry_window()
        _check_manager_fallback()
        _check_ensure_running()
        _check_supervisor_uses_adb_flag()
        _check_info_shapes()
    finally:
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()

    print("EMULATOR-ADB", "PASS" if not FAILED else "FAIL")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
