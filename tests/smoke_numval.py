"""配置里的数值写成坏字符串时，必须告警回退而不是抛异常。

为什么单独立一条冒烟：``afk_core/numval.py`` 存在的理由就是「坏值不能让界面
打不开、不能让后台监控线程默默死掉」，而它被 config / supervisor / emulator /
housekeeping / notify / repair / updater / 设置页共同依赖，任何一个调用点退回
到裸 ``int()`` 都会让这条保证消失。

判据：
1. ``as_float`` / ``as_int`` 对 8 种坏值都返回默认值并写 warning。
2. ``None`` 当作「没写」处理，静默回退、不告警。
3. low/high 夹取生效；"30.0" 这类能转的字符串正常转。
4. 真的把坏值写进 settings.yaml 后，SettingsPage 与 MainWindow 仍能构造。
"""

import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(r"E:\GameAFK\Auto-AFK")
sys.path.insert(0, str(ROOT))

from afk_core import config, numval

FAILED = []


def check(name, ok):
    if ok:
        print("ok   {0}".format(name))
    else:
        print("FAIL {0}".format(name))
        FAILED.append(name)


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capture():
    handler = _Collect()
    logger = logging.getLogger("afk.numval")
    logger.addHandler(handler)
    return handler


def test_conversions():
    handler = _capture()
    try:
        for bad in ("abc", "", "两次", "30 分钟", [], {}, [1], "12:30"):
            try:
                value = numval.as_float(bad, 7.5, what="demo.float")
                ok = value == 7.5
            except Exception:
                ok = False
            check("as_float 坏值 {0!r} 回退默认值".format(bad), ok)
        check("每个坏值都写了 warning（{0} 条）".format(len(handler.records)), len(handler.records) == 8)

        handler.records.clear()
        check("None 静默回退不告警", numval.as_float(None, 7.5, what="demo.float") == 7.5)
        check("None 不写 warning", handler.records == [])

        handler.records.clear()
        check("字符串数字能转", numval.as_int("42", 0, what="demo.int") == 42)
        check("小数形态的字符串能转 int", numval.as_int("30.0", 0) == 30)
        check("正常数字不写 warning", handler.records == [])

        check("低于下限被夹取", numval.as_int(-5, 3, low=1) == 1)
        check("高于上限被夹取", numval.as_int(999, 3, high=10) == 10)
        check("低值不被误判", numval.as_float("2.5", 0, low=1) == 2.5)
    finally:
        logging.getLogger("afk.numval").removeHandler(handler)
        handler.close()


def test_bad_settings_still_opens_gui():
    """把坏值写进 settings.yaml 后，设置页与主窗口仍然能起来。"""
    tmp = Path(tempfile.mkdtemp(prefix="afk_numval_"))
    for sub in ("logs", "logs/projects", "reports"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    saved = {
        name: getattr(config, name)
        for name in (
            "SETTINGS_YAML",
            "PROJECTS_YAML",
            "REPAIR_YAML",
            "LOGS_DIR",
            "PROJECT_LOGS",
            "REPORTS_DIR",
            "EVENTS_FILE",
            "STATE_FILE",
            "DURATIONS_FILE",
            "ERROR_INDEX",
        )
    }
    try:
        config.SETTINGS_YAML = tmp / "settings.yaml"
        config.PROJECTS_YAML = tmp / "afk_projects.yaml"
        config.REPAIR_YAML = tmp / "repair_rules.yaml"
        config.LOGS_DIR = tmp / "logs"
        config.PROJECT_LOGS = tmp / "logs" / "projects"
        config.REPORTS_DIR = tmp / "reports"
        config.EVENTS_FILE = tmp / "logs" / "events.jsonl"
        config.STATE_FILE = tmp / "state.json"
        config.DURATIONS_FILE = tmp / "logs" / "durations.jsonl"
        config.ERROR_INDEX = tmp / "logs" / "errors.jsonl"
        for name in ("settings.yaml", "afk_projects.yaml", "repair_rules.yaml"):
            (tmp / name).write_bytes((ROOT / "configs" / "core" / name).read_bytes())
        config.invalidate_cache()

        data = config.load_yaml(config.SETTINGS_YAML, {})
        data = dict(data)
        data["pre_run"] = dict(data.get("pre_run") or {})
        data["pre_run"]["at_startup"] = False
        data["housekeeping"] = {"enabled": False, "log_days": "两周", "report_days": None}
        gui_cfg = dict(data.get("gui") or {})
        gui_cfg["widget_enabled"] = False
        gui_cfg["window_size"] = []
        gui_cfg["background_opacity"] = "半透明"
        data["gui"] = gui_cfg
        data["post_actions"] = {"after_all": "none", "delay_sec": "abc"}
        data["idle"] = {"enabled": False, "minutes": "半小时", "prompt_sec": ""}
        data["chain"] = {"order": [], "max_attempts_per_project": "三次"}
        data["repair"] = {"enabled": True, "max_rounds": "两次", "retry_delay_sec": "五秒"}
        data["notifications"] = {"enabled": False, "dedupe_sec": "半分钟"}
        config.save_yaml(config.SETTINGS_YAML, data)
        config.invalidate_cache()

        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        from gui.settings_page import SettingsPage

        try:
            page = SettingsPage(log_fn=lambda *a, **k: None)
            page.reload_from_disk()
            page.hide()
            check("坏数值下设置页仍能构造", True)
            check("延迟回退到 60 秒", page.post_delay.value() == 60)
            check("空闲分钟回退到 30", page.idle_minutes.value() == 30)
            check("提示秒数回退到 30", page.idle_prompt.value() == 30)
            check("背景不透明度回退到 100", page.bg_opacity.value() == 100)
        except Exception:
            print(traceback.format_exc())
            check("坏数值下设置页仍能构造", False)

        import gui.main as gm

        try:
            window = gm.MainWindow()
            window.show()
            app.processEvents()
            window._navigate("settings")
            app.processEvents()
            window.close()
            check("坏数值下主窗口仍能打开", True)
        except Exception:
            print(traceback.format_exc())
            check("坏数值下主窗口仍能打开", False)

        from afk_core import housekeeping, repair

        try:
            housekeeping.cleanup_logs(None, config.settings())
            check("坏数值下后台清理不抛", True)
        except Exception as exc:
            print("cleanup_logs: {0}".format(exc))
            check("坏数值下后台清理不抛", False)

        try:
            engine = repair.RepairEngine(bus=None, settings=config.settings())
            engine.handle_failure("Demo", None, "some error text")
            check("坏数值下修复引擎不抛", True)
        except Exception as exc:
            print("repair: {0}".format(exc))
            check("坏数值下修复引擎不抛", False)
    finally:
        config.invalidate_cache()
        for name, value in saved.items():
            setattr(config, name, value)


def main():
    test_conversions()
    test_bad_settings_still_opens_gui()
    print()
    if FAILED:
        print("NUMVAL FAIL: {0}".format(", ".join(FAILED)))
        return 1
    print("NUMVAL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
