r"""注册表结构损坏后不能把程序/链线程搞死。

历史缺陷（2026-11-06 发现）：`afk_core/config.py:registry()` 只保证顶层是映射，
`afk_projects.yaml` 里的 `projects` / `groups` 是什么形状完全没管。手写错误
会一路传到运行时：

  * `projects: [a, b]` → `Supervisor.start_project()` 里
    `self._projects().get(key)` 抛 `AttributeError: 'list' object has no
    attribute 'get'` —— 用户点「启动」失败，界面只有一条线程 traceback；
  * `groups: hello` 或 `groups: [hello]` → `background_group_ids()` 抛
    `'str' object has no attribute 'get'`（空闲自动挂机在后台线程静默失败），
    而 `_chain_worker` 的 `[g.get("id") for g in groups]` 直接让链线程死掉，
    界面永远停在「运行中」；
  * `projects: {A: hello}`（规格不是映射）→ `config.project_dir()` 里
    `spec.get()` 抛 AttributeError。

判据：
  1. 任何畸形注册表下 `registry()` 都返回 `projects` 是 dict、`groups` 是 list；
  2. `start_project` / `background_group_ids` / `start_chain` 都不抛异常；
  3. 好的条目要保留（不能一坏全丢）；
  4. `members: A`（单个成员写成字符串）能被救成 `["A"]`；
  5. 结构告警只报一次（registry() 每次刷新都会重读，逐次告警会刷满日志）。
"""

import logging
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILS = []

SPEC = """  {name}:
    display_name: {name}
    enabled: true
    launch_type: python
    path: .
    python: .venv\\Scripts\\python.exe
    args: [tests\\smoke_project.py]
    retry: 0
"""


def check(name, ok, detail=""):
    if ok:
        print("ok   {0}".format(name))
    else:
        print("FAIL {0}  <- {1}".format(name, detail))
        FAILS.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="afk_registry_"))
    try:
        return _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _write(text):
    from afk_core import config

    config.PROJECTS_YAML.write_text(text, encoding="utf-8")


def _probe(label):
    """构造 Supervisor 并依次调所有会碰注册表的入口，返回出错清单。"""
    from afk_core.events import EventBus
    from afk_core.supervisor import Supervisor

    bus = EventBus()
    sup = Supervisor(bus)
    errors = []
    for name, fn in (
        ("_projects", lambda: sup._projects()),
        ("_groups", lambda: sup._groups()),
        ("background_group_ids", lambda: sup.background_group_ids()),
        ("status_snapshot", lambda: sup.status_snapshot()),
        ("start_project", lambda: sup.start_project("NoSuchProject")),
        ("start_chain", lambda: sup.start_chain()),
    ):
        try:
            fn()
        except Exception as exc:
            errors.append("{0}: {1}: {2}".format(name, type(exc).__name__, exc))
    try:
        bus.close_handles()
    except Exception:
        pass
    return errors


def _plan(registry):
    """用当前注册表排一次链的计划（只算步骤，不启动任何进程）。"""
    from afk_core.events import EventBus
    from afk_core.supervisor import Supervisor

    bus = EventBus()
    try:
        sup = Supervisor(bus)
        sup.registry = registry
        order = ((sup.settings.get("chain") or {}).get("order")) or []
        return sup._chain_plan(registry.get("groups") or [], order or None)
    finally:
        try:
            bus.close_handles()
        except Exception:
            pass


def _run(tmp):
    from afk_core import config

    shutil.copy(config.ROOT / "configs" / "core" / "settings.yaml", tmp / "settings.yaml")
    shutil.copy(config.ROOT / "configs" / "core" / "repair_rules.yaml", tmp / "repair_rules.yaml")
    config.SETTINGS_YAML = tmp / "settings.yaml"
    config.PROJECTS_YAML = tmp / "afk_projects.yaml"
    config.REPAIR_YAML = tmp / "repair_rules.yaml"
    config.LOGS_DIR = tmp / "logs"
    config.PROJECT_LOGS = config.LOGS_DIR / "projects"
    config.REPORTS_DIR = tmp / "reports"
    config.EVENTS_FILE = config.LOGS_DIR / "events.jsonl"
    config.STATE_FILE = config.LOGS_DIR / "state.json"
    config.DURATIONS_FILE = config.LOGS_DIR / "durations.jsonl"
    config.ERROR_INDEX = config.LOGS_DIR / "errors.jsonl"
    for d in (config.LOGS_DIR, config.PROJECT_LOGS, config.REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    cases = [
        ("projects 是列表", "projects:\n  - a\n  - b\ngroups: []\n"),
        ("projects 是字符串", "projects: hello\ngroups: []\n"),
        ("projects 里规格是字符串", "projects:\n  A: hello\ngroups: []\n"),
        ("groups 是字符串", "projects: {}\ngroups: hello\n"),
        ("groups 里条目是字符串", "projects: {}\ngroups:\n  - hello\n"),
        ("members 是字符串", "projects:\n" + SPEC.format(name="A") + "groups:\n  - id: g1\n    members: A\n"),
        ("整份空文件", ""),
    ]
    for label, text in cases:
        _write(text)
        reg = config.registry()
        check("「{0}」时 projects 是映射".format(label), isinstance(reg.get("projects"), dict),
              type(reg.get("projects")).__name__)
        check("「{0}」时 groups 是列表".format(label), isinstance(reg.get("groups"), list),
              type(reg.get("groups")).__name__)
        errors = _probe(label)
        check("「{0}」时所有入口不抛异常".format(label), not errors, "; ".join(errors))

    # 好条目不能被坏条目连累
    _write(
        "projects:\n"
        + SPEC.format(name="Good")
        + "  Bad: hello\n"
        + "groups:\n  - id: g1\n    members: [Good]\n  - hello\n"
    )
    reg = config.registry()
    check("混合形态：好项目保留", "Good" in (reg.get("projects") or {}), list((reg.get("projects") or {})))
    check("混合形态：坏项目丢掉", "Bad" not in (reg.get("projects") or {}), list((reg.get("projects") or {})))
    check("混合形态：好分组保留", len(reg.get("groups") or []) == 1, reg.get("groups"))
    check("混合形态：坏分组丢掉", all(isinstance(g, dict) for g in reg.get("groups") or []),
          reg.get("groups"))

    # members 写成字符串要被救成列表（否则链里遍历会逐字符）
    _write("projects:\n" + SPEC.format(name="A") + "groups:\n  - id: g1\n    members: A\n")
    groups = config.registry().get("groups") or []
    members = groups[0].get("members") if groups else None
    check("members 写成字符串时救成列表", members == ["A"], repr(members))
    check("members 救回来之后链能正常起", not _probe("members"), _probe("members"))

    # -- base 继承：同项目多卡片 / 多配置文件的支点 ----------------------
    _write(
        "projects:\n"
        + SPEC.format(name="Base")
        + "  Inst:\n"
        "    base: Base\n"
        "    config_name: 日服.json\n"
    )
    projects = config.registry().get("projects") or {}
    inst = projects.get("Inst") or {}
    check("base 继承：子项拿到基项目的字段", inst.get("path") == ".", repr(inst.get("path")))
    check("base 继承：子项自己的字段覆盖基项目",
          inst.get("config_name") == "日服.json", repr(inst.get("config_name")))
    check("base 继承：展开后保留 base 键", inst.get("base") == "Base", repr(inst.get("base")))
    check("base 继承：没写 display_name 时用「基项目名（实例名）」",
          inst.get("display_name") == "Base（Inst）", repr(inst.get("display_name")))
    check("base 继承：基项目本身不受影响",
          (projects.get("Base") or {}).get("display_name") == "Base")
    check("base 展开后 supervisor 各入口不抛", not _probe("base"), _probe("base"))

    # 深合并：映射递归合并，列表整体替换（否则「实例想清空 links」永远清不掉）
    _write(
        "projects:\n"
        "  B:\n"
        "    display_name: B\n"
        "    path: .\n"
        "    args: [a]\n"
        "    env:\n"
        "      K: 1\n"
        "      J: 2\n"
        "  C:\n"
        "    base: B\n"
        "    args: [b, c]\n"
        "    env:\n"
        "      J: 9\n"
    )
    projects = config.registry().get("projects") or {}
    c = projects.get("C") or {}
    check("base 继承：列表整体替换（不做元素合并）", c.get("args") == ["b", "c"], repr(c.get("args")))
    check("base 继承：映射递归合并", c.get("env") == {"K": 1, "J": 9}, repr(c.get("env")))

    # 显式写了 display_name 就用它，不再自动拼接
    _write(
        "projects:\n"
        + SPEC.format(name="Base2")
        + "  Named:\n"
        "    base: Base2\n"
        "    display_name: 我的日服\n"
    )
    named = (config.registry().get("projects") or {}).get("Named") or {}
    check("base 继承：显式 display_name 优先", named.get("display_name") == "我的日服",
          repr(named.get("display_name")))

    # base 指向不存在的项目：按独立项目处理，不能抛
    _write("projects:\n  D:\n    display_name: D\n    path: .\n    base: NoSuch\n")
    projects = config.registry().get("projects") or {}
    check("base 指向不存在的项目时保留自身", (projects.get("D") or {}).get("display_name") == "D",
          repr(projects.get("D")))
    check("base 指向不存在的项目不抛", not _probe("missing-base"), _probe("missing-base"))

    # 循环引用（A→B→A）必须挡住，否则是无限递归
    _write(
        "projects:\n"
        "  P:\n    display_name: P\n    path: .\n    base: Q\n"
        "  Q:\n    display_name: Q\n    path: .\n    base: P\n"
    )
    projects = config.registry().get("projects") or {}
    check("base 循环引用时两个项目都保留", sorted(projects) == ["P", "Q"], sorted(projects))
    check("base 循环引用不无限递归", not _probe("cycle"), _probe("cycle"))

    # 指向自己：等价于没写 base
    _write("projects:\n  S:\n    display_name: S\n    path: .\n    base: S\n")
    self_base = (config.registry().get("projects") or {}).get("S") or {}
    check("base 指向自己时按独立项目处理", self_base.get("display_name") == "S", repr(self_base))

    # base 告警只报一次（registry() 每次刷新都会重读）
    records = []

    class _GrabBase(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("afk.config")
    handler = _GrabBase()
    logger.addHandler(handler)
    old_level = logger.level
    old_prop = logger.propagate
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    try:
        # 文件里要真的有一个「缺失 base」的项目，否则这轮什么都不会告警
        # （上一段写的是 base 指向自己，属于正常配置）。
        _write("projects:\n  M:\n    display_name: M\n    path: .\n    base: NoSuch\n")
        config._WARNED_SHAPE.clear()
        for _ in range(5):
            config.registry()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_prop
    missing = [m for m in records if "base 指向不存在的项目" in m]
    check("base 缺失告警只报一次", len(missing) == 1, "{0} 条: {1}".format(len(missing), records))

    # -- groups 里引用已删除项目：不能排进链，也不能刷成「项目跑失败」 --------
    _write(
        "projects:\n"
        + SPEC.format(name="A")
        + SPEC.format(name="B")
        + "groups:\n"
        "  - id: g1\n    members: [A, Ghost]\n    parallel: true\n"
        "  - id: g2\n    members: [B]\n    parallel: false\n"
    )
    reg = config.registry()
    groups = reg.get("groups") or []
    members = [g.get("members") for g in groups]
    check("groups 里引用已删除项目的成员被剔除", members == [["A"], ["B"]], repr(members))
    check("剔除幽灵成员后链计划里没有它",
          not any("Ghost" in s.get("members", []) for s in _plan(reg)), _plan(reg))
    check("剔除幽灵成员后并行块照常生效（不再被整块作废）",
          all(len(s.get("members", [])) == 1 for s in _plan(reg)), _plan(reg))

    records = []

    class _GrabGhost(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("afk.config")
    handler = _GrabGhost()
    logger.addHandler(handler)
    old_level = logger.level
    old_prop = logger.propagate
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    try:
        config._WARNED_SHAPE.clear()
        for _ in range(5):
            # 读盘缓存会让第 2 次起根本不解析，测不出「告警去重」。每次都
            # 主动失效，逼它真的重新解析五遍。
            config.invalidate_cache()
            config.registry()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_prop
    ghosts = [m for m in records if "不在 projects 里" in m]
    check("幽灵成员告警只报一次", len(ghosts) == 1, "{0} 条: {1}".format(len(ghosts), records))

    # 结构告警只报一次
    _write("projects: hi\ngroups: nope\n")
    records = []

    class _Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("afk.config")
    handler = _Grab()
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.WARNING)
    old_prop = logger.propagate
    logger.propagate = False
    try:
        config._WARNED_SHAPE.clear()
        for _ in range(5):
            config.invalidate_cache()
            config.registry()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_prop
    # 注意：过滤词不能只写 "projects"——两条告警的文案里都带 "afk_projects.yaml"，
    # 会把自己的 groups 告警也算进来（实测 2 条而不是 1 条）。
    repeated = [m for m in records if "的 projects 不是映射" in m]
    check("结构告警只报一次（不刷屏）", len(repeated) == 1, "{0} 条".format(len(repeated)))

    print()
    if FAILS:
        print("FAILED {0}: {1}".format(len(FAILS), "; ".join(FAILS)))
        print("REGISTRY SHAPE FAIL")
        return 1
    print("REGISTRY SHAPE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
