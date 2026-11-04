"""新外壳（导航栏/设置页/嵌入页）与 supervisor 扩展的功能性冒烟测试。

离屏运行；设置页测试使用临时 YAML，不触碰真实 configs/core/settings.yaml。

用法：
    QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR="C:\\Windows\\Fonts" \
        .venv/Scripts/python.exe tests/smoke_shell_ui.py
"""

import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

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

# 项目注册表同理：注册表热重载那条用例会真的把 projects.yaml 写一遍，
# 而 config.save_yaml 会抹掉注释、重排缩进——直接写仓库里的真文件会把
# configs/core/afk_projects.yaml 永久改脏（实测发生过）。指向临时副本。
_REAL_PROJECTS = config.PROJECTS_YAML
_TMP_PROJECTS = _TMP_DIR / "afk_projects.yaml"
shutil.copy(_REAL_PROJECTS, _TMP_PROJECTS)
config.PROJECTS_YAML = _TMP_PROJECTS

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


class _FakeScreen:
    """假屏幕，用来验证窗口在不同可用区域下的尺寸收敛。"""

    def __init__(self, x, y, width, height):
        from PySide6.QtCore import QRect

        self._rect = QRect(x, y, width, height)

    def availableGeometry(self):
        return self._rect


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

    # 快速连点：同一时间只能有一个指示条动画在跑。
    # 历史缺陷：每次 set_active(animate=True) 都新建 QVariantAnimation 且不取消
    # 上一个，旧动画的 valueChanged 仍在推进，两条交错会让指示条抖动/跳回。
    for key in ("page:Alpha", "settings", "overview", "page:Beta", "page:Alpha"):
        nav.set_active(key, animate=True)
        pump(app, 20)
    check("连点后仍有动画在跑", nav._anim is not None)
    deadline = time.monotonic() + 2.0
    while nav._anim is not None and time.monotonic() < deadline:
        pump(app, 20)
    check("动画结束引用被清空（不留孤儿对象）", nav._anim is None)
    check(
        "连点结束后指示条落在最后选中项上",
        abs(nav._indicator.y() - (nav.item_for_key("page:Alpha").y() + 8)) <= 2,
    )

    # finish() 之后动态追加的导航项，点击只应发出一次 navigate。
    # 历史缺陷：add_item 在 _finished 分支里又 connect 了一次 buttonClicked，
    # 而 __init__ 已经连过，一个点击会发出两遍信号。
    nav.add_item("page:Late", "Late")
    pump(app, 40)
    late_got = []
    nav.navigate.connect(lambda k: late_got.append(k))
    nav.item_for_key("page:Late").click()
    pump(app, 60)
    check("finish 后新增项点击只发一次信号", late_got == ["page:Late"])
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
    # 数值框改成「停止变动 400ms 后合并落盘」，等防抖窗口过去再断言
    pump(app, 700)
    saved = config.settings()
    check("结束后操作写入设置", saved.get("post_actions", {}).get("after_all") == "shutdown")
    check("结束后延迟写入设置", saved.get("post_actions", {}).get("delay_sec") == 120)

    # 3b. 连续拖动数值框不能逐格重写 settings.yaml + 刷屏日志。
    # 历史缺陷：每次 valueChanged 都走「读 yaml → 改 → 写回 → 写一行日志」，
    # 实测把延迟从 60 拖到 89 就落盘 29 次、日志面板多出 36 行，
    # 把用户真正要看的运行日志全冲掉了。
    smoke_logs = []
    page._log = lambda *a, **k: smoke_logs.append(k if k else a)
    writes = []
    orig_save = config.save_yaml

    def counting_save(path, data):
        writes.append(path)
        return orig_save(path, data)

    config.save_yaml = counting_save
    try:
        for value in range(60, 90):
            page.post_delay.setValue(value)
        # 不先 pump：负载高时这 30 次 setValue 本身可能就超过 400ms 防抖窗口，
        # 一进事件循环定时器立刻到期，断言会假失败（setValue 是同步的）。
        check("拖动数值框过程中不写配置文件（防抖）", len(writes) == 0)
        check("拖动数值框不逐格刷日志", len(smoke_logs) == 0)
        deadline = time.monotonic() + 2.0
        while not writes and time.monotonic() < deadline:
            pump(app, 40)
        check("停下后只落盘一次", len(writes) == 1)
        check("停下后只写一行日志", len(smoke_logs) == 1)
        check("落盘的是最后一次的值", config.settings().get("post_actions", {}).get("delay_sec") == 89)
        # 切换页面 / 退出前必须能把还没到期的改动写掉，否则用户改完立刻离开会丢配置
        page.post_delay.setValue(7)
        pump(app, 60)
        check("未到防抖窗口时磁盘还是旧值", config.settings().get("post_actions", {}).get("delay_sec") == 89)
        writes.clear()
        page.flush_pending()
        check("flush_pending 立刻落盘", len(writes) == 1)
        check("flush_pending 落盘值正确", config.settings().get("post_actions", {}).get("delay_sec") == 7)
    finally:
        config.save_yaml = orig_save

    # 4. reload_from_disk：外部改文件后界面跟随
    external = config.settings()
    external.setdefault("gui", {})["silent_open_projects"] = ["Beta"]
    external["post_actions"]["after_all"] = "exit"
    # 历史缺陷：reload_from_disk 不刷新「项目运行顺序」列表，外部改了
    # chain.order（或直接在「设置」tab 里改 yaml 保存）再回来，界面还是旧顺序。
    external.setdefault("chain", {})["order"] = ["Gamma", "Beta", "Alpha"]
    config.save_yaml(config.SETTINGS_YAML, external)
    page.reload_from_disk()
    pump(app, 40)
    check("reload 后勾选跟随文件", page.silent_checks["Beta"].isChecked() and not page.silent_checks["Alpha"].isChecked())
    check("reload 后操作跟随文件", page.post_combo.currentData() == "exit")
    reloaded_order = [
        page.order_list.item(i).data(Qt.UserRole) for i in range(page.order_list.count())
    ]
    check("reload 后运行顺序列表跟随文件", reloaded_order == ["Gamma", "Beta", "Alpha"])
    # 历史缺陷：列表高度写死 168px，而 QSS 每行 44px，5 个项目只显示 3 行半，
    # 第 4 行被切在中间（看起来像画坏了）。高度必须按行数算。
    row_h = max(1, page.order_list.sizeHintForRow(0))
    check("运行顺序列表放得下所有行（不切半行）",
          page.order_list.viewport().height() >= page.order_list.count() * row_h - 4)
    check("运行顺序列表行多时不再出现滚动条", page.order_list.verticalScrollBar().maximum() == 0)
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

    # 0a. 「打开日志」必须真的打开日志，不能只往实时日志面板写一行。
    #     历史缺陷：_open_logs 只调 self._log(...)，按钮点了没任何反应。
    opened = []
    page._open_logs_fn = lambda key: opened.append(key)
    page._open_logs()
    check("打开日志按钮会调起打开动作", opened == ["Alpha"])

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

        # 窗口不能在 1366 宽的笔记本上撑出屏幕。历史缺陷：5 张项目卡横排在一个
        # QHBoxLayout 里，每张卡 minimumSizeHint 约 264px，把最小宽度顶到 1592px，
        # w.resize(1360, 900) 被 Qt 直接拉回 1592。改用 FlowLayout 折行后回落。
        min_width = window.minimumSizeHint().width()
        check("窗口最小宽度不超过 1366（笔记本可完整显示）", min_width <= 1366)
        window.resize(1360, 900)
        pump(app, 80)
        check("resize(1360) 真正生效，不被最小宽度顶回", window.width() <= 1366)

        # 折行后的第二行项目卡不能压到下面的页签上。历史缺陷：FlowLayout 直接
        # addLayout 进 QVBoxLayout，父布局只按「单卡高度」分配空间，1062px 宽时
        # 第二行卡片被画在实时日志页签上面（两个控件互相重叠）。
        # 卡片区现在套在滚动区里，卡片的 geometry 是宿主坐标，必须映射到窗口
        # 坐标再比，否则比较的是两套坐标系。
        cards_bottom = max(
            card.mapTo(window, card.rect().bottomLeft()).y() for card in window.cards.values()
        )
        tabs_top = window.tabs.mapTo(window, window.tabs.rect().topLeft()).y()
        check("折行后的项目卡不会压住下方页签", cards_bottom <= tabs_top)
        host = window.cards_host
        check(
            "卡片容器的要求高度随折行数变化（宽屏一行、窄屏两行）",
            host.rows_height_for(1600) < host.rows_height_for(1000),
        )
        # 窗口最小高度不能由「折行数」决定。历史缺陷：FlowHost 用 heightForWidth
        # 报高度，Qt 在首轮布局前用 QWidget 默认宽度 640 去问，5 张卡被算成竖排
        # 848px，主窗口最小高度抬到 1165，show() 时窗口从 900 撑到 1165
        # （1080p 屏贴着任务栏，1366x768 屏直接超出屏幕）。
        window.resize(1360, 900)
        pump(app, 80)
        check("1360x900 下窗口高度不被折行撑大", window.height() <= 900)
        check("窗口最小高度不超过 900（1080p 屏可完整显示）", window.minimumSizeHint().height() <= 900)
        window.resize(1280, 900)
        pump(app, 120)
        check("1280 宽折成两行时窗口仍是 900 高", window.height() <= 900)
        check("窄窗口下卡片区域确实变高（折行生效）", host.minimumHeight() > 0)

        # 卡片区必须能滚动：历史缺陷是卡片直接摆进页面布局，5 张卡把主窗口
        # 最小高度顶到 821px，1366x768 笔记本（可用 728）上底部按钮掉出屏幕。
        # 套滚动区后最小高度只剩一行的量，矮屏上靠滚动看完整张卡。
        cards_scroll = window.cards_scroll
        check("卡片区套在滚动区里", cards_scroll is not None and cards_scroll.widget() is host)
        check("卡片区不再撑高窗口最小高度（矮屏可用）", window.minimumSizeHint().height() <= 640)
        # 卡片区高度要么等于折行所需高度，要么被可用空间吸附到更少的整行；
        # 不能超过折行所需（否则底部留一片空白）。
        check(
            "卡片区期望高度不超过折行所需高度",
            0 < cards_scroll.content_height() <= host.rows_height_for(host.width()),
        )
        check(
            "卡片区高度按整行吸附（不会切半行卡片）",
            cards_scroll.content_height() % max(1, host.single_row_height() + host.flow().v_spacing())
            in (0, host.single_row_height()),
        )
        # 窗口被压到最小高度时，卡片区也必须留得下一整行：CARDS_MIN_HEIGHT(120)
        # 比一行卡片(=179)矮，否则卡片停在「未运行」那行、四个按钮全被切掉。
        check(
            "窗口最小高度下卡片区放得下整行（按钮不被切）",
            cards_scroll.minimumHeight() >= host.single_row_height(),
        )
        check(
            "窗口最小高度至少是一整行卡片 + 其余控件",
            window.minimumSizeHint().height() >= cards_scroll.minimumHeight(),
        )

        # 1366x768 笔记本（可用 728）上必须能同时看到两行卡片：历史缺陷是卡片区
        # 只拿到一行高度、第二行卡片被切在按钮中间，看起来像界面画坏了。
        fake_small = _FakeScreen(0, 0, 1366, 728)
        prev_screen = gm.MainWindow.screen
        gm.MainWindow.screen = lambda self, _s=fake_small: _s
        try:
            small_win = gm.MainWindow()
            small_win.show()
            pump(app, 200)
            small_rows = small_win.cards_scroll.rows_for(small_win.cards_scroll.height())
            check("1366x728 屏上两行卡片都能完整显示", small_rows >= 2)
            small_win.hide()
            small_win._quit()
            small_win.deleteLater()
            pump(app, 60)
        finally:
            gm.MainWindow.screen = prev_screen

        # 窗口必须完整落在屏幕可用区域内。历史缺陷：尺寸写死 1360x900 且从不
        # 校正位置，1366x768（可用 728）上底边掉出屏幕 132px。
        pref_w, pref_h = gm._preferred_window_size()
        check("首选窗口尺寸不超过屏幕可用区域", pref_w <= 1360 and pref_h <= 900)
        fake = _FakeScreen(0, 0, 1366, 728)
        prev_screen = gm.MainWindow.screen
        gm.MainWindow.screen = lambda self, _s=fake: _s
        try:
            probe_win = gm.MainWindow()
            probe_win.show()
            pump(app, 120)
            frame = probe_win.frameGeometry()
            if not (frame.top() >= 0 and frame.top() + frame.height() <= 728):
                print("  [debug] frame={0} size={1}x{2} minHint={3} initial_applied={4}".format(
                    frame.getRect(), probe_win.width(), probe_win.height(),
                    probe_win.minimumSizeHint().height(),
                    getattr(probe_win, "_initial_geometry_applied", None)))
            check("1366x728 屏上窗口不超出可用区域", frame.top() >= 0 and frame.top() + frame.height() <= 728)
            probe_win.hide()
            probe_win._quit()
            probe_win.deleteLater()
            pump(app, 40)
        finally:
            gm.MainWindow.screen = prev_screen

        # 导航指示条必须落在被选中项上，不能在左上角画出孤立的蓝色块。
        # 历史缺陷：_NavIndicator 构造时就 show()，在没有任何导航项被选中
        # 之前用 QWidget 默认几何 100x30 @ (0,0)，压住品牌 Logo。
        overview_item = window.nav.item_for_key("overview")
        ind = window.nav._indicator
        check(
            "导航指示条落在当前选中项上",
            abs(ind.y() - (overview_item.y() + 8)) <= 2 and ind.height() <= overview_item.height(),
        )

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

        # 「错误报告」页不能在用户首次打开时显示空列表。
        # 历史缺陷：report_list 只在点「刷新」按钮时填充，切到本页永远 0 条
        # （磁盘上实际有 40+ 份报告），用户会以为没有错误报告。
        reports_index = None
        for i in range(window.tabs.count()):
            if window.tabs.widget(i) is window._reports_tab:
                reports_index = i
                break
        check("错误报告页存在", reports_index is not None)
        if reports_index is not None:
            window.tabs.setCurrentIndex(reports_index)
            pump(app, 120)
            check("切到错误报告页会加载列表", window._reports_loaded)
            # 空目录时也应给出一条不可选中的说明项，而不是空白
            has_placeholder = (
                window.report_list.count() == 1
                and window.report_list.item(0).data(Qt.UserRole) is None
            )
            check("错误报告页不会是完全空白", window.report_list.count() > 0 or has_placeholder)

        # 在「设置」tab 直接改 settings.yaml 保存后，总览页头部的开关必须跟着变。
        # 历史缺陷：_save_text 只重读了 self.settings，头部三个控件（运行前更新 /
        # 定时启动 / 时间）仍是构造时的旧值，界面与文件不一致，用户会以为没保存成功。
        dirty = dict(window.settings)
        dirty["pre_run"] = dict(dirty.get("pre_run") or {}, enabled=False)
        dirty["schedule"] = {"enabled": True, "time": "07:30"}
        window.settings_edit.setPlainText(yaml.safe_dump(dirty, allow_unicode=True, sort_keys=False))
        window._save_text(window.settings_edit, config.SETTINGS_YAML)
        pump(app, 60)
        check("设置 tab 保存后「运行前更新」跟随文件", window.pre_run_check.isChecked() is False)
        check("设置 tab 保存后「定时启动」跟随文件", window.schedule_check.isChecked() is True)
        check(
            "设置 tab 保存后定时时间跟随文件",
            window.schedule_time.time().toString("HH:mm") == "07:30",
        )

        # 拖动「定时启动」时间框不能逐格写盘 + 刷屏日志。
        # 历史缺陷：timeChanged 直连 _toggle_schedule，按住箭头连点 30 格就是
        # 30 次整份写 settings.yaml、30 行「定时启动: ...」日志，把运行日志冲掉。
        sched_writes = []
        sched_logs = []
        orig_save = config.save_yaml
        orig_log_fn = window._append_log

        def counting_save(path, data):
            sched_writes.append(path)
            return orig_save(path, data)

        def counting_log(project, level, message, *args, **kwargs):
            sched_logs.append(message)
            return orig_log_fn(project, level, message, *args, **kwargs)

        base_time = window.schedule_time.time()
        try:
            config.save_yaml = counting_save
            window._append_log = counting_log
            from PySide6.QtCore import QTime

            moment = QTime(base_time.hour(), base_time.minute())
            for _step in range(30):
                moment = moment.addSecs(60)
                window.schedule_time.setTime(moment)
            # 这里不能先 pump：整机负载高时这 30 次 setTime 本身就可能花掉 400ms
            # 以上，一进事件循环防抖定时器立刻到期，断言会假失败。setTime 是同步
            # 的，不 pump 就不会有写盘，直接断言即可。
            check("拖动时间框过程中不写配置文件（防抖）", len(sched_writes) == 0)
            check("拖动时间框不逐格刷日志", len(sched_logs) == 0)
            deadline = time.monotonic() + 2.0
            while not sched_writes and time.monotonic() < deadline:
                pump(app, 30)
            check("停下后定时时间只落盘一次", len(sched_writes) == 1)
            check("停下后只写一行定时日志", len(sched_logs) == 1)
            check(
                "落盘的是最后一次拖到的时间",
                str((config.settings().get("schedule") or {}).get("time")) == moment.toString("HH:mm"),
            )
        finally:
            config.save_yaml = orig_save
            window._append_log = orig_log_fn

        # 保存设置后托盘勾选与桌面小组件可见性也要跟随文件。
        # 历史缺陷：_save_text 只同步了头部三个控件，用户在「设置」tab 把
        # idle.enabled 关掉、gui.widget_enabled 打开并保存，托盘菜单仍显示旧勾选，
        # 小组件既不出现也不消失，用户只能再去点一遍托盘。
        sync_cfg = dict(window.settings)
        sync_cfg["idle"] = dict(sync_cfg.get("idle") or {}, enabled=False)
        sync_cfg["gui"] = dict(sync_cfg.get("gui") or {}, widget_enabled=True)
        window.settings_edit.setPlainText(yaml.safe_dump(sync_cfg, allow_unicode=True, sort_keys=False))
        window._save_text(window.settings_edit, config.SETTINGS_YAML)
        pump(app, 60)
        check("设置 tab 保存后托盘「空闲自动挂机」跟随文件", window.idle_action.isChecked() is False)
        check("设置 tab 保存后托盘「桌面小组件」跟随文件", window.widget_action.isChecked() is True)
        check(
            "设置 tab 保存后桌面小组件真的显示出来",
            window.widget is not None and window.widget.isVisible() is True,
        )

        # 「设置」tab 的 yaml 编辑器不能被过期内容覆盖回旧值。
        # 历史缺陷：settings_edit 只在开窗时读一次盘，用户在头部开关里改了设置，
        # 再回本页点「保存设置」，会把界面里那份过期文本整份写回，刚改的配置
        # 被静默覆盖（实测 pre_run.enabled 改成 False 又被写回 True）。
        window.pre_run_check.setChecked(True)
        pump(app, 60)
        editor_cfg = yaml.safe_load(window.settings_edit.toPlainText()) or {}
        check(
            "头部开关改动会同步进设置 tab 编辑器",
            bool((editor_cfg.get("pre_run") or {}).get("enabled")) is True,
        )
        window.pre_run_check.setChecked(False)
        pump(app, 60)
        window._save_text(window.settings_edit, config.SETTINGS_YAML)
        pump(app, 60)
        check(
            "保存设置 tab 不会把头部刚改的值覆盖回旧值",
            bool((config.settings().get("pre_run") or {}).get("enabled")) is False,
        )
        # 用户手动编辑过编辑器时，程序不得覆盖他正在写的内容
        window.settings_edit.setPlainText("# user editing\n" + window.settings_edit.toPlainText())
        pump(app, 60)
        check("用户编辑后编辑器被标记为脏", window._settings_edit_dirty is True)
        typed = window.settings_edit.toPlainText()
        window.settings_page.idle_check.setChecked(not window.settings_page.idle_check.isChecked())
        pump(app, 60)
        check("用户正在编辑的内容不会被程序覆盖", window.settings_edit.toPlainText() == typed)

        # 顶层不是映射的 YAML 也能通过语法校验（`just a string` / `- a`），
        # 存进去之后每处 settings().get(...) 都会 AttributeError，程序从此打不开、
        # 也改不回来（只能手工编辑文件）。必须在校验阶段就挡住。
        from PySide6.QtWidgets import QMessageBox as _QMB

        saved_msgs = []
        orig_critical = _QMB.critical
        # 签名是 critical(parent, 标题, 正文)，正文在第三个参数
        _QMB.critical = staticmethod(lambda *a, **k: saved_msgs.append(str(a[2]) if len(a) > 2 else ""))
        try:
            before_bytes = config.SETTINGS_YAML.read_bytes()
            for bad_text in ("just a string", "- a\n- b\n"):
                window.settings_edit.setPlainText(bad_text)
                window._save_text(window.settings_edit, config.SETTINGS_YAML)
                pump(app, 30)
                check(
                    "非映射的 YAML 被拒绝保存（{0}）".format(bad_text.split(chr(10))[0][:12]),
                    config.SETTINGS_YAML.read_bytes() == before_bytes,
                )
            check("非映射的 YAML 会提示用户", any("顶层" in m for m in saved_msgs))
        finally:
            _QMB.critical = orig_critical
            # 把编辑器恢复成合法内容，避免影响后续断言
            window._load_text(window.settings_edit, config.SETTINGS_YAML)
            window._settings_edit_dirty = False

        # 底层读取也要兜住：即使文件被外部改坏，settings() 也不能把字符串
        # 直接返回给调用方（否则每处 .get() 都 AttributeError，程序打不开）。
        probe_yaml = config.SETTINGS_YAML.parent / "_probe_broken.yaml"
        probe_yaml.write_text("just a string", encoding="utf-8")
        try:
            check("load_yaml 对非映射内容回落成空映射", config.load_yaml(probe_yaml, {}) == {})
        finally:
            probe_yaml.unlink(missing_ok=True)

        # 在「项目注册表」tab 保存后，界面必须跟着重载。
        # 历史缺陷：_save_text 对 projects.yaml 什么都不做，文件对了但项目卡、
        # 左侧导航、supervisor、设置页全是开窗时那份旧注册表——新增的项目不出现，
        # 删掉的项目卡片还在。
        original_registry = config.registry()
        mutated = yaml.safe_load(yaml.safe_dump(original_registry, allow_unicode=True))
        mutated.setdefault("projects", {})
        extra_key = "SmokeExtra"
        mutated["projects"][extra_key] = dict(next(iter(mutated["projects"].values())))
        mutated["projects"][extra_key]["display_name"] = "冒烟新增项目"
        dropped_key = next(iter(mutated["projects"]))
        if dropped_key != extra_key:
            mutated["projects"].pop(dropped_key, None)
        window.registry_edit.setPlainText(yaml.safe_dump(mutated, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check("保存注册表后新项目出现在项目卡", extra_key in window.cards)
        check("保存注册表后删掉的项目卡片消失", dropped_key not in window.cards)
        check("保存注册表后 supervisor 用新注册表", extra_key in (window.supervisor.registry.get("projects") or {}))
        check("保存注册表后设置页运行顺序跟随", extra_key in [
            window.settings_page.order_list.item(i).data(Qt.UserRole)
            for i in range(window.settings_page.order_list.count())
        ])
        if (mutated["projects"][extra_key] or {}).get("gui"):
            check("保存注册表后新项目出现在导航", window.nav.item_for_key("page:" + extra_key) is not None)
            check("导航项排在「设置」前面", [
                window.nav.layout().itemAt(i).widget()
                for i in range(window.nav.layout().count())
            ].index(window.nav.item_for_key("page:" + extra_key)) < [
                window.nav.layout().itemAt(i).widget()
                for i in range(window.nav.layout().count())
            ].index(window.nav.item_for_key("settings")))
        check("保存注册表后副标题跟随项目数量", "冒烟新增项目" in window._subtitle_label.text())
        # 还原：把注册表写回原样，后续断言依赖原来的 5 个项目
        window.registry_edit.setPlainText(yaml.safe_dump(original_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check("还原注册表后卡片数量回到原来", sorted(window.cards) == sorted(original_registry["projects"]))

        # 拖动桌面小组件不能逐帧重写 settings.yaml。
        # 历史缺陷：moveEvent 每帧都调 config.save_yaml，实测拖动一次写 59 次
        # 整份配置；改成合并到「停下 400ms 后」落盘一次。
        widget = window.widget
        check("主窗口已构建小组件", widget is not None)
        if widget is not None:
            write_count = []
            orig_save = config.save_yaml

            def counting_save(path, data):
                write_count.append(path)
                return orig_save(path, data)

            config.save_yaml = counting_save
            try:
                # 前面的断言可能刚刚让小组件显示出来、或留下未到期的防抖写盘，
                # 先把这些残留动作跑干净，再开始计数，否则会把它们算进拖动里。
                pump(app, 700)
                write_count.clear()
                # 离屏下隐藏窗口的 move() 不会产生 moveEvent，必须先 show 出来
                widget.show()
                pump(app, 80)
                write_count.clear()
                base = widget.current_position()
                for step in range(20):
                    widget.move(base[0] + step * 3, base[1] + step * 2)
                pump(app, 40)
                check("拖动过程中不写配置文件（防抖）", len(write_count) == 0)
                deadline = time.monotonic() + 1.5
                while not write_count and time.monotonic() < deadline:
                    pump(app, 30)
                check("停下后只落盘一次", len(write_count) == 1)
                saved_pos = (window.settings.get("gui") or {}).get("widget_pos")
                check("落盘位置与小组件实际位置一致", list(saved_pos or []) == widget.current_position())
            finally:
                config.save_yaml = orig_save

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
    # 整个文件的运行数据隔离：test_supervisor_extensions 会创建 EventBus 并
    # 触发 post_action 事件，若不隔离就会写进用户真实 logs/events.jsonl。
    # 提升到 main() 级别可覆盖所有子测试（原先只在 test_main_window 里隔离，
    # 而在它之前执行的 test_supervisor_extensions 会漏出去）。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_shell_ui_")
    try:
        test_nav_rail(app)
        test_settings_page(app)
        test_project_page(app)
        test_supervisor_extensions()
        test_winembed_sanity()
        test_main_window(app)
    finally:
        config.SETTINGS_YAML = _REAL_SETTINGS
        config.PROJECTS_YAML = _REAL_PROJECTS
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
    print()
    if FAILURES:
        print("SHELL-UI {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("SHELL-UI PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
