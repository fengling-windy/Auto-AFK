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
