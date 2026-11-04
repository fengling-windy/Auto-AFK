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


def registry():
    data = load_yaml(PROJECTS_YAML, {})
    data.setdefault("projects", {})
    data.setdefault("groups", [])
    data.setdefault("reserved", [])
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
