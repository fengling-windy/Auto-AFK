import logging
import os
import threading
import time
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
    # 读盘也要能扛住「正好有别人在写这份文件」。GUI 里设置页保存与窗口尺寸保存
    # 会并发，os.replace 完成那一瞬目标文件被短暂占用，open() 会抛
    # PermissionError [Errno 13]。如果这里直接回落到默认值，调用方拿到空配置、
    # 再原样写回去，用户的设置就被抹掉了——所以先退避重试，实在读不到才回落。
    data = None
    last_error = None
    for attempt in range(12):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except FileNotFoundError:
            return fallback
        except OSError as exc:
            last_error = exc
            time.sleep(0.01 * (attempt + 1))
            continue
        break
    if data is None and last_error is not None:
        logging.getLogger("afk.config").warning("failed to parse %s: %s (falling back to defaults)", path, last_error)
        return fallback
    try:
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
    except Exception as exc:
        logging.getLogger("afk.config").warning("failed to parse %s: %s (falling back to defaults)", path, exc)
        return fallback


# 保存配置文件用同一个锁：GUI 里设置页的合并落盘和窗口尺寸保存会并发写同一个文件
_WRITE_LOCK = threading.Lock()


def _write_atomic(path, text):
    """把 text 写进 path，尽量做到「要么是旧内容，要么是新内容」。

    历史缺陷（2026-11-06 发现）：这里原本用固定的 `<文件名>.tmp` 做中转，
    GUI 里两处同时保存（设置页的 400ms 合并落盘 + 拖动窗口时写 window_size）
    就会抢同一个 tmp：实测 8 线程 × 30 次保存有 7 路抛
    `PermissionError [WinError 32] ... settings.yaml.tmp -> settings.yaml`。
    另一种情况是目标文件正被别的句柄开着（设置页的 yaml 编辑器、杀软扫描、
    用户用编辑器打开着 settings.yaml），Windows 上 os.replace 需要目标文件的
    DELETE 权限 → `WinError 5 拒绝访问`，保存直接失败。

    所以：唯一 tmp 名 + 线程锁 + 退避重试，实在替换不了就退回原地重写
    （原地重写只要写权限，不需要 DELETE 权限）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    with _WRITE_LOCK:
        tmp = path.with_name("{0}.{1}.{2}.tmp".format(path.name, os.getpid(), threading.get_ident()))
        try:
            with open(tmp, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            last = None
            for attempt in range(20):
                try:
                    os.replace(tmp, path)
                    return
                except OSError as exc:
                    last = exc
                    time.sleep(0.02 * (attempt + 1))
            # 目标被别的句柄占着：退回原地重写（不留 .tmp 垃圾）
            with open(path, "r+b") as f:
                f.seek(0)
                f.write(payload)
                f.truncate(len(payload))
            logging.getLogger("afk.config").debug(
                "%s 无法原子替换（%s），已就地重写", path, last)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


def save_yaml(path, data):
    _write_atomic(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def write_text_atomic(path, text):
    """公开的原子写文本（state.json / 标记文件等不能用 write_text 直接覆盖）。"""
    _write_atomic(Path(path), text)


def read_text(path, attempts=12):
    """读文本，扛住「正好有别人在原子替换这份文件」。

    Windows 上 os.replace 完成那一瞬目标文件会被短暂占用：`read_text` 会抛
    `PermissionError: [Errno 13]`，而替换过程中目标可能短暂不存在，抛
    `FileNotFoundError`。实测 state.json 并发读（CLI status / GUI 面板）能读到
    空文件或直接抛 PermissionError，于是被当成「状态文件损坏」按全新状态处理。
    这里和 load_yaml 一样退避重试，实在读不到才把异常抛给调用方。
    """
    last = None
    for attempt in range(max(1, attempts)):
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            last = exc
            time.sleep(0.01 * (attempt + 1))
    raise last


def settings():
    return load_yaml(SETTINGS_YAML, {})


# 已经就「gui 不是映射」告警过的项目（registry() 每次刷新都会重读，逐次告警会
# 把日志刷满——实测开一次窗口就重复 8 遍）。
_WARNED_GUI = set()
# 同理，注册表结构问题也只告警一次（键是位置，不是项目名）
_WARNED_SHAPE = set()


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


def _normalize_projects(raw, data, warned):
    """把 registry()['projects'] 归一成 {项目名: 规格映射}。

    历史缺陷：`load_yaml` 只保证顶层是映射，`afk_projects.yaml` 里的
    `projects` / `groups` 是什么形状完全没管。于是手写错误会一路传到运行时：
      * `projects: [a, b]` → Supervisor.start_project 的
        `self._projects().get(key)` 抛 `AttributeError: 'list' object has no
        attribute 'get'`，用户点「启动」失败且界面无提示；
      * `groups: hello` 或 `groups: [hello]` → background_group_ids() 抛
        `'str' object has no attribute 'get'`（空闲自动挂机会在后台线程里
        静默失败）；`_chain_worker` 里 `[g.get("id") for g in groups]` 也炸，
        链线程直接死掉，界面停在「运行中」。
      * `projects: {A: hello}` → 单项目规格不是映射，project_dir() 就炸。
    这里在唯一读取入口统一归一，坏条目丢掉并留一条 warning（同 _normalize_gui
    的思路）。丢掉是安全的：坏条目的项目本来也无法运行。
    """
    if not isinstance(raw, dict):
        if warned is not None and "projects" not in warned:
            warned.add("projects")
            logging.getLogger("afk.config").warning(
                "afk_projects.yaml 的 projects 不是映射（读到 %s），已按空注册表处理；"
                "请把项目写成「项目名: {规格}」的形式",
                type(raw).__name__,
            )
        data["projects"] = {}
        return
    cleaned = {}
    dropped = []
    for key, spec in raw.items():
        if isinstance(spec, dict):
            cleaned[key] = spec
        else:
            dropped.append(str(key))
    if dropped and warned is not None and "projects:spec" not in warned:
        warned.add("projects:spec")
        logging.getLogger("afk.config").warning(
            "afk_projects.yaml 里这些项目的规格不是映射，已忽略：%s", ", ".join(dropped)
        )
    data["projects"] = cleaned


def _normalize_groups(raw, data, warned):
    """把 registry()['groups'] 归一成 [{...}]，坏条目丢掉。

    分组直接决定链怎么跑：组不是列表、组不是映射、members 不是列表都会让
    链线程或空闲自动挂机抛 AttributeError。这里一并归一，并把 members 里
    非字符串的成员丢掉（成员名必须是项目名）。
    """
    if not isinstance(raw, list):
        if warned is not None and "groups" not in warned:
            warned.add("groups")
            logging.getLogger("afk.config").warning(
                "afk_projects.yaml 的 groups 不是列表（读到 %s），已按「没有分组」处理",
                type(raw).__name__,
            )
        data["groups"] = []
        return
    cleaned = []
    dropped = 0
    for group in raw:
        if not isinstance(group, dict):
            dropped += 1
            continue
        members = group.get("members")
        if isinstance(members, str):
            # "members: A" 是常见的手写让步（单个成员忘了写列表），能救就救
            group["members"] = [members]
        elif isinstance(members, list):
            group["members"] = [m for m in members if isinstance(m, str)]
        elif members is not None:
            group["members"] = []
        cleaned.append(group)
    if dropped and warned is not None and "groups:item" not in warned:
        warned.add("groups:item")
        logging.getLogger("afk.config").warning(
            "afk_projects.yaml 里有 %d 个分组不是映射，已忽略", dropped
        )
    data["groups"] = cleaned


def registry():
    data = load_yaml(PROJECTS_YAML, {})
    warned = _WARNED_SHAPE
    data.setdefault("projects", {})
    data.setdefault("groups", [])
    data.setdefault("reserved", [])
    _normalize_projects(data.get("projects"), data, warned)
    _normalize_groups(data.get("groups"), data, warned)
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
