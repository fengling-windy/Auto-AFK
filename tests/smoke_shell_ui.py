"""新外壳（导航栏/设置页/嵌入页）与 supervisor 扩展的功能性冒烟测试。

离屏运行；设置页测试使用临时 YAML，不触碰真实 configs/core/settings.yaml。

用法：
    QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR="C:\\Windows\\Fonts" \
        .venv/Scripts/python.exe tests/smoke_shell_ui.py
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

# 在导入任何依赖设置的模块前，把 settings.yaml 指向临时文件
_REAL_SETTINGS = config.SETTINGS_YAML
_TMP_DIR = Path(tempfile.mkdtemp(prefix="afk_shell_smoke_"))
_TMP_SETTINGS = _TMP_DIR / "settings.yaml"
shutil.copy(_REAL_SETTINGS, _TMP_SETTINGS)
config.SETTINGS_YAML = _TMP_SETTINGS

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from afk_core.events import EventBus
from afk_core.supervisor import Supervisor, apply_chain_order
from gui.nav_rail import NavRail
from gui.project_page import ProjectPage
from gui.settings_page import SettingsPage
from gui import winembed

FAILURES = []


def check(name, ok):
    print("{0} {1}".format("PASS" if ok else "FAIL", name))
    if not ok:
        FAILURES.append(name)


def pump(app, ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


FAKE_REGISTRY = {
    "projects": {
        "Alpha": {"display_name": "Alpha（示例项目甲）", "gui": {"args": ["app.py"]}},
        "Beta": {"display_name": "Beta（示例项目乙）", "gui": {"exe": "gui.exe"}},
        "Gamma": {"display_name": "Gamma（无界面项目）"},
    },
    "groups": [],
    "reserved": [],
}


class FakeSupervisor:
    def __init__(self):
        self.spawned = []
        self.extra_args = []
        self.report_ports = []
        self.result = None  # None / Popen-like

    def spawn_project_gui(self, key, extra_args=None, report_port=None):
        self.spawned.append(key)
        self.extra_args.append(list(extra_args or []))
        self.report_ports.append(report_port)
        return self.result


class FakeProc:
    def __init__(self, pid=4321):
        self.pid = pid
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self._alive = False


def test_nav_rail(app):
    nav = NavRail()
    got = []
    nav.navigate.connect(got.append)
    nav.add_item("overview", "总览")
    nav.add_item("cap", "项目", section=True)
    nav.add_item("page:Alpha", "Alpha")
    nav.add_item("page:Beta", "Beta")
    nav.add_item("settings", "设置")
    nav.finish()
    nav.show()
    pump(app, 60)

    nav.set_active("page:Alpha", animate=False)
    check("导航高亮切换", nav.active_key() == "page:Alpha")
    check("指示条跟随高亮项", abs(nav._indicator.y() - (nav.item_for_key("page:Alpha").y() + 8)) <= 2)

    nav.item_for_key("page:Beta").set_dot("#60a5fa")
    nav.item_for_key("page:Beta").click()
    pump(app, 60)
    check("点击导航发出 navigate 信号", got == ["page:Beta"])
    check("点击后高亮跟随", nav.active_key() == "page:Beta")
    nav.hide()


def test_settings_page(app):
    # 干净起点
    config.save_yaml(config.SETTINGS_YAML, {"gui": {}, "post_actions": {"after_all": "none", "delay_sec": 60}, "chain": {}})
    logs = []
    page = SettingsPage(log_fn=lambda *a, **k: logs.append(k if k else a), registry=FAKE_REGISTRY)
    page.show()
    pump(app, 60)

    # 1. 静默启动勾选 → 写入 gui.silent_open_projects
    page.silent_checks["Alpha"].setChecked(True)
    pump(app, 40)
    saved = config.settings()
    check("静默启动勾选写入设置", saved.get("gui", {}).get("silent_open_projects") == ["Alpha"])
    page.silent_checks["Beta"].setChecked(True)
    page.silent_checks["Alpha"].setChecked(False)
    pump(app, 40)
    saved = config.settings()
    check("静默启动取消勾选同步", saved.get("gui", {}).get("silent_open_projects") == ["Beta"])

    # 2. 运行顺序：选中 Beta(行1) 上移 → chain.order = [Beta, Alpha, Gamma]
    page.order_list.setCurrentRow(1)
    page._move_selected(-1)
    pump(app, 40)
    saved = config.settings()
    check("运行顺序写入设置", saved.get("chain", {}).get("order") == ["Beta", "Alpha", "Gamma"])
    # 顶部再上移应无效且不报错
    page.order_list.setCurrentRow(0)
    page._move_selected(-1)
    pump(app, 20)
    check("列表顶部上移无副作用", config.settings().get("chain", {}).get("order") == ["Beta", "Alpha", "Gamma"])

    # 3. 结束后操作：combo → shutdown；延迟 → 120
    idx = page.post_combo.findData("shutdown")
    page.post_combo.setCurrentIndex(idx)
    page.post_delay.setValue(120)
    pump(app, 40)
    saved = config.settings()
    check("结束后操作写入设置", saved.get("post_actions", {}).get("after_all") == "shutdown")
    check("结束后延迟写入设置", saved.get("post_actions", {}).get("delay_sec") == 120)

    # 4. reload_from_disk：外部改文件后界面跟随
    external = config.settings()
    external.setdefault("gui", {})["silent_open_projects"] = ["Beta"]
    external["post_actions"]["after_all"] = "exit"
    config.save_yaml(config.SETTINGS_YAML, external)
    page.reload_from_disk()
    pump(app, 40)
    check("reload 后勾选跟随文件", page.silent_checks["Beta"].isChecked() and not page.silent_checks["Alpha"].isChecked())
    check("reload 后操作跟随文件", page.post_combo.currentData() == "exit")
    page.hide()


def test_project_page(app):
    sup = FakeSupervisor()
    logs = []
    page = ProjectPage("Alpha", FAKE_REGISTRY["projects"]["Alpha"], "#4fc3f7", sup, lambda *a, **k: logs.append(a))
    page.show()
    pump(app, 40)

    # 嵌入与窗口相关的原生调用全部替换为桩
    recorded = {}
    import gui.project_page as pp

    check("初始状态点为灰色（未启动）", page.dot_state() == pp.DOT_OFF)

    orig_find = pp.winembed.find_window_for_spawn
    orig_alive = pp.winembed.window_alive
    orig_show = pp.winembed.show_hwnd
    orig_matches = pp.winembed.window_matches
    orig_is_admin = pp.is_admin
    orig_embed = page._embed

    def fake_find(**kw):
        recorded.setdefault("find_kw", kw)
        if sup.result is None:
            return None, "界面进程已退出（code 0），未出现窗口"
        return 0x1234, None

    pp.winembed.find_window_for_spawn = fake_find
    pp.winembed.window_alive = lambda hwnd: recorded.setdefault("alive", True)
    pp.winembed.show_hwnd = lambda hwnd, show=True: recorded.setdefault("show", []).append((hwnd, show))
    pp.winembed.window_matches = lambda hwnd, pids=None, title_keywords=None: recorded.setdefault(
        "validated", (hwnd, set(pids or set()), list(title_keywords or []))
    ) or True
    pp.is_admin = lambda: True

    def fake_embed(hwnd):
        recorded["embedded"] = hwnd
        page._set_state(ProjectPage.STATE_EMBEDDED, "已嵌入（测试桩）")

    page._embed = fake_embed

    # 0. 导航不自动启动：ensure_started 未调用时不应 spawn
    check("未点击启动时不自动 spawn", sup.spawned == [])

    # 1. spawn 失败 → failed
    sup.result = None
    page.ensure_started()
    pump(app, 200)
    check("spawn 失败进入 failed 状态", page._state == ProjectPage.STATE_FAILED)

    # 2. spawn 成功 + 找到窗口（页面可见）→ 嵌入；查找参数为进程树/标题关键词
    page.REPORT_WAIT_SEC = 0.4  # 缩短上报等待（测试无真实引导上报，走回退匹配）
    sup.result = FakeProc(4321)
    sup.spawned.clear()
    recorded.clear()
    page.ensure_started()
    pump(app, 1200)
    check("点击启动后调用 spawn_project_gui", sup.spawned == ["Alpha"])
    check("python GUI 以引导上报端口启动", isinstance(sup.report_ports[-1], int))
    check("找到窗口后调用嵌入", recorded.get("embedded") == 0x1234)
    check("嵌入后状态为 embedded", page._state == ProjectPage.STATE_EMBEDDED)
    find_kw = recorded.get("find_kw") or {}
    check("回退匹配传入 spawn 进程树", 4321 in (find_kw.get("pids") or set()))
    check("回退匹配传入标题关键词参数", "title_keywords" in find_kw)
    check("嵌入前做了归属校验", recorded.get("validated", (None, None, None))[0] == 0x1234)
    check("嵌入后状态点为绿色", page.dot_state() == pp.DOT_ON)

    # 2a. 引导脚本上报 HWND → 嵌入（不经回退匹配）
    import socket as _socket

    real_spawn = sup.spawn_project_gui

    def fake_spawn_with_report(key, extra_args=None, report_port=None):
        # 模拟引导脚本：UDP 上报 HWND，再走真实记录桩
        if report_port:
            _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM).sendto(
                "HWND 43210".encode("ascii"), ("127.0.0.1", report_port)
            )
        return real_spawn(key, extra_args, report_port)

    sup.spawn_project_gui = fake_spawn_with_report
    pp.winembed.window_matches = lambda hwnd, pids=None, title_keywords=None: recorded.setdefault(
        "validated2", (hwnd, set(pids or set()))
    ) or True
    page2 = ProjectPage("BetaPy", {"display_name": "BetaPy（纯 python GUI）", "gui": {"args": ["app.py"]}}, "#81c784", sup, lambda *a, **k: None)
    page2.show()
    pump(app, 30)
    page2.REPORT_WAIT_SEC = 5.0

    def fake_embed2(hwnd):
        recorded["embedded"] = hwnd

    page2._embed = fake_embed2
    sup.result = FakeProc(4322)
    recorded.clear()
    page2.ensure_started()
    pump(app, 3600)  # 首次上报后有 2.5s 的收集窗（取最新 HWND）
    check("引导上报的 HWND 触发嵌入", recorded.get("embedded") == 43210)
    check("上报窗口经过归属校验", recorded.get("validated2", (None, None))[0] == 43210)
    sup.spawn_project_gui = real_spawn
    page2.hide()

    # 2b. 归属校验不通过 → 拒绝嵌入
    pp.winembed.window_matches = lambda hwnd, pids=None, title_keywords=None: False
    recorded.clear()
    sup.result = FakeProc(4325)
    page._set_state(ProjectPage.STATE_IDLE, "未启动")
    page.ensure_started()
    pump(app, 900)  # 引导路径含 0.4s 上报等待，再走回退匹配
    check("非本项目窗口被拒绝嵌入", page._state == ProjectPage.STATE_FAILED and "embedded" not in recorded)
    check("拒绝时提示归属不符", "不属于本项目" in (page.state_label.text()))
    pp.winembed.window_matches = lambda hwnd, pids=None, title_keywords=None: True

    # 2c. require_admin + 非管理员：直接提示，不 spawn
    admin_spec = {"display_name": "Admin（需提权）", "require_admin": True, "gui": {"args": ["app.py"]}}
    apage = ProjectPage("Admin", admin_spec, "#ffb74d", sup, lambda *a, **k: None)
    apage.show()
    pump(app, 30)
    pp.is_admin = lambda: False
    sup.spawned.clear()
    apage.ensure_started()
    pump(app, 200)
    check("非管理员下需提权项目直接失败", apage._state == ProjectPage.STATE_FAILED and not sup.spawned)
    check("提示需要管理员权限", "管理员" in apage.state_label.text())
    pp.is_admin = lambda: True
    apage.hide()

    # 3. 静默模式：页面不可见时找到窗口 → 隐藏而非嵌入
    page2 = ProjectPage("Beta", FAKE_REGISTRY["projects"]["Beta"], "#81c784", sup, lambda *a, **k: None)

    def fake_embed2(hwnd):
        recorded["embedded2"] = hwnd

    page2._embed = fake_embed2
    # page2 不 show() → isVisible() False
    sup.result = FakeProc(4322)
    recorded.clear()
    page2.ensure_started(silent=True)
    pump(app, 200)
    check("静默模式隐藏窗口而非嵌入", recorded.get("show") == [(0x1234, False)] and "embedded2" not in recorded)
    check("静默模式状态为 hidden", page2._state == ProjectPage.STATE_HIDDEN)

    # 4. 静默隐藏后用户切进该页 → 嵌入
    page2.ensure_started()
    pump(app, 80)
    check("进入页面时嵌入已隐藏窗口", recorded.get("embedded2") == 0x1234)

    # 5. tick：嵌入窗口被关闭 → 回到 idle（显式置为 embedded，不依赖前面状态）
    recorded["alive"] = True
    page._hwnd = 0x1234
    page._set_state(ProjectPage.STATE_EMBEDDED, "已嵌入（测试桩）")
    pump(app, 30)
    recorded["alive"] = False
    page.tick()
    pump(app, 40)
    check("窗口关闭后 tick 回到 idle", page._state == ProjectPage.STATE_IDLE and page._hwnd is None)
    check("关闭后状态点回到灰色", page.dot_state() == pp.DOT_OFF)

    # 5b. shutdown：关闭嵌入窗口 + 结束界面进程树
    close_calls = []
    kill_calls = []
    orig_close = pp.winembed.close_hwnd
    orig_kill = pp.winembed.kill_tree
    pp.winembed.close_hwnd = lambda hwnd: close_calls.append(hwnd)
    pp.winembed.kill_tree = lambda pid: kill_calls.append(pid)
    page._hwnd = 0x1234
    recorded["alive"] = True
    page._proc = FakeProc(4321)
    page.shutdown()
    pump(app, 30)
    check("退出时向嵌入窗口投递 WM_CLOSE", close_calls == [0x1234])
    check("退出时结束界面进程树", kill_calls == [4321])
    pp.winembed.close_hwnd = orig_close
    pp.winembed.kill_tree = orig_kill

    # 6. 网页模式（BAAH）：真实本地 HTTP 服务验证端口/参数/就绪探测
    import http.server
    import socketserver
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    real_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    web_spec = {"display_name": "WebProj（网页示例）", "gui": {"args": ["editor.py"], "web": True, "port_arg": "--port", "no_show_arg": "--no-show"}}
    wpage = ProjectPage("WebProj", web_spec, "#ffb74d", sup, lambda *a, **k: None)
    wpage.show()
    pump(app, 40)
    pp.winembed.pick_free_port = lambda: real_port

    def fake_embed_web(port):
        recorded["embedded_web"] = port
        wpage._set_state(ProjectPage.STATE_EMBEDDED, "已嵌入（测试桩）")

    wpage._embed_web = fake_embed_web
    sup.result = FakeProc(4323)
    recorded.clear()
    wpage.ensure_started()
    pump(app, 400)
    check("网页模式传入 --no-show", "--no-show" in (sup.extra_args[-1] if sup.extra_args else []))
    check("网页模式传入固定端口", str(real_port) in (sup.extra_args[-1] if sup.extra_args else []))
    check("HTTP 就绪后触发网页嵌入", recorded.get("embedded_web") == real_port)
    check("网页模式状态为 embedded", wpage._state == ProjectPage.STATE_EMBEDDED)
    check("网页模式状态点为绿色", wpage.dot_state() == pp.DOT_ON)

    # 7. 网页服务停止 → tick 探测后回 idle
    httpd.shutdown()
    for _ in range(ProjectPage.WEB_CHECK_EVERY * 4 + 2):
        wpage.tick()
        pump(app, 5)
    pump(app, 40)
    check("网页服务停止后 tick 回到 idle", wpage._state == ProjectPage.STATE_IDLE and wpage._port is None)

    pp.winembed.find_window_for_spawn = orig_find
    pp.winembed.window_alive = orig_alive
    pp.winembed.show_hwnd = orig_show
    pp.winembed.window_matches = orig_matches
    pp.is_admin = orig_is_admin
    page._embed = orig_embed
    page.hide()
    page2.hide()
    wpage.hide()


def test_supervisor_extensions():
    # 运行顺序排序：列出的按优先级，未列出的保持原相对顺序垫底
    ordered = apply_chain_order(["A", "B", "C", "D"], ["C", "A"])
    check("chain.order 组内排序", ordered == ["C", "A", "B", "D"])
    check("空顺序保持原序", apply_chain_order(["A", "B"], []) == ["A", "B"])

    # exit 后动作：发出带 data.action 的 post_action 事件
    bus = EventBus()
    queue = bus.subscribe()
    sup = Supervisor(bus, settings={"post_actions": {"after_all": "exit", "delay_sec": 0}})
    sup._run_post_action("exit")
    event = None
    deadline = time.time() + 5
    while time.time() < deadline and event is None:
        try:
            event = queue.get_nowait()
        except Exception:
            time.sleep(0.05)
    ok = (
        event is not None
        and event.get("kind") == "post_action"
        and (event.get("data") or {}).get("action") == "exit"
    )
    check("exit 后动作发出带标记的事件", ok)
    bus.unsubscribe(queue)
    bus.stop_server()


def test_winembed_sanity():
    # 验证 EnumWindows 机制本身可用：可见窗口数随桌面上开着什么而变（可能只有几个），
    # 所以断言「至少能枚举到窗口」而不是一个会随环境漂移的具体数量。
    windows = winembed.list_windows()
    check("EnumWindows 枚举到可见窗口", len(windows) >= 1)
    check("窗口条目结构完整", all(len(w) == 4 and w[0] for w in windows[:20]))
    # include_hidden 应当比默认（仅可见）枚举到更多窗口，说明过滤逻辑生效
    all_windows = winembed.list_windows(include_hidden=True)
    check("include_hidden 枚举到不少于可见窗口", len(all_windows) >= len(windows))


def test_main_window(app):
    """完整实例化 MainWindow（隔离：临时 settings/日志目录，spawn 打桩）。"""
    config.save_yaml(
        config.SETTINGS_YAML,
        {
            "pre_run": {"enabled": False},
            "housekeeping": {"enabled": False},
            "schedule": {"enabled": False},
            "gui": {"minimize_to_tray": False, "autostart_chain_on_launch": False},
            "post_actions": {"after_all": "none", "delay_sec": 60},
        },
    )
    tmp_logs = _TMP_DIR / "logs"
    config.LOGS_DIR = tmp_logs
    config.PROJECT_LOGS = tmp_logs / "projects"
    config.EVENTS_FILE = tmp_logs / "events.jsonl"
    config.STATE_FILE = tmp_logs / "state.json"

    import gui.main as gm

    window = gm.MainWindow()
    window.show()
    pump(app, 200)

    gui_projects = [k for k, s in (config.registry().get("projects", {}) or {}).items() if s.get("gui")]
    try:
        check("页面数 = 总览 + 项目页 + 设置页", window.stack.count() == 1 + len(gui_projects) + 1)
        check("导航含总览/设置项", window.nav.item_for_key("overview") is not None and window.nav.item_for_key("settings") is not None)
        for key in gui_projects:
            check("导航含项目页 {0}".format(key), window.nav.item_for_key("page:" + key) is not None)
        check("默认显示总览页", window.stack.currentIndex() == 0 and window.nav.active_key() == "overview")

        # spawn 打桩：页面上的「启动界面」不会真的拉起项目 GUI
        spawn_calls = []
        window.supervisor.spawn_project_gui = lambda key, extra_args=None, report_port=None: spawn_calls.append(key) or None
        window._navigate("page:March7thAssistant")
        pump(app, 300)
        page = window.project_pages["March7thAssistant"]
        check("导航切到项目页", window.stack.currentWidget() is page)
        check("导航不自动启动项目界面", page._state == ProjectPage.STATE_IDLE and not spawn_calls)
        page.ensure_started()
        pump(app, 300)
        check("点击启动后进入失败态（桩返回 None）", page._state == ProjectPage.STATE_FAILED)
        window._navigate("settings")
        pump(app, 60)
        check("导航切到设置页", window.stack.currentWidget() is window.settings_page)
        window._navigate("overview")
        pump(app, 60)
        check("导航切回总览", window.stack.currentIndex() == 0)

        # _poll 全流程：快照/胶囊/导航点/嵌入页 tick
        window._poll()
        pump(app, 60)
        check("_poll 正常执行", True)

        # exit 后动作事件 → 调度退出
        bus_event = {"project": "core", "kind": "post_action", "level": "info", "message": "post action executed: exit", "data": {"action": "exit"}}
        window.bus.emit(bus_event)
        pump(app, 1300)
        check("exit 事件触发 _quit（定时器已停）", not window.timer.isActive())
    finally:
        try:
            window.hide()
            window._quit()
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    try:
        test_nav_rail(app)
        test_settings_page(app)
        test_project_page(app)
        test_supervisor_extensions()
        test_winembed_sanity()
        test_main_window(app)
    finally:
        config.SETTINGS_YAML = _REAL_SETTINGS
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
    print()
    if FAILURES:
        print("SHELL-UI {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("SHELL-UI PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
