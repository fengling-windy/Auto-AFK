"""新外壳（导航栏/设置页/项目卡）与 supervisor 扩展的功能性冒烟测试。

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

# 修复规则同理：保存动作会真的写文件，指向临时副本。
_REAL_REPAIR = config.REPAIR_YAML
_TMP_REPAIR = _TMP_DIR / "repair_rules.yaml"
shutil.copy(_REAL_REPAIR, _TMP_REPAIR)
config.REPAIR_YAML = _TMP_REPAIR

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from afk_core.events import EventBus
from afk_core.supervisor import ProjectRun, Supervisor, apply_chain_order
from gui.nav_rail import NavRail
from gui.settings_page import SettingsPage

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
        self.result = None  # None / Popen-like

    def spawn_project_gui(self, key, extra_args=None):
        self.spawned.append(key)
        self.extra_args.append(list(extra_args or []))
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
    # 等「动画停 + 指示条真的落到最后选中项」这两个条件同时成立。
    # 只在固定时限里等 _anim 归零是不够的：整机满载时 220ms 的动画跑到一半
    # 就被判定超时，紧接着的落位断言会因为指示条还在半路而偶发失败。
    target_y = nav.item_for_key("page:Alpha").y() + 8
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if nav._anim is None and abs(nav._indicator.y() - target_y) <= 2:
            break
        pump(app, 20)
    check("动画结束引用被清空（不留孤儿对象）", nav._anim is None)
    check(
        "连点结束后指示条落在最后选中项上",
        abs(nav._indicator.y() - target_y) <= 2,
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

    # 导航项多到超出侧栏高度时，选中靠后的项目必须自动滚进视口。
    # 历史缺陷：导航项放进 QScrollArea（免得项目一多就把窗口最小高度顶上去）
    # 之后，set_active 只挪指示条、不动滚动位置，选中项与高亮都在视口外，
    # 用户点完看起来像没反应。
    tall = NavRail()
    tall.resize(196, 240)
    tall.add_item("overview", "总览")
    for index in range(25):
        tall.add_item("page:P{0:02d}".format(index), "项目{0:02d}".format(index))
    tall.add_item("settings", "设置")
    tall.finish()
    tall.show()
    pump(app, 60)
    scroll_bar = tall._scroll.verticalScrollBar()
    check("导航项多于视口时出现滚动条", scroll_bar.maximum() > 0)
    viewport = tall._scroll.viewport()
    tall.set_active("page:P20", animate=False)
    pump(app, 80)
    target_item = tall.item_for_key("page:P20")
    item_top = target_item.mapTo(viewport, target_item.rect().topLeft()).y()
    item_bottom = item_top + target_item.height()
    check("选中靠后的导航项会自动滚进视口", item_top >= 0 and item_bottom <= viewport.height())
    indicator_top = tall._indicator.mapTo(viewport, tall._indicator.rect().topLeft()).y()
    check("指示条与选中项一起进入视口", 0 <= indicator_top and indicator_top < viewport.height())
    tall.set_active("overview", animate=False)
    pump(app, 80)
    first_item = tall.item_for_key("overview")
    first_top = first_item.mapTo(viewport, first_item.rect().topLeft()).y()
    check("选回顶部项时滚动区跟着回到顶部", first_top >= 0 and first_top < viewport.height())
    tall.hide()

    # 长项目名不能把侧栏最小宽度顶掉。历史缺陷：NavItem 是 QPushButton，
    # minimumSizeHint 等于整段文字宽度——注册表里写一个长 display_name，
    # 侧栏最小宽度实测 1176px，整个窗口最小宽度跟着爆到 1927px（1920 屏都放不下）。
    long_rail = NavRail()
    long_rail.resize(196, 320)
    long_name = "超长项目名与显示名测试" * 6
    long_rail.add_item("overview", "总览")
    long_rail.add_item("page:Long", long_name)
    long_rail.add_item("settings", "设置")
    long_rail.finish()
    long_rail.show()
    pump(app, 80)
    long_item = long_rail.item_for_key("page:Long")
    check("超长导航项不把侧栏最小宽度顶出去", long_item.minimumSizeHint().width() <= 120)
    check("超长导航项在界面上被省略号裁掉", long_item.display_text() != long_name)
    check("超长导航项保留完整名称", long_item.text() == long_name)
    check("超长导航项把完整名称放进 tooltip", long_item.toolTip() == long_name)
    long_rail.hide()


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

    # 5. 新增挂机项目：项目键会变成 configs/<键> 目录名、日志名、报告名。
    # 历史缺陷：只校验「非空 + 不重复」，带 Windows 保留字符的键照样写进注册表，
    # 之后 supervisor 建 configs/<键> 目录直接 OSError(123)，链在这一步崩掉，
    # 而错误信息只有一个 WinError，看不出是项目键的问题。
    added = []
    page2 = SettingsPage(log_fn=lambda *a, **k: None, registry=FAKE_REGISTRY)
    page2.project_added.connect(added.append)
    page2.show()
    pump(app, 40)
    before_projects = len(config.registry()["projects"])
    for bad_key in ['bad:name', 'bad|name', 'bad<name>', 'bad"name"', 'bad\\name']:
        page2.add_key_edit.setText(bad_key)
        page2.add_path_edit.setText("BAAH")
        page2._add_project()
    pump(app, 40)
    check("含 Windows 保留字符的项目键被拒绝", len(config.registry()["projects"]) == before_projects)
    check("非法键不发出新增信号", added == [])
    page2.add_key_edit.setText("SmokeNewProj")
    page2.add_path_edit.setText("BAAH")
    page2._add_project()
    pump(app, 40)
    check("合法项目键照常写入注册表", "SmokeNewProj" in (config.registry()["projects"] or {}))
    check("合法键发出一次新增信号", added == ["SmokeNewProj"])
    # 收尾：把这一条从临时副本里删掉，避免影响后面的「新增项目出现在卡片」断言
    _cleanup = config.load_yaml(config.PROJECTS_YAML, {})
    (_cleanup.get("projects") or {}).pop("SmokeNewProj", None)
    config.save_yaml(config.PROJECTS_YAML, _cleanup)
    page2.hide()


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


def test_main_window(app):
    """完整实例化 MainWindow（隔离：临时 settings/日志目录，spawn 打桩）。"""
    # 开窗前先在配置里放一组并行块（取注册表前两个项目，天然相邻）。
    # 历史缺陷：_build_ui 建完卡片不回灌并行状态，只有 _rebuild_cards()（换肤 /
    # 重载注册表）才刷新——上次退出前设好的并行组，重开程序后卡片全写「并行」、
    # 连线也不画，看着像设置丢了（其实 chain.parallel_blocks 还在，跑起来仍是同时跑）。
    _boot_keys = list((config.registry().get("projects", {}) or {}).keys())
    _boot_pair = _boot_keys[:2]
    config.save_yaml(
        config.SETTINGS_YAML,
        {
            "pre_run": {"enabled": False},
            "housekeeping": {"enabled": False},
            "schedule": {"enabled": False},
            "gui": {"minimize_to_tray": False, "autostart_chain_on_launch": False},
            "post_actions": {"after_all": "none", "delay_sec": 60},
            "chain": {
                "order": list(_boot_keys),
                "parallel_blocks": [_boot_pair] if len(_boot_pair) == 2 else [],
            },
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
        # 页面是「总览 + 项目状态 + 设置」：项目界面不再嵌入本窗口（历史实现是给
        # 每个带 gui 段的项目建一个嵌入页，用 QWindow.fromWinId 把项目窗口抓进来）。
        check("页面数 = 总览 + 项目状态 + 设置页", window.stack.count() == 3)
        check("导航含总览/项目状态/设置项",
              window.nav.item_for_key("overview") is not None
              and window.nav.item_for_key("projects") is not None
              and window.nav.item_for_key("settings") is not None)
        check("导航不再有项目嵌入页",
              not [k for k in window.nav.item_keys() if str(k).startswith("page:")])
        check("默认显示总览页", window.stack.currentIndex() == 0 and window.nav.active_key() == "overview")
        # 配置里预置的并行组，开窗就该画出来（不点任何按钮）
        if len(_boot_pair) == 2:
            check("开窗后配置里的并行组已经生效（{0}）".format(window.parallel_groups()),
                  window.parallel_groups() == [list(_boot_pair)])
            check("开窗后参与并行的卡片显示「并行中」",
                  all(window.cards[k].is_parallel() for k in _boot_pair if k in window.cards))
            check("开窗后连线层已经拿到这一组",
                  [[getattr(w, "key", None) for w in g] for g in (window.links._groups or [])]
                  == [list(_boot_pair)])

        # 卡片的详情行不能跟状态行互相矛盾。历史缺陷：update_state 的兜底文案
        # 无条件写「运行中...」，排队中的项目（status=pending、还没有 pid/exit/
        # message）显示成「等待」+「运行中...」，用户以为它已经在跑了。
        card = window.cards[gui_projects[0]]
        card.update_state({"status": "pending"})
        check("排队中的卡片不显示成「运行中」", "运行中" not in card.detail_label.text())
        card.update_state({"status": "running", "pid": 123})
        check("运行中的卡片详情显示 pid", "pid=123" in card.detail_label.text())
        card.update_state({"status": "hang"})
        check("卡死的卡片不显示成「运行中」", "运行中" not in card.detail_label.text())
        # 复原：上面改了卡片文案，卡片高度跟着变，会影响后面「整行吸附」那条
        # 断言的单行高度（同一份 content_height 却按新行高取模）。
        card.update_state(None)
        window._sync_cards_height()
        pump(app, 60)

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
        # 用「实测卡片高度」算行数：CardsScrollArea 内部记的 _row_height 取的是
        # sizeHint 最大值，和 FlowLayout 排版时用的控件真实高度会差 1px，直接调
        # rows_for() 会把 538px（三行）算成两行，那不是界面问题而是度量口径问题。
        row_h = min((c.height() for c in window.cards.values() if c.height() > 0), default=0)
        vsp = host.flow().v_spacing()
        step = max(1, row_h + vsp)
        content = cards_scroll.content_height()
        snapped_rows = max(1, (content + vsp) // step) if row_h > 0 else 0
        expected = snapped_rows * step - vsp
        check(
            "卡片区高度按整行吸附（不会切半行卡片）",
            snapped_rows >= 1 and abs(content - expected) <= 3,
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
            check("1366x728 屏上窗口不超出可用区域", frame.top() >= 0 and frame.top() + frame.height() <= 728)
            probe_win.hide()
            probe_win._quit()
            probe_win.deleteLater()
            pump(app, 40)
        finally:
            gm.MainWindow.screen = prev_screen

        # 窗口尺寸要能记住：历史缺陷是尺寸从不落盘，每次启动都回到首选值，
        # 用户调好的大小关掉再开又变回去，只能重调一遍。
        big = _FakeScreen(0, 0, 1920, 1040)
        prev_screen = gm.MainWindow.screen
        gm.MainWindow.screen = lambda self, _s=big: _s
        try:
            geo_probe = gm.MainWindow()
            geo_probe.show()
            pump(app, 200)
            geo_probe.resize(1240, 860)
            pump(app, 500)
            geo_probe._flush_window_geometry()
            persisted_size = (config.settings().get("gui") or {}).get("window_size")
            check(
                "窗口尺寸会写进 settings.yaml{0}".format("" if persisted_size == [1240, 860] else "（实际 {0}）".format(persisted_size)),
                persisted_size == [1240, 860],
            )
            geo_probe.hide()
            geo_probe._quit()
            geo_probe.deleteLater()
            pump(app, 60)
            restored = gm.MainWindow()
            restored.show()
            pump(app, 200)
            check(
                "重启后恢复上次窗口尺寸{0}".format(
                    "" if (restored.width(), restored.height()) == (1240, 860)
                    else "（实际 {0}x{1}）".format(restored.width(), restored.height())
                ),
                (restored.width(), restored.height()) == (1240, 860),
            )
            # 换了小屏之后，记住的尺寸必须被夹进新屏幕可用区域（不能沿用大屏尺寸）。
            small = _FakeScreen(0, 0, 1366, 728)
            gm.MainWindow.screen = lambda self, _s=small: _s
            restored.hide()
            restored._quit()
            restored.deleteLater()
            pump(app, 60)
            clamped = gm.MainWindow()
            clamped.show()
            pump(app, 200)
            check(
                "换小屏后记住的尺寸被夹进可用区域{0}".format(
                    "" if (clamped.width() <= 1366 and clamped.height() <= 728)
                    else "（实际 {0}x{1}）".format(clamped.width(), clamped.height())
                ),
                clamped.width() <= 1366 and clamped.height() <= 728,
            )
            clamped.hide()
            clamped._quit()
            clamped.deleteLater()
            pump(app, 60)
        finally:
            gm.MainWindow.screen = prev_screen
            # 别把测试期间的窗口尺寸留给后面的用例
            leftover = config.settings()
            (leftover.get("gui") or {}).pop("window_size", None)
            config.save_yaml(config.SETTINGS_YAML, leftover)

        # 历史缺陷：_show_main_window 无条件 showNormal()，最大化后最小化到托盘，
        # 再点托盘恢复就把窗口打回「最大化之前的小尺寸」，用户还得重新最大化一次
        # （空闲提示弹出时同样会把最大化窗口打回小窗）。
        window.showMaximized()
        pump(app, 150)
        maximized_before = window.isMaximized()
        window.hide()
        pump(app, 100)
        window._show_main_window()
        pump(app, 200)
        check("托盘恢复后仍保持最大化", maximized_before and window.isMaximized())
        window.showNormal()
        pump(app, 150)
        check("取消最大化后恢复普通窗口", not window.isMaximized())

        # 历史缺陷：_NavIndicator 构造时就 show()，在没有任何导航项被选中
        # 之前用 QWidget 默认几何 100x30 @ (0,0)，压住品牌 Logo。
        overview_item = window.nav.item_for_key("overview")
        ind = window.nav._indicator
        check(
            "导航指示条落在当前选中项上",
            abs(ind.y() - (overview_item.y() + 8)) <= 2 and ind.height() <= overview_item.height(),
        )

        # 「界面」按钮走原生独立打开：只 spawn 一个进程，不再嵌入本窗口
        spawn_calls = []
        window.supervisor.spawn_project_gui = lambda key, extra_args=None: spawn_calls.append(key) or None
        window._open_project_gui(gui_projects[0])
        pump(app, 400)
        check("「界面」按钮拉起项目自己的进程", spawn_calls == [gui_projects[0]])
        check("打开界面失败时写告警日志", "无法打开" in window.log_view.toPlainText())
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

        # 开窗之后才产生的报告必须能被看到。
        # 历史缺陷：「只在第一次进入时扫盘」实现成 _reports_loaded 之后永不重扫，
        # 链跑挂了生成的报告（正是最需要看的那种）切过来仍然是「还没有错误报告」，
        # 界面与磁盘状态相反。现在按报告目录 mtime 判断是否需要重扫。
        if reports_index is not None:
            # 只数真实报告条目：空目录时会有一条 data 为 None 的占位说明项，
            # 它会被第一条真报告顶掉，所以不能拿总条数当基线。
            def _real_reports():
                return [
                    window.report_list.item(i).data(Qt.UserRole)
                    for i in range(window.report_list.count())
                    if window.report_list.item(i).data(Qt.UserRole) is not None
                ]

            before = len(_real_reports())
            new_dir = Path(config.REPORTS_DIR) / "20260101_000000_SmokeProbe"
            new_dir.mkdir(parents=True, exist_ok=True)
            (new_dir / "snapshot.json").write_text(
                '{"project": "SmokeProbe", "reason": "smoke"}', encoding="utf-8"
            )
            window.tabs.setCurrentIndex(0)
            pump(app, 60)
            window.tabs.setCurrentIndex(reports_index)
            pump(app, 150)
            names = _real_reports()
            check("开窗后新生成的错误报告会出现在列表里", str(new_dir) in names)
            check("重扫后列表条数增加", len(names) == before + 1)
            # 目录没变时不该重复扫盘（否则每次切页都要 stat 200 个目录）
            mtime_before = window._reports_mtime
            window.tabs.setCurrentIndex(0)
            pump(app, 60)
            window.tabs.setCurrentIndex(reports_index)
            pump(app, 120)
            check("报告目录没变时不重复扫盘", window._reports_mtime == mtime_before)

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

        # 界面上的开关写盘时不能整份覆盖掉期间的外部改动。
        # 历史缺陷：_toggle_pre_run / _flush_schedule / _flush_widget_pos 都是
        # 「改内存里那份 settings 再整份写回」。只要期间 CLI、另一进程或用户手改
        # 过 settings.yaml，点一下开关就把那些改动全抹掉了（实测把 idle.minutes
        # 改成 77 后点一下「运行前更新」，文件又变回 30，chain.order 也回到空）。
        external = config.settings()
        external["idle"] = dict(external.get("idle") or {}, minutes=77)
        external["chain"] = dict(external.get("chain") or {}, order=["BAAH"])
        config.save_yaml(config.SETTINGS_YAML, external)
        window.pre_run_check.setChecked(not window.pre_run_check.isChecked())
        pump(app, 150)
        after_toggle = config.settings()
        check(
            "点开关写盘时保留外部改过的 idle.minutes{0}".format(
                "" if (after_toggle.get("idle") or {}).get("minutes") == 77
                else "（实际 {0}）".format((after_toggle.get("idle") or {}).get("minutes"))
            ),
            (after_toggle.get("idle") or {}).get("minutes") == 77,
        )
        check(
            "点开关写盘时保留外部改过的 chain.order",
            ((after_toggle.get("chain") or {}).get("order") or []) == ["BAAH"],
        )
        external2 = config.settings()
        external2["idle"] = dict(external2.get("idle") or {}, minutes=88)
        config.save_yaml(config.SETTINGS_YAML, external2)
        window.schedule_check.setChecked(not window.schedule_check.isChecked())
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            pump(app, 30)
            if (config.settings().get("idle") or {}).get("minutes") == 88:
                break
        check(
            "改定时启动写盘时保留外部改过的 idle.minutes",
            (config.settings().get("idle") or {}).get("minutes") == 88,
        )

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
            # 空内容 / 只有注释：safe_load 返回 None，写进去之后 settings() 变成
            # 空映射——运行顺序、空闲挂机、开机自启、配色全部静默回到默认值。
            before_bytes = config.SETTINGS_YAML.read_bytes()
            saved_msgs.clear()
            for empty_text in ("", "# 读取失败: 磁盘只读\n", "\n\n"):
                window.settings_edit.setPlainText(empty_text)
                window._save_text(window.settings_edit, config.SETTINGS_YAML)
                pump(app, 30)
                check(
                    "空内容的 YAML 被拒绝保存（{0!r}）".format(empty_text[:8]),
                    config.SETTINGS_YAML.read_bytes() == before_bytes,
                )
            check("空内容的 YAML 会提示用户", any("内容为空" in m for m in saved_msgs))
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

        # 注册表里手写 `gui: true` 不能把程序搞崩。
        # 历史缺陷：gui/main.py 与 afk_core/supervisor.py 都直接对 spec["gui"]
        # 调 .get()，写成布尔值会在 MainWindow 构造时抛
        # AttributeError: 'bool' object has no attribute 'get'，用户连界面都打不开，
        # 也没法把配置改回来。config.registry() 现在把它归一成 None。
        bool_gui_yaml = config.SETTINGS_YAML.parent / "_probe_gui_bool.yaml"
        bool_gui_yaml.write_text(
            "projects:\n"
            "  BoolGui:\n"
            "    display_name: 布尔界面\n"
            "    gui: true\n"
            "  MapGui:\n"
            "    display_name: 映射界面\n"
            "    gui:\n"
            "      args: [app.py]\n",
            encoding="utf-8",
        )
        real_projects_yaml = config.PROJECTS_YAML
        try:
            config.PROJECTS_YAML = bool_gui_yaml
            probe_registry = config.registry()
            check("注册表里 gui: true 不会让程序打不开", isinstance(probe_registry, dict))
            check(
                "gui: true 被归一成「没有界面入口」",
                probe_registry["projects"]["BoolGui"]["gui"] is None,
            )
            check(
                "gui 写成映射时原样保留",
                probe_registry["projects"]["MapGui"]["gui"] == {"args": ["app.py"]},
            )
        finally:
            config.PROJECTS_YAML = real_projects_yaml
            bool_gui_yaml.unlink(missing_ok=True)

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
        # 先人为造出「残留键」：把要被删掉的项目写进运行顺序、隐藏列表和并行组。
        # 历史缺陷：保存注册表后只清了卡片/导航/设置页，这三处仍留着它的键，
        # 于是「设置 → 已隐藏的项目」里永远挂着一个幽灵项，点「放回总览」没反应，
        # 链每次跑到 chain.order 里的死键都刷一条「跳过未知项目」。
        second_key = [k for k in (config.registry().get("projects") or {}) if k != dropped_key][0]
        window._save_chain_order([dropped_key, second_key])
        window._parallel_blocks = [[dropped_key, second_key]]
        window._write_parallel_blocks()
        window._hidden_projects = [dropped_key]
        # 静默启动列表（gui.silent_open_projects）里也塞一个死键

        def _pin_silent(settings):
            gui_cfg = settings.setdefault("gui", {})
            if not isinstance(gui_cfg, dict):
                gui_cfg = {}
                settings["gui"] = gui_cfg
            gui_cfg["silent_open_projects"] = [dropped_key]

        window._mutate_settings(_pin_silent)
        pump(app, 120)
        check("动手前注册表里确实有那个项目", dropped_key in window.cards)
        window.registry_edit.setPlainText(yaml.safe_dump(mutated, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check("保存注册表后新项目出现在项目卡", extra_key in window.cards)
        check("保存注册表后删掉的项目卡片消失", dropped_key not in window.cards)
        _after_delete = config.settings()
        _order_after = ((_after_delete.get("chain") or {}).get("order")) or []
        _hidden_after = ((_after_delete.get("gui") or {}).get("hidden_projects")) or []
        _blocks_after = ((_after_delete.get("chain") or {}).get("parallel_blocks")) or []
        _silent_after = ((_after_delete.get("gui") or {}).get("silent_open_projects")) or []
        check(
            "删掉项目后运行顺序里不再有它（{0}）".format(_order_after),
            dropped_key not in _order_after,
        )
        check(
            "删掉项目后隐藏列表里不再有它（{0}）".format(_hidden_after),
            dropped_key not in _hidden_after,
        )
        check(
            "删掉项目后并行组里不再有它（{0}）".format(_blocks_after),
            dropped_key not in [k for b in _blocks_after for k in b],
        )
        check(
            "删掉项目后静默启动列表里不再有它（{0}）".format(_silent_after),
            dropped_key not in _silent_after,
        )
        check("保存注册表后 supervisor 用新注册表", extra_key in (window.supervisor.registry.get("projects") or {}))
        check("保存注册表后设置页运行顺序跟随", extra_key in [
            window.settings_page.order_list.item(i).data(Qt.UserRole)
            for i in range(window.settings_page.order_list.count())
        ])
        # 在设置页里排好的运行顺序，必须同步到总览的卡片顺序。
        # 历史缺陷：_on_settings_changed 只换肤不重排，第一次改顺序恰好因为首启
        # 主题从空变成 dark 走了 _rebuild_cards 而「碰巧生效」，第二次就纹丝不动，
        # 用户在设置里排好的顺序根本没落到卡片上（也看不出哪一步错了）。
        _order_keys = window.settings_page._ordered_project_keys()
        _rotated = _order_keys[1:] + _order_keys[:1]
        window.settings_page.order_list.blockSignals(True)
        window.settings_page.order_list.clear()
        for _k in _rotated:
            _item = QListWidgetItem(_k)
            _item.setData(Qt.UserRole, _k)
            window.settings_page.order_list.addItem(_item)
        window.settings_page.order_list.blockSignals(False)
        window.settings_page._save_order()
        pump(app, 200)
        check("设置页改运行顺序后卡片跟着重排（{0}）".format(window.visible_card_keys()),
              window.visible_card_keys() == _rotated)
        # 再改一次：确认不是「只在主题变化那次碰巧生效」
        _rotated2 = _rotated[2:] + _rotated[:2]
        window.settings_page.order_list.blockSignals(True)
        window.settings_page.order_list.clear()
        for _k in _rotated2:
            _item = QListWidgetItem(_k)
            _item.setData(Qt.UserRole, _k)
            window.settings_page.order_list.addItem(_item)
        window.settings_page.order_list.blockSignals(False)
        window.settings_page._save_order()
        pump(app, 200)
        check("连续第二次改运行顺序卡片也跟随（{0}）".format(window.visible_card_keys()),
              window.visible_card_keys() == _rotated2)
        # 设置页「启动时后台打开项目界面」的勾选项同样要按注册表重建。
        # 历史缺陷：这几个复选框只在 SettingsPage.__init__ 里建一次，新增的带界面
        # 项目永远没有勾选框（无法让它开机静默启动），删掉的项目反而留下一个勾上
        # 就写进 silent_open_projects 的死项。
        if (mutated["projects"][extra_key] or {}).get("gui"):
            check("保存注册表后静默启动勾选项跟随", extra_key in window.settings_page.silent_checks)
        dropped_gui = dropped_key if (original_registry["projects"].get(dropped_key) or {}).get("gui") else None
        if dropped_gui:
            check("保存注册表后删掉项目的静默勾选项消失",
                  dropped_gui not in window.settings_page.silent_checks)
        # 嵌入页已经整体移除，注册表里再多带 gui 段的项目也不会长出导航项。
        check("保存注册表后不会新增项目导航项",
              not [k for k in window.nav.item_keys() if str(k).startswith("page:")])
        check("保存注册表后副标题跟随项目数量", "冒烟新增项目" in window._subtitle_label.text())
        # 「项目状态」页同样要跟着注册表走。历史缺陷：它只在 __init__ 与切页时
        # reload，用户停在总览页存盘后，那页还是开窗时那份清单（卡片有了、
        # 状态页却说没有这个项目）。
        check("保存注册表后项目状态页跟上新项目（{0}）".format(sorted(window.projects_page._blocks)),
              extra_key in window.projects_page._blocks)
        check("保存注册表后项目状态页去掉已删项目",
              dropped_key not in window.projects_page._blocks)
        # 还原：把注册表写回原样，后续断言依赖原来的 5 个项目
        window.registry_edit.setPlainText(yaml.safe_dump(original_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check("还原注册表后卡片数量回到原来", sorted(window.cards) == sorted(original_registry["projects"]))

        # 注册表里 enabled: false 的项目，链会直接跳过。历史缺陷：卡片上一个
        # 标识都没有（状态还是「等待」、启动按钮照常可点），用户点「启动」只得到
        # 一行日志「project disabled, skipped」，看起来像点了没反应。
        disabled_key = [k for k in original_registry["projects"] if k != extra_key][0]
        disabled_registry = yaml.safe_load(yaml.safe_dump(original_registry, allow_unicode=True))
        disabled_registry["projects"][disabled_key]["enabled"] = False
        window.registry_edit.setPlainText(
            yaml.safe_dump(disabled_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        _dcard = window.cards[disabled_key]
        check("注册表里禁用后卡片标出「已禁用」（{0}）".format(_dcard.status_label.text()),
              _dcard.status_label.text() == "已禁用")
        check("禁用的项目启动按钮不可点", not _dcard.start_btn.isEnabled())
        check("禁用的项目并行按钮不可点", not _dcard.parallel_btn.isEnabled())
        # 「停止」要留着：注册表里取消勾选不会让已经在跑的进程退出，得留个入口。
        check("禁用的项目没在跑时「停止」也置灰", not _dcard.stop_btn.isEnabled())
        check("禁用的项目状态提示说明原因（{0}）".format(_dcard.detail_label.text()),
              "enabled: false" in _dcard.detail_label.text())
        # _poll 每轮会把卡片刷成「等待 / 未运行」，不能把「已禁用」这条线索冲掉
        window._poll()
        pump(app, 60)
        check("刷新状态后禁用提示仍在（{0}）".format(_dcard.status_label.text()),
              _dcard.status_label.text() == "已禁用")
        window.registry_edit.setPlainText(yaml.safe_dump(original_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        _dcard = window.cards[disabled_key]
        check("重新启用后卡片恢复可点（{0}）".format(_dcard.status_label.text()),
              _dcard.start_btn.isEnabled() and _dcard.status_label.text() != "已禁用")

        # 卡片上的「启动 / 停止」要跟着运行状态走。历史缺陷：两个按钮一直可点，
        # 跑起来再点「启动」只得到一条 already running 的 warn；没在跑时点
        # 「停止」supervisor 直接 return False，一条日志都没有。
        _btn_key = disabled_key
        _btn_run = ProjectRun(_btn_key, {"display_name": _btn_key})
        _btn_run.status = "running"
        window.supervisor.runs[_btn_key] = _btn_run
        window._poll()
        pump(app, 80)
        _bcard = window.cards[_btn_key]
        check("项目跑起来后「启动」置灰", not _bcard.start_btn.isEnabled())
        check("项目跑起来后「停止」可点", _bcard.stop_btn.isEnabled())
        for _status in ("pending", "hang"):
            _btn_run.status = _status
            window._poll()
            pump(app, 60)
            check("状态 {0} 时仍算在跑（启动置灰/停止可点）".format(_status),
                  not _bcard.start_btn.isEnabled() and _bcard.stop_btn.isEnabled())
        _btn_run.status = "success"
        window._poll()
        pump(app, 80)
        check("项目跑完后「启动」恢复可点", _bcard.start_btn.isEnabled())
        check("项目跑完后「停止」置灰", not _bcard.stop_btn.isEnabled())
        window.supervisor.runs.pop(_btn_key, None)
        window._poll()
        pump(app, 60)
        check("没有 run 时「停止」置灰", not window.cards[_btn_key].stop_btn.isEnabled())

        # 已经跑起来之后才被禁用：卡片写「已禁用」，但「停止」必须还能点，
        # 否则用户只能去点「全部停止」把别的项目也一起停掉。
        window.registry_edit.setPlainText(
            yaml.safe_dump(disabled_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 300)
        _live_run = ProjectRun(disabled_key, {"display_name": disabled_key})
        _live_run.status = "running"
        window.supervisor.runs[disabled_key] = _live_run
        window._poll()
        pump(app, 80)
        _lcard = window.cards[disabled_key]
        # 还在跑的时候按 run 渲染（写「运行中」），跑完才回落成「已禁用」——
        # 否则用户眼看着进程还在跑，卡片却写「已禁用」，会以为它已经停了。
        check("运行中才被禁用时卡片仍显示真实状态（{0}）".format(_lcard.status_label.text()),
              _lcard.status_label.text() == gm.STATUS_TEXT["running"])
        check("运行中才被禁用时「停止」仍可点", _lcard.stop_btn.isEnabled())
        check("运行中才被禁用时「启动」仍不可点", not _lcard.start_btn.isEnabled())
        _live_run.status = "failed"
        window._poll()
        pump(app, 80)
        check("跑完之后卡片回落成「已禁用」（{0}）".format(_lcard.status_label.text()),
              _lcard.status_label.text() == "已禁用")
        window.supervisor.runs.pop(disabled_key, None)
        window.registry_edit.setPlainText(
            yaml.safe_dump(original_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 300)

        # 改已存在项目的显示名，卡片标题要跟着变。
        # 历史缺陷：_reload_registry 只补新增卡片、删多余卡片，已存在的卡片一直
        # 用构造时的 spec，副标题都改了名字，卡片标题还是旧的。
        rename_key = next(iter(original_registry["projects"]))
        renamed = yaml.safe_load(yaml.safe_dump(original_registry, allow_unicode=True))
        renamed["projects"][rename_key]["display_name"] = "冒烟改名项目（测试）"
        window.registry_edit.setPlainText(yaml.safe_dump(renamed, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check("改显示名后项目卡标题跟随", window.cards[rename_key].name_label.text() == "冒烟改名项目")
        window.registry_edit.setPlainText(yaml.safe_dump(original_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)

        # 长 display_name 不能把窗口最小宽度顶出去。历史缺陷：三处文本控件
        # （卡片标题 QLabel#cardName、头部副标题 QLabel#subtitle、导航项
        # QPushButton#navItem）的 minimumSizeHint 都等于整段文字宽度，注册表里
        # 写个长名字后分别爆到 1232 / 1409 / 1176px，窗口最小宽度实测 1927px
        # ——1920 的屏幕都放不下，窗口被 Qt 强行拉宽，右侧一整截跑到屏幕外。
        long_name = "超长项目名与显示名测试" * 6
        long_registry = yaml.safe_load(yaml.safe_dump(original_registry, allow_unicode=True))
        long_registry["projects"][rename_key]["display_name"] = long_name
        window.registry_edit.setPlainText(yaml.safe_dump(long_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        long_card = window.cards[rename_key]
        check("超长显示名不把窗口最小宽度顶出 1366", window.minimumSizeHint().width() <= 1366)
        check(
            "超长显示名在卡片上被省略号裁掉",
            long_card.name_label.display_text() != long_name,
        )
        check("超长显示名保留完整文本", long_card.name_label.text() == long_name)
        check("超长显示名把完整文本放进 tooltip", long_card.name_label.toolTip() == long_name)
        check(
            "超长显示名下副标题不撑宽头部",
            window._subtitle_label.minimumSizeHint().width() <= 200,
        )
        window.registry_edit.setPlainText(yaml.safe_dump(original_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check(
            "还原显示名后卡片标题回到原名",
            window.cards[rename_key].name_label.text() == str(original_registry["projects"][rename_key].get("display_name", rename_key)).split("（")[0],
        )

        # 改 gui.project_colors 后卡片色条与日志前缀要跟着变。
        # 历史缺陷：project_colors 只在 MainWindow.__init__ 读过一次，改完保存后
        # 卡片顶部色条、日志前缀颜色全是开窗时那份旧配色。
        color_key = rename_key
        color_before = window.cards[color_key].color
        probe_color = "#ff00aa"
        settings_text = window.settings_edit.toPlainText()
        mutated_settings = yaml.safe_load(settings_text) or {}
        mutated_settings.setdefault("gui", {})["project_colors"] = {color_key: probe_color}
        window.settings_edit.setPlainText(yaml.safe_dump(mutated_settings, allow_unicode=True, sort_keys=False))
        window._save_text(window.settings_edit, config.SETTINGS_YAML)
        pump(app, 400)
        check("改项目配色后卡片颜色跟随", window.cards[color_key].color == probe_color)
        check(
            "改项目配色后卡片色条跟随",
            probe_color in window.cards[color_key].accent.styleSheet(),
        )
        check("改项目配色后日志前缀用新颜色", window._project_color(color_key) == probe_color)
        # 还原配色，避免影响后面的断言
        mutated_settings["gui"]["project_colors"] = {}
        window.settings_edit.setPlainText(yaml.safe_dump(mutated_settings, allow_unicode=True, sort_keys=False))
        window._save_text(window.settings_edit, config.SETTINGS_YAML)
        pump(app, 300)
        check("还原配色后卡片颜色回到原来", window.cards[color_key].color == color_before)

        # 项目身份色必须跟着主题走。
        # 历史缺陷：PROJECT_COLORS 是写死的亮色板，换到浅色主题后卡片色条与日志
        # 前缀还是 #4fc3f7 / #ffd54f 这类亮色，白底上对比度只有 1.4–3.4。
        # 另外 settings.yaml 出厂就写着这 8 个默认色，不能当「用户自定义」盖掉主题板。
        from gui import themes as _th

        dark_color = window.cards[color_key].color
        window._apply_theme("light")
        pump(app, 200)
        light_color = window.cards[color_key].color
        check("浅色主题下卡片身份色换了（{0} → {1}）".format(dark_color, light_color),
              light_color != dark_color)
        check("浅色主题下卡片身份色与本主题色板一致",
              light_color == _th.project_color("light", color_key))
        check("浅色主题下日志前缀也换了",
              window._project_color(color_key) == _th.project_color("light", color_key))
        # 出厂默认色不该被当成「用户自定义」盖掉主题板（上面还原成 {} 后又测一遍
        # 显式写回默认值的情形）
        defaults = {k: _th.PROJECT_PALETTE[_th.DEFAULT_THEME][k]
                    for k in ("March7thAssistant", "BAAH")}
        window.settings.setdefault("gui", {})["project_colors"] = dict(defaults)
        window._reload_project_colors()
        pump(app, 200)
        check("写回出厂默认色不会盖掉浅色主题的色板",
              window.cards["March7thAssistant"].color == _th.project_color("light", "March7thAssistant"))
        # 真改过的颜色仍然优先
        window.settings["gui"]["project_colors"] = {color_key: "#ff00aa"}
        window._reload_project_colors()
        pump(app, 150)
        check("用户改过的颜色优先于主题色板",
              window.cards[color_key].color == "#ff00aa")
        window.settings["gui"]["project_colors"] = {}
        window._apply_theme("dark")
        pump(app, 200)
        check("切回深色主题后身份色跟着回来",
              window.cards[color_key].color == _th.project_color("dark", color_key))

        # 注册表里去掉某个项目的 gui 段后，卡片上的「界面」按钮要收起来。
        # 历史缺陷：gui_btn 只在 ProjectCard 构造时决定一次可见性，之后改注册表
        # 存盘，按钮还在，点下去会去拉起一个已经没有界面入口的进程。
        gui_key = next(k for k, spec in original_registry["projects"].items() if spec.get("gui"))
        check("有界面入口的项目卡片显示「界面」按钮", window.cards[gui_key].gui_btn.isVisible())
        no_gui = yaml.safe_load(yaml.safe_dump(original_registry, allow_unicode=True))
        no_gui["projects"][gui_key]["gui"] = None
        window.registry_edit.setPlainText(yaml.safe_dump(no_gui, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check("去掉 gui 段后卡片「界面」按钮收起", not window.cards[gui_key].gui_btn.isVisible())
        window.registry_edit.setPlainText(yaml.safe_dump(original_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check("还原 gui 段后「界面」按钮回来", window.cards[gui_key].gui_btn.isVisible())

        # 拖动把原本相邻的并行组拆开之后，界面不能再显示「并行中」。
        # 历史缺陷：parallel_groups() 只检查组员「还显示着」，不检查是否仍然相邻，
        # 于是拖散之后界面照样画着连线、按钮写着「并行中」，而 supervisor 的
        # merge_parallel_blocks 会因为「项目之间隔着别的项目」把整块作废——
        # 界面说同时跑，实际是顺序跑，用户完全看不出来。
        order_keys = window.visible_card_keys()
        if len(order_keys) >= 3:
            first, second, third = order_keys[0], order_keys[1], order_keys[2]
            window.toggle_parallel(first)
            window.toggle_parallel(second)
            pump(app, 120)
            check("点两张相邻卡的「并行」组成一组", window.parallel_groups() == [[first, second]])
            check("并行组写进 chain.parallel_blocks", [first, second] in (
                (config.settings().get("chain") or {}).get("parallel_blocks") or []
            ))
            # 把第三张卡拖到前两张之间，原本相邻的 first/second 就被拆开了
            window._apply_card_order([first, third, second] + order_keys[3:])
            pump(app, 120)
            check("拖动拆散并行组后界面不再认这一组", window.parallel_groups() == [])
            check("拆散后卡片不再显示「并行中」", not window.cards[first].is_parallel())
            check("拆散后连线层没有组", not (window.links._groups or []))
            window._prune_parallel_blocks()
            pump(app, 120)
            check("拆散后配置里的并行块被清掉", not (
                (config.settings().get("chain") or {}).get("parallel_blocks") or []
            ))
            window._apply_card_order(order_keys)
            pump(app, 120)

        # 多项目并行：A-B 已经一组，再点 C 然后点组边的 B，要并成 A-B-C。
        # 历史缺陷：toggle_parallel 把「已在组里 → 退出」分支放在最前面，点 B
        # 时先把 B 从 A-B 摘掉、组里只剩 A 于是整组作废——用户想让三张一起跑，
        # 结果一个并行都不剩，日志还写着「取消并行」。
        if len(order_keys) >= 4:
            first, second, third, fourth = order_keys[0], order_keys[1], order_keys[2], order_keys[3]
            window.toggle_parallel(first)
            window.toggle_parallel(second)
            pump(app, 120)
            check("两张组成一组（多项目并行起点）", window.parallel_groups() == [[first, second]])
            window.toggle_parallel(third)
            window.toggle_parallel(second)
            pump(app, 120)
            check("再加一张并成三张一组（{0}）".format(window.parallel_groups()),
                  window.parallel_groups() == [[first, second, third]])
            check("三张一组也写进配置", [first, second, third] in (
                (config.settings().get("chain") or {}).get("parallel_blocks") or []
            ))
            window.toggle_parallel(fourth)
            window.toggle_parallel(third)
            pump(app, 120)
            check("四张也能并成一组", window.parallel_groups() == [[first, second, third, fourth]])
            # 从中间摘掉一张：剩下三张不再相邻（first/second/fourth 中间隔着 third
            # 那个位置虽然空了，但 second 与 fourth 之间的卡片槽已按新顺序连续），
            # 关键是界面与配置必须一致——不能在配置里留一个 supervisor 会告警的块。
            window.toggle_parallel(third)
            pump(app, 120)
            check("点组内一张取消并行", window.parallel_groups() == [])
            check("取消后配置里也不留无效块", not (
                (config.settings().get("chain") or {}).get("parallel_blocks") or []
            ))
            window._parallel_blocks = []
            window._parallel_pending = None
            window._write_parallel_blocks()
            window._sync_parallel_state()
            pump(app, 120)

            # 互相重叠的并行块（A-B 与 B-C）：supervisor 遇到重叠会把后一块整块
            # 作废并告警「B 已经在另一个并行组里」，界面上却把三张卡都点亮成
            # 「并行中」。两边必须一致：只认先出现的那组，重叠的从配置里清掉。
            window._parallel_blocks = [[first, second], [second, third]]
            window._write_parallel_blocks()
            window._prune_parallel_blocks()
            pump(app, 120)
            check("重叠的并行块只保留先出现的一组（{0}）".format(window.parallel_groups()),
                  window.parallel_groups() == [[first, second]])
            check("重叠块被清掉后配置里只剩一组（{0}）".format(
                      (config.settings().get("chain") or {}).get("parallel_blocks")),
                  (config.settings().get("chain") or {}).get("parallel_blocks") == [[first, second]])
            check("不在有效组里的卡片不显示「并行中」",
                  not window.cards[third].is_parallel())
            window._parallel_blocks = []
            window._parallel_pending = None
            window._write_parallel_blocks()
            window._sync_parallel_state()
            pump(app, 120)

        # 从总览收起一张卡再从设置页放回来，卡片要回到原来的位置。
        # 历史缺陷：_reload_registry 末尾按「此刻的界面顺序」重排，而新补的卡片
        # 是 flow.addWidget 追加在尾部——用户只是点了 × 又点回来，运行顺序就被
        # 悄悄改了（BAAH 从第二位跑到了最后）。
        _pos_before = window.visible_card_keys()
        if len(_pos_before) >= 3:
            _victim = _pos_before[1]
            window.hide_card(_victim)
            pump(app, 200)
            check("收起一张卡后它从总览消失", _victim not in window.visible_card_keys())
            window.restore_card(_victim)
            pump(app, 200)
            check("放回来的卡片回到原来的位置（{0}）".format(window.visible_card_keys()),
                  window.visible_card_keys() == _pos_before)

        # 收起一张「正在运行」的卡片要给出警告：× 只会把它从总览隐藏，进程不会停。
        # 历史缺陷：日志一律写「已从总览移除 X（设置页可恢复）」，用户以为点了 ×
        # 等于停掉，实际上进程、窗口、占用的模拟器都还在。
        if len(_pos_before) >= 3:
            _running = _pos_before[2]
            _run = ProjectRun(_running, {"display_name": _running})
            _run.status = "running"
            window.supervisor.runs[_running] = _run
            window.hide_card(_running)
            pump(app, 200)
            _hide_lines = [ln for ln in window.log_view.toPlainText().splitlines() if _running in ln]
            check("收起正在运行的项目时警告「仍在运行」（{0}）".format(_hide_lines[-1:]),
                  any("仍在运行" in ln for ln in _hide_lines))
            window.restore_card(_running)
            window.supervisor.runs.pop(_running, None)
            pump(app, 200)

        # 注册表里删掉某个项目时，界面不能因为 QStackedWidget 移除页面而乱翻页。
        # 历史缺陷（嵌入页时代）：移除当前页会自己切到相邻下标，用户看到的是另一
        # 个项目。现在只有总览/设置两页，删项目后必须仍在原来那一页上。
        window.nav.navigate.emit("overview")
        pump(app, 300)
        removed_view = yaml.safe_load(yaml.safe_dump(original_registry, allow_unicode=True))
        removed_view["projects"].pop(gui_key, None)
        window.registry_edit.setPlainText(yaml.safe_dump(removed_view, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check("删掉项目后仍停在总览页", window.stack.currentWidget() is window.stack.widget(0))
        check("删掉项目后导航高亮仍是总览", window.nav.active_key() == "overview")
        window.registry_edit.setPlainText(yaml.safe_dump(original_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)

        # 删掉「当前正被日志过滤器选中的项目」后，日志区要跟着回到全部项目。
        # 历史缺陷：重建下拉框时 blockSignals 了，currentIndexChanged 不触发，
        # _rerender_log 没跑——下拉框显示「全部项目」，日志区却还是那个已经不存在
        # 的项目的过滤结果，其它项目的历史日志一行都不显示。
        window.event_buffer.append({"project": "core", "level": "info", "message": "SMOKE-FILTER-CORE"})
        window.event_buffer.append({"project": rename_key, "level": "info", "message": "SMOKE-FILTER-KEEP"})
        combo = window.filter_combo
        combo.setCurrentIndex(max(0, combo.findData(rename_key)))
        pump(app, 150)
        check("过滤器选中断项目时只剩它的日志", "SMOKE-FILTER-CORE" not in window.log_view.toPlainText())
        filter_registry = yaml.safe_load(yaml.safe_dump(original_registry, allow_unicode=True))
        filter_registry["projects"].pop(rename_key, None)
        window.registry_edit.setPlainText(yaml.safe_dump(filter_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)
        check("删掉被过滤的项目后下拉框回到全部项目", (combo.currentData() or "") == "")
        check(
            "删掉被过滤的项目后日志区恢复全部项目",
            "SMOKE-FILTER-CORE" in window.log_view.toPlainText(),
        )
        window.registry_edit.setPlainText(yaml.safe_dump(original_registry, allow_unicode=True, sort_keys=False))
        window._save_text(window.registry_edit, config.PROJECTS_YAML)
        pump(app, 400)

        # 日志自动滚动：只有「本来就在底部」才跟着滚。
        # 历史缺陷：每来一行都无条件 setValue(maximum)，用户往上翻着看历史
        # （或正在复制一段报错）时，链还在跑、日志还在刷，视图被不断拽回底部，
        # 那段历史根本读不了。
        log_scroll = window.log_view.verticalScrollBar()
        window.autoscroll_check.setChecked(True)
        window._clear_log()
        pump(app, 100)
        for i in range(200):
            window._append_log("core", "info", "SMOKE-AUTO-{0}".format(i), emit_event=False)
        pump(app, 150)
        check("日志追加到超出视口后停到底部", log_scroll.value() >= log_scroll.maximum() - 4)
        log_scroll.setValue(0)
        pump(app, 100)
        window._append_log("core", "info", "SMOKE-AUTO-HISTORY", emit_event=False)
        pump(app, 150)
        check("用户往上翻看历史时新日志不把视图拽回底部", log_scroll.value() == 0)
        check(
            "被拽住的日志仍然进了缓冲区",
            "SMOKE-AUTO-HISTORY" in window.log_view.toPlainText(),
        )
        log_scroll.setValue(log_scroll.maximum())
        pump(app, 100)
        window._append_log("core", "info", "SMOKE-AUTO-RESUME", emit_event=False)
        pump(app, 150)
        check("回到底部后重新开始跟随", log_scroll.value() >= log_scroll.maximum() - 4)
        window.autoscroll_check.setChecked(False)
        log_scroll.setValue(0)
        pump(app, 100)
        window._append_log("core", "info", "SMOKE-AUTO-OFF", emit_event=False)
        pump(app, 150)
        check("关掉自动滚动后不再被拉动", log_scroll.value() == 0)
        window.autoscroll_check.setChecked(True)
        window._clear_log()
        pump(app, 100)

        # 长任务按钮只管起线程、不禁用也不去重：连点「环境自检」三次就起三个
        # 线程同时跑一遍全量自检（更新按钮更糟——实测三个线程并发 clone 同一
        # 批 git 工作树）。现在运行期间按钮变灰，重复点击被忽略。
        import gui.main as gm

        doctor_calls = []

        def fake_checks():
            doctor_calls.append(1)
            time.sleep(0.6)
            return [{"status": "ok", "name": "stub", "detail": "stub"}]

        orig_checks = gm.doctor.run_checks
        gm.doctor.run_checks = fake_checks
        try:
            window.doctor_btn.setEnabled(True)
            window.doctor_btn.click()
            window.doctor_btn.click()
            window.doctor_btn.click()
            pump(app, 150)
            check("连点环境自检只跑一次", len(doctor_calls) == 1)
            check("自检运行期间按钮变灰", not window.doctor_btn.isEnabled())
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not window.doctor_btn.isEnabled():
                pump(app, 50)
            check("自检结束后按钮恢复可用", window.doctor_btn.isEnabled())
            check("自检结束后没有多跑一遍", len(doctor_calls) == 1)
        finally:
            gm.doctor.run_checks = orig_checks
            window.doctor_btn.setEnabled(True)

        # 保存「修复规则」后运行中的引擎必须看到新规则。
        # 历史缺陷：RepairEngine 只在构造时读一次 repair_rules.yaml，界面上加
        # 的规则永远不生效，用户只能重启程序；同理关掉 repair.enabled 也不生效。
        engine = window.supervisor.repair
        rules_before = len(engine.rules)
        mutated_rules = yaml.safe_load(window.repair_edit.toPlainText()) or {}
        mutated_rules.setdefault("rules", [])
        mutated_rules["rules"].append(
            {
                "id": "smoke-probe-rule",
                "description": "冒烟专用，永不匹配",
                "match": "NEVER_MATCH_SMOKE_PROBE",
                "actions": [{"type": "rerun"}],
            }
        )
        window.repair_edit.setPlainText(yaml.safe_dump(mutated_rules, allow_unicode=True, sort_keys=False))
        window._save_text(window.repair_edit, config.REPAIR_YAML)
        pump(app, 300)
        check("保存修复规则后引擎规则数 +1", len(engine.rules) == rules_before + 1)
        check(
            "保存修复规则后新规则进入引擎",
            any(str(r.get("id")) == "smoke-probe-rule" for r in engine.rules),
        )
        # 还原规则文件并让引擎重新读
        config.save_yaml(config.REPAIR_YAML, yaml.safe_load(Path(_REAL_REPAIR).read_text(encoding="utf-8")))
        engine.reload()
        check("还原修复规则后引擎规则数回到原来", len(engine.rules) == rules_before)

        # 保存「设置」后引擎手上的 repair 配置也要跟着换。
        settings_before = dict(engine.settings.get("repair") or {})
        mutated_settings = yaml.safe_load(window.settings_edit.toPlainText()) or {}
        mutated_settings.setdefault("repair", {})
        mutated_settings["repair"]["max_rounds"] = int(settings_before.get("max_rounds", 2)) + 5
        window.settings_edit.setPlainText(
            yaml.safe_dump(mutated_settings, allow_unicode=True, sort_keys=False)
        )
        window._save_text(window.settings_edit, config.SETTINGS_YAML)
        pump(app, 300)
        check(
            "保存设置后修复引擎同步 repair 配置",
            int((engine.settings.get("repair") or {}).get("max_rounds", 0))
            == int(settings_before.get("max_rounds", 2)) + 5,
        )
        config.save_yaml(config.SETTINGS_YAML, yaml.safe_load(Path(_REAL_SETTINGS).read_text(encoding="utf-8")))
        window._on_settings_changed()
        pump(app, 100)

        # 「今日状态」标签必须跟着标记文件走，即使 chain_status 没变。
        # 历史缺陷：_poll 只在 chain 状态变化时刷新标签，而标记也可能由 CLI 或
        # 另一个进程写入，本进程链路一直 idle，标签就永远停在「未完成」。
        from afk_core import completion as completion_mod

        completion_mod.clear()
        window.settings_page._refresh_completion_label()
        pump(app, 100)
        check("未完成时「今天还没完成」按钮不可点", not window.settings_page.clear_completion_btn.isEnabled())
        completion_mod.mark_chain_success(["March7thAssistant"])
        pump(app, 1200)
        check(
            "外部写入完成标记后标签自动变「已完成」",
            "已完成" in window.settings_page.completion_label.text(),
        )
        check("完成后恢复按钮可点", window.settings_page.clear_completion_btn.isEnabled())
        completion_mod.clear()
        pump(app, 1200)
        check(
            "外部清除标记后标签自动回到「未完成」",
            "未完成" in window.settings_page.completion_label.text(),
        )
        window.settings_page._refresh_completion_label()
        pump(app, 100)

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

        # 小组件位置必须仍然可见，存进去的坐标不能盲信。
        # 历史缺陷：拔掉副屏 / 改分辨率后，settings.yaml 里记的 5000,300 被原样
        # 恢复，小组件落到所有屏幕之外，用户看不到也拖不动，每次启动都重放。
        if widget is not None:
            from PySide6.QtGui import QGuiApplication

            def _widget_visible(w):
                rect = w.frameGeometry()
                for screen in QGuiApplication.screens():
                    overlap = screen.availableGeometry().intersected(rect)
                    if overlap.width() >= min(48, rect.width()) and overlap.height() >= min(48, rect.height()):
                        return True
                return False

            widget.restore_position([5000, 300])
            pump(app, 60)
            check("越界的小组件坐标不会把窗口丢到屏幕外", _widget_visible(widget))
            widget.restore_position([-9999, -9999])
            pump(app, 60)
            check("负坐标同样回落到屏内", _widget_visible(widget))
            widget.restore_position([0, 0])
            pump(app, 60)
            check("合法的左上角坐标被原样保留", widget.current_position() == [0, 0])
            widget.restore_position(None)
            pump(app, 60)
            check("没有记录位置时回落到默认可见位置", _widget_visible(widget))

        # _poll 全流程：快照/胶囊/导航高亮（不再有嵌入页 tick）
        window._poll()
        pump(app, 60)
        check("_poll 正常执行", True)

        # 链在跑时「一键挂机」必须置灰：历史缺陷是按钮照旧可点，点下去
        # run_chain() 只回一句 warn「chain already running」，界面上看起来
        # 像没反应，用户会连点。
        window.supervisor.chain_status = "idle"
        window._poll()
        pump(app, 60)
        check("空闲时「一键挂机」可点（{0}）".format(window.chain_btn.text()),
              window.chain_btn.isEnabled() and window.chain_btn.text() == "一键挂机")
        window.supervisor.chain_status = "running"
        window._poll()
        pump(app, 60)
        check("链在跑时「一键挂机」置灰且换文案（{0}）".format(window.chain_btn.text()),
              not window.chain_btn.isEnabled() and window.chain_btn.text() == "挂机中…")
        check("置灰时提示指向「全部停止」",
              "全部停止" in window.chain_btn.toolTip())
        window.supervisor.chain_status = "idle"
        window._poll()
        pump(app, 60)
        check("链跑完后「一键挂机」恢复可点（{0}）".format(window.chain_btn.text()),
              window.chain_btn.isEnabled() and window.chain_btn.text() == "一键挂机")

        # 关窗口会 _quit() → supervisor.stop_all()，正在挂的项目被直接杀掉。
        # 历史缺陷：closeEvent 一句不问就退，minimize_to_tray 关掉之后用户以为
        # 只是关个界面，实际正在跑的项目全停（实测伪造 running 的 run，close()
        # 之后 alive 从 True 变 False）。
        _busy_key = window.visible_card_keys()[0]
        window.supervisor.runs.clear()
        _real_chain_running = window.supervisor.chain_running
        window.supervisor.chain_running = lambda: False
        check("开始前没有在跑的项目（{0}）".format(sorted(window.supervisor.runs)),
              window._busy_project_keys() == [])
        check("没有在跑的项目、链也闲着时不用问，直接放行",
              window._confirm_quit_with_busy([]) is True)
        _busy_run = ProjectRun(_busy_key, {"display_name": _busy_key})
        _busy_run.status = "running"
        window.supervisor.runs[_busy_key] = _busy_run
        check("伪造一个运行中的项目会被算成「还在跑」（{0}）".format(window._busy_project_keys()),
              window._busy_project_keys() == [_busy_key])
        _done_run = ProjectRun("SmokeDone", {"display_name": "SmokeDone"})
        _done_run.status = "success"
        window.supervisor.runs["SmokeDone"] = _done_run
        check("跑到终态的项目不算「还在跑」", "SmokeDone" not in window._busy_project_keys())
        window.supervisor.runs.pop("SmokeDone", None)
        _asked = []
        _real_confirm = window._confirm_quit_with_busy

        def _say_no(keys):
            _asked.append(list(keys))
            return False

        window._confirm_quit_with_busy = _say_no
        try:
            window.close()
            pump(app, 80)
            check("有项目在跑时关窗口会先把它们列出来（{0}）".format(_asked),
                  _asked == [[_busy_key]])
            check("选择「不退出」后窗口还在、状态轮询也没停",
                  window.isVisible() and window.timer.isActive())
            check("取消退出会在日志里留一行",
                  "退出已取消" in window.log_view.toPlainText())
            check("取消退出不会把运行中的项目停掉", _busy_run.status == "running")
        finally:
            window._confirm_quit_with_busy = _real_confirm
            window.supervisor.runs.pop(_busy_key, None)
            window.show()
            pump(app, 60)

        # 项目还没真正起来（正在更新/起模拟器）时 runs 是空的，但链在跑；
        # 这时候关窗口会把整条链静默中止，也要问一句。
        window.supervisor.runs.clear()
        _chain_asked = []

        def _chain_say_no(keys):
            _chain_asked.append(list(keys))
            return False

        window.supervisor.chain_running = lambda: True
        window._confirm_quit_with_busy = _chain_say_no
        try:
            window.close()
            pump(app, 80)
            check("链在跑但项目还没起来时，关窗口也会问一句（{0}）".format(_chain_asked),
                  _chain_asked == [[]])
            check("链在跑时取消退出，窗口还在", window.isVisible() and window.timer.isActive())
            check("链在跑时取消退出会写「挂机链仍在运行」",
                  "挂机链仍在运行" in window.log_view.toPlainText())
        finally:
            window._confirm_quit_with_busy = _real_confirm
            window.supervisor.chain_running = _real_chain_running
            window.show()
            pump(app, 60)

        # exit 后动作事件 → 调度退出
        bus_event = {"project": "core", "kind": "post_action", "level": "info", "message": "post action executed: exit", "data": {"action": "exit"}}
        window.bus.emit(bus_event)
        # QTimer.singleShot(800, self._quit) 的到期依赖事件循环真正跑到那一刻；
        # 固定 pump(1300) 在机器忙时（前面刚写过几次 settings.yaml）会差几十毫秒
        # 打完最后一行，断言偶发失败。这里轮询到定时器停下，上限 6 秒。
        _quit_deadline = time.monotonic() + 6.0
        while window.timer.isActive() and time.monotonic() < _quit_deadline:
            pump(app, 50)
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
        test_supervisor_extensions()
        test_main_window(app)
    finally:
        config.SETTINGS_YAML = _REAL_SETTINGS
        config.PROJECTS_YAML = _REAL_PROJECTS
        config.REPAIR_YAML = _REAL_REPAIR
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
