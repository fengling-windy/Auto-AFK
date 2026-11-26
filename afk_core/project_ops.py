"""项目生命周期操作：安装 / 重装 / 删除 / 配置发现 / 任务清单读写。

GUI 的「项目配置」页与「运行顺序」页只做展示，真正的磁盘与注册表改动都在这里，
这样可以脱离 Qt 单独测试。所有会碰用户配置的操作都遵守同一条底线：
**configs/<项目> 里的用户配置永远不被删除**（删除源码前先摘掉 junction）。
"""

import copy
import json
import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path

from . import config
from .proc import host_env

log = logging.getLogger("afk.project_ops")


# 支持安装的项目（顺序即下拉框顺序）。default_spec 与 configs/core/afk_projects.yaml
# 里的出厂条目一致，只在注册表里没有该项目时用来补一条。
SUPPORTED_PROJECTS = [
    {
        "key": "March7thAssistant",
        "display_name": "三月七小助手（崩坏：星穹铁道 / 云游戏）",
        "repo": "moesnow/March7thAssistant",
        "clone_url": "https://github.com/moesnow/March7thAssistant.git",
        "clone_args": ["--recurse-submodules"],
        "build_script": "setup/30_build_m7a.ps1",
        "default_spec": {
            "display_name": "三月七小助手（崩坏：星穹铁道 / 云游戏）",
            "repo": "moesnow/March7thAssistant",
            "enabled": True,
            "launch_type": "python",
            "path": "March7thAssistant",
            "python": ".venv\\Scripts\\python.exe",
            "args": ["main.py"],
            "gui": {"args": ["app.py"]},
            "retry": 1,
            "hang_timeout": 900,
            "require_admin": True,
            "require_finish": True,
            "min_runtime_sec": 120,
            "config_dir": "configs\\March7thAssistant",
            "links": [
                {"link": "config", "store": "config"},
                {"link": "settings", "store": "settings"},
            ],
            "sync_files": [{"path": "config.yaml", "store": "config.yaml"}],
            "success_patterns": ["停止运行"],
            "error_patterns": ["发生错误", "Traceback \\(most recent call last\\)"],
            "finish_patterns": ["停止运行"],
        },
    },
    {
        "key": "BAAH",
        "display_name": "BAAH（蔚蓝档案 / MuMu 模拟器 ADB）",
        "repo": "BlueArchiveArisHelper/BAAH",
        "clone_url": "https://github.com/BlueArchiveArisHelper/BAAH.git",
        "clone_args": [],
        "build_script": "setup/31_build_baah.ps1",
        "default_spec": {
            "display_name": "BAAH（蔚蓝档案 / MuMu 模拟器 ADB）",
            "repo": "BlueArchiveArisHelper/BAAH",
            "enabled": True,
            "launch_type": "python",
            "path": "BAAH",
            "python": ".venv\\Scripts\\python.exe",
            "args": ["jsoneditor.py", "{config_name}"],
            "gui": {"args": ["jsoneditor.py"], "web": True},
            "retry": 1,
            "hang_timeout": 900,
            "require_admin": True,
            "require_finish": True,
            "min_runtime_sec": 60,
            "manage_emulator": True,
            "config_dir": "configs\\BAAH",
            "links": [
                {"link": "BAAH_CONFIGS", "store": "."},
                {"link": "LOG", "store": "LOG"},
            ],
            "sync_files": [],
            "success_patterns": ["所有任务结束"],
            "error_patterns": ["运行出错", "Traceback \\(most recent call last\\)"],
            "finish_patterns": ["所有任务结束"],
        },
    },
    {
        "key": "MaaEnd",
        "display_name": "MaaEnd（明日方舟：终末地 / 独占前台）",
        "repo": "MaaEnd/MaaEnd",
        "clone_url": "https://github.com/MaaEnd/MaaEnd.git",
        "clone_args": ["--recursive", "-b", "v2"],
        "build_script": "setup/32_build_maaend.ps1",
        "default_spec": {
            "display_name": "MaaEnd（明日方舟：终末地 / 独占前台）",
            "repo": "MaaEnd/MaaEnd",
            "enabled": True,
            "launch_type": "exe",
            "path": "MaaEnd",
            "exe": "install\\mxu.exe",
            "args": ["--autostart", "-i", "{instance}", "--quit-after-run"],
            "gui": {"exe": "install\\mxu.exe"},
            "instance": "全套日常",
            "retry": 1,
            "hang_timeout": 900,
            "require_finish": True,
            "min_runtime_sec": 60,
            "stop_on_error": True,
            "stop_on_error_delay_sec": 15,
            "report_on_warn": True,
            "config_dir": "configs\\MaaEnd",
            "links": [
                {"link": "install\\config", "store": "mxu\\config"},
                {"link": "install\\debug", "store": "mxu\\debug"},
                {"link": "install\\cache", "store": "mxu\\cache"},
            ],
            "sync_files": [],
            "log_files": ["install\\debug\\maafw.log"],
            "success_patterns": [],
            "error_patterns": ["go-service panicked", "FATAL:", "分辨率不符合要求"],
            "finish_patterns": ["Tasker::run_task\\] \\| leave"],
        },
    },
    {
        "key": "ZenlessZoneZero-OneDragon",
        "display_name": "绝区零一条龙（PC 客户端）",
        "repo": "OneDragon-Anything/ZenlessZoneZero-OneDragon",
        "clone_url": "https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon.git",
        "clone_args": [],
        "build_script": None,
        "default_spec": {
            "display_name": "绝区零一条龙（PC 客户端）",
            "repo": "OneDragon-Anything/ZenlessZoneZero-OneDragon",
            "enabled": True,
            "launch_type": "python",
            "path": "ZenlessZoneZero-OneDragon",
            "python": ".venv\\Scripts\\python.exe",
            "args": ["src\\zzz_od\\application\\zzz_application_launcher.py", "--close-game"],
            "gui": {"args": ["src\\zzz_od\\gui\\app.py"]},
            "extra_pythonpath": ["src"],
            "retry": 1,
            "hang_timeout": 900,
            "require_finish": True,
            "min_runtime_sec": 120,
            "config_dir": "ZenlessZoneZero-OneDragon\\config",
            "sync_files": [],
            "success_patterns": ["one dragon finished success=True"],
            "error_patterns": ["Traceback \\(most recent call last\\)", "运行失败", "初始化超时"],
            "finish_patterns": ["one dragon finished success="],
        },
    },
    {
        "key": "BetterGI",
        "display_name": "BetterGI（原神 / 更好的原神）",
        "repo": "babalae/better-genshin-impact",
        "clone_url": "https://github.com/babalae/better-genshin-impact.git",
        "clone_args": ["--depth", "1"],
        "build_script": "setup/33_build_bettergi.ps1",
        "default_spec": {
            "display_name": "BetterGI（原神 / 更好的原神）",
            "repo": "babalae/better-genshin-impact",
            "enabled": True,
            "launch_type": "exe",
            "path": "BetterGI",
            "exe": "publish\\BetterGI.exe",
            "args": ["startOneDragon"],
            "gui": {"exe": "publish\\BetterGI.exe"},
            "retry": 1,
            "hang_timeout": 900,
            "require_finish": True,
            "min_runtime_sec": 120,
            "stop_on_finish": True,
            "report_on_warn": True,
            "require_admin": True,
            "window_keywords": ["原神", "Genshin Impact", "GenshinImpact", "YuanShen"],
            "window_finish": True,
            "window_absent_sec": 20,
            "config_dir": "configs\\BetterGI",
            "links": [
                {"link": "publish\\User", "store": "User"},
                {"link": "publish\\log", "store": "log"},
            ],
            "sync_files": [],
            "log_files": ["publish\\log\\better-genshin-impact*.log"],
            "success_patterns": ["一条龙和配置组任务结束"],
            "error_patterns": ["UnHandle Exception \\[FATAL\\]", "\\[FTL\\]", "一条龙任务将被取消"],
            "finish_patterns": ["一条龙和配置组任务结束"],
        },
    },
]

SUPPORTED_BY_KEY = {item["key"]: item for item in SUPPORTED_PROJECTS}


def supported_project(key):
    return SUPPORTED_BY_KEY.get(str(key))


def _projects(registry=None):
    registry = registry if registry is not None else config.registry()
    return registry.get("projects", {}) or {}


def is_project_installed(key, registry=None):
    """注册表里有它，并且项目目录真的在盘上。"""
    spec = _projects(registry).get(str(key))
    if not spec:
        return False
    try:
        return config.project_dir(spec).exists()
    except OSError:
        return False


# -- 配置发现 -----------------------------------------------------------------
def _base_key(key, spec):
    return str((spec or {}).get("_base_project") or (spec or {}).get("base") or key)


def discover_project_configs(key, spec=None):
    """返回 [(显示名, 取值)]：这个项目当前有哪些可选的配置。

    取值就是写进任务 config 字段、最终传给子项目的东西：
    MaaEnd = 实例名；BAAH / M7A = 配置文件名；BetterGI = 一条龙配置名。
    """
    spec = spec or _projects().get(str(key)) or {}
    base = _base_key(key, spec)
    store = config.config_dir_for(base, spec)
    found = []

    def _add(label, value=None):
        value = label if value is None else value
        if value and all(value != item[1] for item in found):
            found.append((str(label), str(value)))

    try:
        if base == "MaaEnd":
            path = store / "mxu" / "config" / "mxu-MaaEnd.json"
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                for inst in data.get("instances") or []:
                    if isinstance(inst, dict) and inst.get("name"):
                        _add(inst["name"])
        elif base == "BAAH":
            for item in sorted(store.glob("*.json")):
                _add(item.name)
        elif base == "BetterGI":
            for item in sorted((store / "User" / "OneDragon").glob("*.json")):
                _add(item.stem)
        elif base == "March7thAssistant":
            for pattern in ("*.yaml", "*.yml"):
                for item in sorted(store.glob(pattern)):
                    _add(item.name)
        else:
            for pattern in ("*.yml", "*.yaml", "*.json"):
                for item in sorted(store.glob(pattern)):
                    _add(item.name)
    except Exception as exc:
        log.warning("discover configs for %s failed: %s: %s", key, type(exc).__name__, exc)
    for name in (spec.get("config_name"), spec.get("instance")):
        if name:
            _add(name)
    return found


# -- 任务清单（chain.tasks） --------------------------------------------------
def _task_id_for(project, existing_ids):
    if project not in existing_ids:
        return project
    n = 2
    while "{0}#{1}".format(project, n) in existing_ids:
        n += 1
    return "{0}#{1}".format(project, n)


def load_tasks(settings=None, registry=None):
    """读出任务清单；settings 里还没有 chain.tasks 时按 chain.order 合成默认清单。"""
    settings = settings if settings is not None else config.settings()
    projects = _projects(registry)
    chain = settings.get("chain") or {}
    raw = chain.get("tasks")
    tasks = []
    if isinstance(raw, list) and raw:
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            project = str(item.get("project") or "")
            if project not in projects:
                continue
            tid = str(item.get("id") or "") or _task_id_for(project, seen)
            if tid in seen:
                tid = _task_id_for(project, seen)
            seen.add(tid)
            tasks.append({
                "id": tid,
                "project": project,
                "config": str(item.get("config") or ""),
                "enabled": bool(item.get("enabled", True)),
            })
        return tasks
    order = [str(k) for k in (chain.get("order") or []) if str(k) in projects]
    order += [k for k in projects if k not in order]
    hidden = {str(k) for k in ((settings.get("gui") or {}).get("hidden_projects") or [])}
    for key in order:
        if key in hidden:
            continue
        tasks.append({"id": key, "project": key, "config": "", "enabled": True})
    return tasks


def save_tasks(tasks, registry=None):
    """把任务清单写回 settings.yaml（chain.tasks + chain.order），并清掉失效的并行块。"""
    projects = _projects(registry)
    clean = []
    for item in tasks:
        if str(item.get("project")) not in projects:
            continue
        clean.append({
            "id": str(item["id"]),
            "project": str(item["project"]),
            "config": str(item.get("config") or ""),
            "enabled": bool(item.get("enabled", True)),
        })
    ids = [t["id"] for t in clean]

    def mutate(settings):
        chain = settings.setdefault("chain", {})
        if not isinstance(chain, dict):
            chain = {}
            settings["chain"] = chain
        first_migration = not (isinstance(chain.get("tasks"), list) and chain.get("tasks"))
        chain["tasks"] = clean
        chain["order"] = ids
        blocks = []
        for block in chain.get("parallel_blocks") or []:
            if isinstance(block, (list, tuple)):
                kept = [str(k) for k in block if str(k) in ids]
                if len(kept) >= 2:
                    blocks.append(kept)
        if first_migration:
            # 从旧「分组」模式迁到任务模式：分组里本来就并行的成员保持并行
            used = {k for b in blocks for k in b}
            for group in (registry or config.registry()).get("groups") or []:
                members = [m for m in (group.get("members") or []) if m in ids]
                if group.get("parallel") and len(members) >= 2 and not (set(members) & used):
                    blocks.append(members)
                    used |= set(members)
        chain["parallel_blocks"] = blocks

    settings = config.settings()
    mutate(settings)
    config.save_yaml(config.SETTINGS_YAML, settings)
    return clean


def add_task(project, cfg="", settings=None, registry=None):
    tasks = load_tasks(settings, registry)
    tid = _task_id_for(project, {t["id"] for t in tasks})
    tasks.append({"id": tid, "project": project, "config": cfg or "", "enabled": True})
    save_tasks(tasks, registry)
    return tid


# -- 删除 / 安装 / 重装 ------------------------------------------------------
def _onerror(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as exc:
        log.debug("rmtree retry failed for %s: %s", path, exc)


def _is_link(path):
    from . import configsync

    return configsync._is_link(Path(path))


def safe_remove_project_dir(project_dir, spec=None):
    """删除项目源码目录，但绝不碰 configs/<项目> 里的用户配置。

    项目里的 links（如 BAAH_CONFIGS、publish\\User）是指向 configs/ 的 junction：
    直接 rmtree 在部分 Python/Windows 组合下会顺着 junction 把用户配置一起删掉，
    所以先逐个摘链接（只摘链接本身，不碰目标）。
    """
    project_dir = Path(project_dir)
    if not project_dir.exists():
        return True
    for link in (spec or {}).get("links", []) or []:
        link_path = project_dir / link["link"]
        try:
            if link_path.exists() and _is_link(link_path):
                try:
                    os.unlink(link_path)
                except (OSError, PermissionError):
                    os.rmdir(link_path)
        except OSError as exc:
            log.warning("unlink %s failed: %s", link_path, exc)
    shutil.rmtree(project_dir, onerror=_onerror)
    return not project_dir.exists()


def delete_project(key, delete_files=False, bus=None):
    """从注册表与 settings 里摘掉项目；delete_files=True 时同时删源码目录。"""
    registry_data = config.load_yaml(config.PROJECTS_YAML, {}) or {}
    projects = registry_data.get("projects") or {}
    spec = copy.deepcopy(_projects().get(key) or {})
    projects.pop(key, None)
    for group in registry_data.get("groups") or []:
        if isinstance(group, dict) and isinstance(group.get("members"), list):
            group["members"] = [m for m in group["members"] if m != key]
    config.save_yaml(config.PROJECTS_YAML, registry_data)

    settings = config.settings()
    chain = settings.get("chain") or {}
    if isinstance(chain.get("tasks"), list):
        dropped = {t.get("id") for t in chain["tasks"] if isinstance(t, dict) and t.get("project") == key}
        chain["tasks"] = [t for t in chain["tasks"] if not (isinstance(t, dict) and t.get("project") == key)]
        chain["order"] = [k for k in (chain.get("order") or []) if k not in dropped]
        chain["parallel_blocks"] = [
            b for b in (chain.get("parallel_blocks") or [])
            if isinstance(b, list) and len([k for k in b if k not in dropped]) >= 2
        ]
        chain["parallel_blocks"] = [[k for k in b if k not in dropped] for b in chain["parallel_blocks"]]
    else:
        chain["order"] = [k for k in (chain.get("order") or []) if k != key]
    settings["chain"] = chain
    gui = settings.setdefault("gui", {})
    gui["hidden_projects"] = [k for k in (gui.get("hidden_projects") or []) if k != key]
    config.save_yaml(config.SETTINGS_YAML, settings)

    removed = None
    if delete_files and spec:
        removed = safe_remove_project_dir(config.project_dir(spec), spec)
    if bus is not None:
        bus.emit({"project": "core", "kind": "project", "level": "info",
                  "message": "project removed: {0}{1}".format(key, " (files deleted)" if removed else "")})
    return True


def _stream(cmd, cwd, log_cb, timeout=3600):
    """跑子进程并把每一行输出实时交给 log_cb；返回退出码。"""
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=host_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except Exception as exc:
        log_cb("无法启动 {0}: {1}".format(cmd[0], exc))
        return -1
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log_cb(line)
        return proc.wait(timeout=timeout)
    except Exception as exc:
        proc.kill()
        log_cb("执行超时或中断: {0}".format(exc))
        return -1


def _ensure_spec(item):
    data = config.load_yaml(config.PROJECTS_YAML, {}) or {}
    projects = data.setdefault("projects", {})
    if item["key"] not in projects:
        projects[item["key"]] = copy.deepcopy(item["default_spec"])
        config.save_yaml(config.PROJECTS_YAML, data)
        return True
    return False


def install_project(key, bus=None, log_cb=None):
    """克隆 + 构建 + 打监测补丁 + 建配置链接。返回 (ok, message)。"""
    log_cb = log_cb or (lambda text: None)
    item = supported_project(key)
    if item is None:
        return False, "不支持安装的项目：{0}".format(key)
    if _ensure_spec(item):
        log_cb("已把 {0} 写入项目注册表".format(key))
    spec = _projects().get(key) or item["default_spec"]
    project_dir = config.project_dir(spec)
    if project_dir.exists() and not (project_dir / ".git").exists() and any(project_dir.iterdir()):
        log_cb("目录已存在且不是 git 仓库，跳过克隆：{0}".format(project_dir))
    elif not (project_dir / ".git").exists():
        log_cb("git clone {0}".format(item["clone_url"]))
        code = _stream(["git", "clone"] + list(item["clone_args"]) + [item["clone_url"], str(project_dir)],
                       config.ROOT, log_cb)
        if code != 0:
            return False, "git clone 失败（退出码 {0}）".format(code)
    else:
        log_cb("源码已存在，跳过克隆")

    script = item.get("build_script")
    if script:
        script_path = config.ROOT / script
        log_cb("运行构建脚本 {0}".format(script))
        code = _stream(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path],
                       config.ROOT, log_cb)
        if code != 0:
            return False, "构建失败（退出码 {0}）".format(code)
    elif spec.get("launch_type", "python") == "python":
        log_cb("uv sync")
        code = _stream(["uv", "sync"], project_dir, log_cb)
        if code != 0:
            return False, "uv sync 失败（退出码 {0}）".format(code)

    try:
        import importlib.util

        s = importlib.util.spec_from_file_location("afk_patcher", config.MONITOR_KIT / "patcher.py")
        module = importlib.util.module_from_spec(s)
        s.loader.exec_module(module)
        module.cmd_apply(config.ROOT, [key])
        log_cb("监测补丁已应用")
    except Exception as exc:
        log_cb("监测补丁应用出现问题：{0}".format(exc))
    try:
        from . import configsync

        for line in configsync.ensure_links(key, spec):
            log_cb(line)
    except Exception as exc:
        log_cb("配置链接建立失败：{0}".format(exc))
    if bus is not None:
        bus.emit({"project": key, "kind": "project", "level": "info", "message": "project installed"})
    return True, "安装完成"


def reinstall_project(key, spec=None, bus=None, log_cb=None):
    """删源码（保留 configs/<项目>）再装一遍。"""
    log_cb = log_cb or (lambda text: None)
    spec = spec or _projects().get(key) or {}
    try:
        import importlib.util

        s = importlib.util.spec_from_file_location("afk_patcher", config.MONITOR_KIT / "patcher.py")
        module = importlib.util.module_from_spec(s)
        s.loader.exec_module(module)
        module.cmd_revert(config.ROOT, [key])
    except Exception as exc:
        log_cb("回滚监测补丁失败（忽略）：{0}".format(exc))
    if spec:
        log_cb("删除旧源码（用户配置保留在 configs/）")
        if not safe_remove_project_dir(config.project_dir(spec), spec):
            return False, "旧源码目录没能删干净，请关闭占用它的程序后重试"
    return install_project(key, bus=bus, log_cb=log_cb)
