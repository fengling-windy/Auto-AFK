r"""settings.yaml 里某一节被写成标量/列表时，程序必须还能打开。

历史缺陷（2026-11-06 发现）：`afk_core/config.py:load_yaml()` 只保证顶层是映射，
节本身的形状完全没管。在「设置」tab 里把手改的 `schedule: true` 保存下去，下次
启动就抛在 GUI 构造里，用户再也进不去界面、也没法把它改回来：

    gui/main.py:353        (self.settings.get("gui") or {}).get("project_colors")
        → AttributeError: 'bool' object has no attribute 'get'
    gui/main.py:555        schedule      同
    gui/main.py:549        pre_run       同
    gui/settings_page.py:142  post_actions 同
    gui/settings_page.py:168  idle        同
    gui/settings_page.py:297  chain       同
    afk_core/housekeeping.py:40  housekeeping 同（后台清理线程静默失败）

判据：
  1. 每个「应当是映射」的节，写成 bool / str / list / int 后 `settings()` 都必须
     返回映射，且原有的默认值能兜住（不能变成 None）；
  2. 畸形节下 GUI 主窗口能构建、能 show，设置页与头部控件都不抛；
  3. 后台入口（cleanup_logs / completed_today / idle_seconds / duration_stats）
     在畸形节下都不抛；
  4. 好的节不受影响（不能一坏全清空）；
  5. 告警只报一次（settings() 每次都重读文件，逐次告警会刷满日志）；
  6. 整份配置全写成畸形时程序仍能打开。
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

SECTIONS = (
    "event",
    "housekeeping",
    "configsync",
    "chain",
    "idle",
    "repair",
    "notifications",
    "post_actions",
    "doctor",
    "gui",
    "schedule",
    "pre_run",
)
SHAPES = (
    ("bool", True),
    ("str", "abc"),
    ("list", [1, 2]),
    ("int", 7),
)


def check(name, ok, detail=""):
    if ok:
        print("ok   {0}".format(name))
    else:
        print("FAIL {0}  <- {1}".format(name, detail))
        FAILS.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="afk_settings_shape_"))
    try:
        return _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _base_text():
    return _BASE_TEXT


def _write_yaml(text):
    from afk_core import config

    config.SETTINGS_YAML.write_text(text, encoding="utf-8")


def _mutate(section, value):
    """在基准配置上把某个节整体换成 value，写成 yaml 文本。"""
    import yaml

    data = yaml.safe_load(_BASE_TEXT)
    data[section] = value
    _write_yaml(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def _run(tmp):
    global _BASE_TEXT
    from afk_core import config, completion, housekeeping, idle, report

    shutil.copy(config.ROOT / "configs" / "core" / "settings.yaml", tmp / "settings.yaml")
    shutil.copy(config.ROOT / "configs" / "core" / "afk_projects.yaml", tmp / "afk_projects.yaml")
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
    _BASE_TEXT = config.SETTINGS_YAML.read_text(encoding="utf-8")

    # 1) 每一节 × 每种畸形类型：settings() 都要返回映射
    for section in SECTIONS:
        for shape_name, shape_value in SHAPES:
            _mutate(section, shape_value)
            config._WARNED_SETTINGS.clear()
            try:
                settings = config.settings()
            except Exception as exc:
                check("{0}={1} 时 settings() 不抛".format(section, shape_name), False,
                      "{0}: {1}".format(type(exc).__name__, exc))
                continue
            got = settings.get(section)
            check("{0}={1} 时 settings()[{0}] 回落成映射".format(section, shape_name),
                  isinstance(got, dict), repr(got))

    # 2) 好节不受影响：只把 gui 写坏，其它节必须原样保留
    import yaml

    baseline = yaml.safe_load(_BASE_TEXT)
    _mutate("gui", "boom")
    config._WARNED_SETTINGS.clear()
    current = config.settings()
    changed = [k for k in baseline if k != "gui" and current.get(k) != baseline.get(k)]
    check("只写坏 gui 时其它节原样保留", not changed, "被改动的节: {0}".format(changed))
    check("写坏的那节按空映射处理", current.get("gui") == {}, repr(current.get("gui")))

    # 3) 后台入口在畸形节下都不抛
    _mutate("housekeeping", True)
    config._WARNED_SETTINGS.clear()
    for label, fn in (
        ("housekeeping.cleanup_logs", lambda: housekeeping.cleanup_logs(None)),
        ("completion.completed_today", lambda: completion.completed_today()),
        ("idle.idle_seconds", lambda: idle.idle_seconds()),
        ("report.duration_stats", lambda: report.duration_stats()),
    ):
        try:
            fn()
            ok, detail = True, ""
        except Exception as exc:
            ok, detail = False, "{0}: {1}".format(type(exc).__name__, exc)
        check("housekeeping 写坏时 {0} 不抛".format(label), ok, detail)

    # 4) 整份配置的所有节都写成畸形值，GUI 仍要能打开
    garbage = yaml.safe_load(_BASE_TEXT)
    for section in SECTIONS:
        garbage[section] = True
    _write_yaml(yaml.safe_dump(garbage, allow_unicode=True, sort_keys=False))
    config._WARNED_SETTINGS.clear()
    window_errors = _probe_window()
    check("所有节都写坏时 GUI 仍能打开", not window_errors, "; ".join(window_errors))

    # 5) 告警只报一次
    _mutate("schedule", True)
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
        config._WARNED_SETTINGS.clear()
        for _ in range(5):
            # 读盘缓存会让第 2 次起不解析，测不出「告警去重」，每次主动失效。
            config.invalidate_cache()
            config.settings()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_prop
    # 过滤词要足够具体：两条告警文案里都带 "settings.yaml"
    repeated = [m for m in records if "的 schedule 不是映射" in m]
    check("结构告警只报一次（不刷屏）", len(repeated) == 1, "{0} 条".format(len(repeated)))

    _write_yaml(_BASE_TEXT)
    print()
    if FAILS:
        print("FAILED {0}: {1}".format(len(FAILS), "; ".join(FAILS)))
        print("SETTINGS SHAPE FAIL")
        return 1
    print("SETTINGS SHAPE PASS")
    return 0


def _probe_window():
    """离屏构建 MainWindow，把所有会读设置节的入口都走一遍。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    for name in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))

    from gui.main import MainWindow
    from gui.modern import APP_QSS, apply_palette

    apply_palette(app)
    app.setStyleSheet(APP_QSS)
    errors = []
    window = None
    try:
        window = MainWindow()
    except Exception as exc:
        errors.append("MainWindow: {0}: {1}".format(type(exc).__name__, exc))
    if window is None:
        return errors
    try:
        window.show()
        for _ in range(120):
            app.processEvents()
        for label, fn in (
            ("show", lambda: None),
            ("_sync_header_settings", window._sync_header_settings),
            ("settings_page.reload_from_disk", window.settings_page.reload_from_disk),
            ("_sync_cards_height", window._sync_cards_height),
            ("_subtitle_text", window._subtitle_text),
            ("_is_done_today", window._is_done_today),
            ("_toggle_idle_enabled", lambda: window._toggle_idle_enabled(True)),
            ("background_group_ids", window.supervisor.background_group_ids),
        ):
            try:
                fn()
            except Exception as exc:
                errors.append("{0}: {1}: {2}".format(label, type(exc).__name__, exc))
    finally:
        try:
            window.timer.stop()
        except Exception:
            pass
        window.close()
        for _ in range(60):
            app.processEvents()
    return errors


if __name__ == "__main__":
    sys.exit(main())
