r"""项目状态页：版本行、扫描队列、状态与参数格式化的回归测试。

历史缺陷（2026-11-22 发现，均已修并在此守）：
  1. `_ProjectBlock.__init__` 只在 spec 里有 `version` 键时才建版本行，于是
     「设置 → 新增挂机项目」建出来（不填 version）的项目，后台 `read_version`
     扫到了版本也没有标签可写 —— 界面上永远看不到版本号；
  2. `_start_version_scan` 只要上一轮扫描线程还活着就直接 return，用户在
     「项目注册表」里存盘/新增项目会立刻 reload，新卡永远停在「未知」；
  3. `set_version("")` 会把用户手写的 version 抹成「未知」，而子项目不是
     git clone（发行包）时扫出来的就是空串。

判据：
  1. 每个项目都有一条「版本」行，无论注册表里写没写 version；
  2. `set_version("")` 不动手写版本，只在本来空/未知时才写「未知」；
  3. 扫描线程按队列消费，扫完自己退出且 `_scan_pending` 清空，之后还能重启；
  4. reload 期间的追加目标不会丢（A 在扫时新增 B，B 也要有版本）；
  5. `set_state` 文案、`_format` 对 bool/list/tuple/dict/str 的输出正确；
  6. spec 为 None / 空映射 / 缺 path 时不抛。
"""

import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, detail=""):
    if ok:
        print("ok   {0}".format(name))
    else:
        print("FAIL {0}  <- {1}".format(name, detail))
        FAILS.append(name)


def pump(app, ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _wait_scan(page, app, timeout=10.0):
    """等扫描线程把队列消费完并自行退出。

    注意：线程一退出不代表 GUI 线程已经收到 version_ready。信号是跨线程排队
    投递的，必须再转几轮事件循环，否则断言「被扫过」时会看不到最后几个键
    （单跑能过、跟别的用例连着跑就偶发失败，就是这个时序）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump(app, 30)
        thread = page._version_thread
        if thread is not None and not thread.is_alive():
            pump(app, 200)
            return True
    return False


def _wait_until(app, pred, timeout=6.0):
    """轮询到 pred() 为真。跨线程信号是排队投递的，不能只等线程退出。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        pump(app, 30)
    return pred()


def _registry_for(projects):
    return lambda: {"projects": projects}


def _project_dir(tmp, name, is_git=False):
    path = tmp / name
    path.mkdir(parents=True, exist_ok=True)
    if is_git:
        (path / ".git").mkdir(exist_ok=True)
    return path


def main():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

    tmp = Path(tempfile.mkdtemp(prefix="afk_projects_page_"))
    try:
        return _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp):
    from afk_core import config

    # 配置全部指向临时副本：这一页只读注册表，但 reload 会读 config.ROOT，
    # 顺手把日志/报告目录也挪走，避免任何写入落进仓库。
    shutil.copy(config.ROOT / "configs" / "core" / "settings.yaml", tmp / "settings.yaml")
    shutil.copy(config.ROOT / "configs" / "core" / "afk_projects.yaml", tmp / "afk_projects.yaml")
    config.SETTINGS_YAML = tmp / "settings.yaml"
    config.PROJECTS_YAML = tmp / "afk_projects.yaml"
    config.LOGS_DIR = tmp / "logs"
    config.PROJECT_LOGS = config.LOGS_DIR / "projects"
    config.REPORTS_DIR = tmp / "reports"
    for d in (config.LOGS_DIR, config.PROJECT_LOGS, config.REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from gui import themes
    from gui.modern import apply_theme
    from gui.project_status_page import KEY_LABELS, ProjectStatusPage, _ProjectBlock

    apply_theme(app, themes.DEFAULT_THEME)

    # -- 1. _format 的四种形状 --------------------------------------------
    fmt = _ProjectBlock._format
    check("_format 布尔转是/否", fmt(True) == "是" and fmt(False) == "否")
    check("_format 列表用逗号连", fmt(["a", "b"]) == "a, b")
    check("_format 元组同列表", fmt(("x", 1)) == "x, 1")
    check("_format 字典用斜杠连", fmt({"k": 1, "j": 2}) == "k: 1 / j: 2")
    check("_format 数字转字串", fmt(3) == "3")
    check("_format 空列表是空串", fmt([]) == "")

    # -- 2. 版本行必须永远存在 --------------------------------------------
    plain = _ProjectBlock("NoVer", {"display_name": "没写版本", "launch_type": "python"}, "#4fc3f7")
    check("没写 version 键也有版本行", hasattr(plain, "_version_label"))
    check("没写 version 键的行初值是「未知」", plain._version_label.text() == "未知")
    withver = _ProjectBlock("WithVer", {"display_name": "写了版本", "version": "v1.2.3"}, "#4fc3f7")
    check("写了 version 键就显示它", withver._version_label.text() == "v1.2.3")

    # -- 3. set_version 的覆盖规则 ----------------------------------------
    withver.set_version("")
    check("扫不出东西时不抹掉手写版本", withver._version_label.text() == "v1.2.3")
    withver.set_version("v9.9.9-dirty")
    check("扫出版本时覆盖", withver._version_label.text() == "v9.9.9-dirty")
    withver.set_version("")
    check("已有扫描结果时再扫空也不抹", withver._version_label.text() == "v9.9.9-dirty")
    plain.set_version("")
    check("本来就是未知时空扫仍是未知", plain._version_label.text() == "未知")
    plain.set_version("abc1234")
    check("未知状态下扫到版本会填上", plain._version_label.text() == "abc1234")
    check("版本行 tooltip 跟随文本", plain._version_label.toolTip() == "abc1234")

    # -- 4. set_state 文案 -------------------------------------------------
    plain.set_state(True)
    check("启用状态文案", plain.state_label.text() == "已启用")
    plain.set_state(False)
    check("禁用状态文案", plain.state_label.text() == "已禁用")

    # -- 5. 页面级：reload 建块、扫描线程队列化、扫完退出 -------------------
    dir_a = _project_dir(tmp, "ProjA")
    dir_b = _project_dir(tmp, "ProjB")
    projects = {
        "A": {"display_name": "项目甲", "path": str(dir_a), "version": "v-a"},
        "B": {"display_name": "项目乙", "path": str(dir_b)},
    }
    page = ProjectStatusPage(
        registry_getter=_registry_for(projects),
        color_getter=lambda k: "#4fc3f7",
        log_fn=lambda *a, **k: None,
    )
    page.show()
    pump(app, 60)
    check("每个项目都建了块", sorted(page._blocks) == ["A", "B"])
    check("A 的手写版本保留", page._blocks["A"]._version_label.text() == "v-a")
    check("启动即排了扫描任务", _wait_scan(page, app))
    check("扫完后待扫队列清空", page._scan_pending == [])
    check("扫完线程已退出", page._version_thread is not None and not page._version_thread.is_alive())
    # 非 git 目录扫出来是空串，不能把手写版本抹掉
    check("非 git 目录不抹手写版本", page._blocks["A"]._version_label.text() == "v-a")
    check("非 git 目录的 B 显示未知", page._blocks["B"]._version_label.text() == "未知")

    # -- 6. 历史缺陷：线程活着时 reload 掉目标就永远扫不到 ----------------
    # 造一个「扫得很慢」的场景：让 read_version 卡住，期间追加 B 的 job。
    slow_started = []
    slow_release = []

    import gui.project_status_page as psp

    real_read_version = psp.read_version

    def slow_read_version(path):
        slow_started.append(path)
        # 第一个目标故意慢，保证 reload 发生在它还没跑完的时候
        if len(slow_started) == 1:
            deadline = time.time() + 5
            while time.time() < deadline and not slow_release:
                time.sleep(0.01)
        return real_read_version(path)

    try:
        psp.read_version = slow_read_version
        dir_c = _project_dir(tmp, "ProjC")
        projects2 = {"C": {"display_name": "项目丙", "path": str(dir_c)}}
        page2 = ProjectStatusPage(
            registry_getter=_registry_for(projects2),
            color_getter=lambda k: "#4fc3f7",
            log_fn=lambda *a, **k: None,
        )
        # 收集真正被扫过的键：队列修好之前，thread 活着时 reload 进来的 A2
        # 永远轮不到 read_version，这个列表里就不会出现 "A2"。
        seen = []
        page2.version_ready.connect(lambda k, v: seen.append(k))
        page2.show()
        pump(app, 30)
        deadline = time.time() + 5
        while time.time() < deadline and not slow_started:
            pump(app, 20)
        check("慢扫描已经开始", bool(slow_started))

        # 扫描线程还活着的时候 reload，追加一个新项目（这是历史缺陷的触发点）
        projects2["A2"] = {"display_name": "后来加的", "path": str(dir_a)}
        page2.reload()
        pump(app, 30)
        check("线程活着时 reload 目标进了队列", ("A2", str(dir_a)) in page2._scan_pending)
        slow_release.append(True)
        check("追加的目标最终被扫到", _wait_scan(page2, app))
        check("追加的目标有版本行", "A2" in page2._blocks)
        check("追加的目标真的跑过 read_version",
              _wait_until(app, lambda: "A2" in seen))
        check("先排的 C 也扫到了", _wait_until(app, lambda: "C" in seen))
    finally:
        psp.read_version = real_read_version

    # -- 7. 畸形 spec 不抛 ------------------------------------------------
    weird = {
        "NoneSpec": None,
        "EmptySpec": {},
    }
    page3 = ProjectStatusPage(
        registry_getter=_registry_for(weird),
        color_getter=lambda k: "#4fc3f7",
        log_fn=lambda *a, **k: None,
    )
    page3.show()
    pump(app, 60)
    check("spec 为 None/空映射也建块", sorted(page3._blocks) == ["EmptySpec", "NoneSpec"])
    check("畸形 spec 也有版本行", all(hasattr(b, "_version_label") for b in page3._blocks.values()))
    check("畸形 spec 不排扫描任务", page3._version_thread is None)
    check("畸形 spec 下 _scan_pending 为空", page3._scan_pending == [])

    # -- 8. reload 会清掉旧块 --------------------------------------------
    projects4 = {"X": {"display_name": "X", "path": str(dir_a)}}
    page4 = ProjectStatusPage(
        registry_getter=_registry_for(projects4),
        color_getter=lambda k: "#4fc3f7",
        log_fn=lambda *a, **k: None,
    )
    page4.show()
    pump(app, 40)
    check("初始只有 X", list(page4._blocks) == ["X"])
    projects4["Y"] = {"display_name": "Y", "path": str(dir_b)}
    page4.reload()
    pump(app, 40)
    check("reload 后跟着注册表增加", sorted(page4._blocks) == ["X", "Y"])
    del projects4["X"]
    page4.reload()
    pump(app, 40)
    check("reload 后跟着注册表减少", list(page4._blocks) == ["Y"])
    check("版本行标签常量存在", KEY_LABELS.get("version") == "版本")

    for widget in (page, page2, page3, page4):
        try:
            widget.close()
        except Exception:
            pass
    pump(app, 60)

    if FAILS:
        print("\nFAILS: {0}".format(", ".join(FAILS)))
        return 1
    print("\nPROJECTS-PAGE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
