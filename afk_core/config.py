import logging
import os
from pathlib import Path

import yaml


def root_dir():
    env = os.environ.get("AFK_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1]


ROOT = root_dir()
CORE_CONFIG_DIR = ROOT / "configs" / "core"
LOGS_DIR = ROOT / "logs"
PROJECT_LOGS = LOGS_DIR / "projects"
REPORTS_DIR = LOGS_DIR / "reports"
STATE_FILE = LOGS_DIR / "state.json"
EVENTS_FILE = LOGS_DIR / "events.jsonl"
ERROR_INDEX = LOGS_DIR / "errors.jsonl"
DURATIONS_FILE = LOGS_DIR / "durations.jsonl"
MONITOR_KIT = ROOT / "monitor_kit"
PROJECTS_YAML = CORE_CONFIG_DIR / "afk_projects.yaml"
SETTINGS_YAML = CORE_CONFIG_DIR / "settings.yaml"
REPAIR_YAML = CORE_CONFIG_DIR / "repair_rules.yaml"


def load_yaml(path, default=None):
    fallback = default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            return fallback
        # 配置文件都是映射（section: value）。有人手工把整份文件改成标量或列表
        # （比如误删了所有缩进，或编辑器自动补了个 "- "），yaml 仍能解析成功，
        # 但之后每一处 settings().get(...) 都会 AttributeError，程序整体不可用。
        # 这里兜回默认值并留一条 warning，让程序至少还能打开并改回来。
        if not isinstance(data, dict):
            logging.getLogger("afk.config").warning(
                "%s 的顶层不是映射（读到 %s），已改用默认配置", path, type(data).__name__
            )
            return fallback
        return data
    except FileNotFoundError:
        return fallback
    except Exception as exc:
        logging.getLogger("afk.config").warning("failed to parse %s: %s (falling back to defaults)", path, exc)
        return fallback


def save_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)


def settings():
    return load_yaml(SETTINGS_YAML, {})


# 已经就「gui 不是映射」告警过的项目（registry() 每次刷新都会重读，逐次告警会
# 把日志刷满——实测开一次窗口就重复 8 遍）。
_WARNED_GUI = set()


def _normalize_gui(spec, key=""):
    """把项目规格里的 ``gui`` 归一成「映射」或 None。

    历史缺陷：注册表里手写 ``gui: true``（最自然的一句「这个项目有界面」）会让
    整个程序打不开——gui/project_page.py:60 与 supervisor._build_gui_command 都
    直接对它调 ``.get()``，于是 MainWindow 构造时抛
    ``AttributeError: 'bool' object has no attribute 'get'``，用户连界面都进不去，
    也没法把配置改回来（只能手工编辑 yaml）。这里在唯一读取入口归一：真值但
    不是映射的按「有界面但没写具体入口」处理（None），并留一条 warning 说明
    要写成映射；假值同样归一成 None。
    """
    gui = spec.get("gui")
    if gui is None or gui is False:
        return None
    if isinstance(gui, dict):
        return gui
    if key not in _WARNED_GUI:
        _WARNED_GUI.add(key)
        logging.getLogger("afk.config").warning(
            "项目 %s 的 gui 不是映射（读到 %r），已按「没有界面入口」处理；"
            "要嵌入界面请写成 gui: {args: [...]} 或 gui: {exe: ...}",
            key or spec.get("display_name") or "?",
            gui,
        )
    return None


def registry():
    data = load_yaml(PROJECTS_YAML, {})
    data.setdefault("projects", {})
    data.setdefault("groups", [])
    data.setdefault("reserved", [])
    projects = data.get("projects")
    if isinstance(projects, dict):
        for key, spec in projects.items():
            if isinstance(spec, dict):
                spec["gui"] = _normalize_gui(spec, key)
    return data


def repair_rules():
    return load_yaml(REPAIR_YAML, {"rules": []})


def project_dir(spec):
    path = Path(spec.get("path", ""))
    if not path.is_absolute():
        path = ROOT / path
    return path


def config_dir_for(key, spec):
    raw = spec.get("config_dir")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        return path
    return ROOT / "configs" / key
