import copy
import logging
import os
import threading
import time
from pathlib import Path

import yaml

# 优先用 libyaml 的 C 实现解析/转储。纯 Python 的 SafeLoader 解析
# afk_projects.yaml（8.8KB）要 9.4ms，CSafeLoader 只要 1.3ms；settings.yaml
# 3.6ms → 0.43ms。settings()/registry() 每次调用都重读盘，配置页一次刷新
# 读十几次就是一百多毫秒的纯解析开销。两个 loader 的语义与输出实测一致
# （同一份 yaml 解析结果 deepcopy 相等，dump 逐字节相同），所以可直接替换；
# 万一环境里没编译 libyaml 就退回纯 Python 版，功能不受影响。
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_YAML_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


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
                data = yaml.load(f, Loader=_YAML_LOADER)
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
            except OSError as exc:
                # 删不掉只会在 logs/ 旁边留一份 .tmp，不影响本次写入；
                # 但反复留垃圾说明有别的进程一直占着它，值得一行 debug。
                logging.getLogger("afk.config").debug("remove temp file %s failed: %s", tmp, exc)
        # 写完立刻让读盘缓存失效。缓存的失效条件是 mtime_ns + 大小，正常情况
        # 下 os.replace 一定改 mtime；但同一进程里连写两次可能落在同一个时间
        # 刻度上（NTFS 的 100ns 精度并非每个文件系统都保证），那就读回旧值。
        # 主动清一次最省心——重新解析只要 1.2ms，而读到过期配置会让用户
        # 「改完设置没生效」。
        invalidate_cache()


def save_yaml(path, data):
    _write_atomic(
        path,
        yaml.dump(data, Dumper=_YAML_DUMPER, allow_unicode=True, sort_keys=False),
    )


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


# -- 读盘缓存 ---------------------------------------------------------------
# settings()/registry() 是所有模块的配置入口，一次开窗就要调几十次（空转还会
# 每 300ms 调），每次都重读文件 + 重新解析 YAML。走 CSafeLoader 后单次解析
# settings 0.46ms、afk_projects 1.21ms，开窗累计仍是几十毫秒的纯重复劳动。
#
# 缓存键是「路径 + mtime_ns + size」：配置都是经 _write_atomic 用 os.replace
# 落盘的，替换后 mtime 一定变，所以不需要额外失效通知；测试把 *_YAML 指到临时
# 文件也只是换个键。
#
# 命中时返回 deepcopy（settings 0.045ms、registry 0.13ms，比重新解析快一个
# 数量级）。**不能直接返回缓存对象**：调用方普遍拿到就改
# （`settings.setdefault("pre_run", {})`、`gui_cfg["window_size"] = [...]`），
# 共享一份字典会让这些改动串到别的调用方甚至写回磁盘。
#
# 只保留「最近一次」的条目：缓存的意义是同一份文件被连续读取，多版本留在
# 内存里反而要各自 deepcopy 管理。
_CACHE_LOCK = threading.Lock()
_SETTINGS_CACHE = {}
_REGISTRY_CACHE = {}


def invalidate_cache():
    """丢掉读盘缓存（保存配置后、测试里换文件后调用）。"""
    with _CACHE_LOCK:
        _SETTINGS_CACHE.clear()
        _REGISTRY_CACHE.clear()


def _file_stamp(path):
    """文件的缓存标识；文件不存在（或读不到属性）时返回 None 走不缓存分支。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size)


def _cached(path, cache, build):
    stamp = _file_stamp(path)
    if stamp is None:
        return build()
    with _CACHE_LOCK:
        hit = cache.get(stamp)
    if hit is not None:
        return copy.deepcopy(hit)
    value = build()
    with _CACHE_LOCK:
        cache.clear()
        cache[stamp] = copy.deepcopy(value)
    return value


def _load_settings():
    data = load_yaml(SETTINGS_YAML, {})
    return _normalize_settings(data, _WARNED_SETTINGS)


def settings():
    return _cached(SETTINGS_YAML, _SETTINGS_CACHE, _load_settings)


# 已经就「某个设置节不是映射」告警过的节名。settings() 每次调用都会重读文件，
# 逐次告警会把日志刷满（一次开窗就能重复几十遍）。
_WARNED_SETTINGS = set()

# settings.yaml 里应当是「键: 值」映射的节。写成标量或列表时，所有
# `settings.get("x") or {}` 之后的 `.get(...)` 都会 AttributeError。
_SETTINGS_SECTIONS = (
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


def _normalize_settings(data, warned=None):
    """把 settings.yaml 里应当是映射的节归一成映射。

    历史缺陷（2026-11-06 发现）：``load_yaml`` 只保证顶层是映射，节本身的形状
    完全没管。在「设置」tab 把手改的 ``schedule: true``（或 ``pre_run: abc``、
    ``chain: [1, 2]``、``gui: 7`` …）保存下去，程序下次就再也打不开：

        gui/main.py:353  (self.settings.get("gui") or {}).get(...)
            → AttributeError: 'bool' object has no attribute 'get'
        gui/main.py:555  schedule  → 同
        gui/main.py:549  pre_run   → 同
        gui/settings_page.py:142  post_actions → 同
        gui/settings_page.py:168  idle         → 同
        gui/settings_page.py:297  chain        → 同
        afk_core/housekeeping.py:40  housekeeping → 同（后台清理线程）

    用户在界面上根本没法把它改回来（界面自己起不来），只能手工编辑 yaml。
    这里在唯一读取入口统一归一：不是映射的节按空映射处理（各处的默认值兜底），
    并留一条 warning 指明是哪一节、读到了什么类型。
    """
    if warned is None:
        warned = set()
    for section in _SETTINGS_SECTIONS:
        if section not in data:
            continue
        value = data.get(section)
        if value is None or isinstance(value, dict):
            continue
        if section not in warned:
            warned.add(section)
            logging.getLogger("afk.config").warning(
                "settings.yaml 的 %s 不是映射（读到 %s），已按默认值处理；"
                "请把它写成「%s:\n  key: value」缩进形式",
                section,
                type(value).__name__,
                section,
            )
        data[section] = {}
    return data


# 已经就「gui 不是映射」告警过的项目（registry() 每次刷新都会重读，逐次告警会
# 把日志刷满——实测开一次窗口就重复 8 遍）。
_WARNED_GUI = set()
# 同理，注册表结构问题也只告警一次（键是位置，不是项目名）
_WARNED_SHAPE = set()


def _normalize_gui(spec, key=""):
    """把项目规格里的 ``gui`` 归一成「映射」或 None。

    历史缺陷：注册表里手写 ``gui: true``（最自然的一句「这个项目有界面」）会让
    整个程序打不开——supervisor._build_gui_command 与界面调用方都直接对它调
    ``.get()``，于是 MainWindow 构造时抛
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
            "要打开界面请写成 gui: {args: [...]} 或 gui: {exe: ...}",
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


def _prune_group_members(data, warned):
    """把 groups 里引用了不存在项目的成员剔除。

    历史缺陷：在「项目注册表」里删掉一个项目后，groups 的 members 仍留着它。
    链的计划照旧把它排进去（`_chain_plan` 只看 enabled 与 only_keys，不看
    项目是否存在），真正启动时才命中 `start_project` 的
    `unknown project` 分支——而且那条分支用的是 `level="error"`，看起来像是
    「项目跑失败了」，实际只是注册表里没有它。并行组里混进一个幽灵成员时，
    `merge_parallel_blocks` 还会把整块判成「某个项目已经在另一个并行组里」，
    用户点「并行」却完全没并行。

    剔除后仍不足两人的组、或剔除后成员为空的组保持原样（组本身是配置，不是
    派生数据；空组在 `_chain_plan` 里本来就会被跳过）。只有真发现幽灵成员时
    才留 warning，避免每次读注册表都刷日志。
    """
    projects = data.get("projects") or {}
    if not isinstance(projects, dict):
        return
    ghosts = []
    for group in data.get("groups") or []:
        if not isinstance(group, dict):
            continue
        members = group.get("members")
        if not isinstance(members, list):
            continue
        kept = [m for m in members if m in projects]
        for member in members:
            if member not in projects:
                ghosts.append("{0}:{1}".format(group.get("id", "?"), member))
        if len(kept) != len(members):
            group["members"] = kept
    if ghosts and warned is not None and "groups:ghost" not in warned:
        warned.add("groups:ghost")
        logging.getLogger("afk.config").warning(
            "afk_projects.yaml 的 groups 里有成员不在 projects 里，已剔除：%s",
            ", ".join(ghosts),
        )


def _merge_spec(base, override):
    """把 override 深合并到 base 之上，返回新映射（两者都不被修改）。

    规则：
      * 映射与映射 → 递归合并
      * 其余情况 → override 的值整体替换（含列表，列表不做元素级合并，
        否则「实例想清空 links」永远清不掉）
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_spec(merged[key], value)
        else:
            merged[key] = value
    return merged


def _expand_bases(projects, warned=None):
    """展开项目规格里的 ``base: <项目名>``（同项目多卡片/多配置文件的支点）。

    用途：同一个挂机项目要出第二张卡片跑另一套配置时，不必复制整段规格，
    只写与基项目的差异：

        BAAH-日服:
          base: BAAH
          config_name: 日服挂机.json

    展开后 ``base`` 键仍保留（方便界面显示「来自哪个项目」），但所有字段
    都已合并到位，运行时各处（_build_command / project_dir / config_dir_for）
    拿到的都是完整规格。

    循环引用（A→B→A）与指向不存在项目的 base 都会被丢弃并告警一次：这类
    配置一旦进入运行路径就是无限递归或 KeyError，必须在这里挡住。
    """
    if not isinstance(projects, dict):
        return
    resolved = {}

    def resolve(key, stack):
        if key in resolved:
            return resolved[key]
        spec = projects.get(key)
        if not isinstance(spec, dict):
            return {}
        base_key = spec.get("base")
        if not isinstance(base_key, str) or not base_key or base_key == key:
            resolved[key] = dict(spec)
            return resolved[key]
        if base_key in stack:
            if warned is not None and "projects:base-cycle" not in warned:
                warned.add("projects:base-cycle")
                logging.getLogger("afk.config").warning(
                    "afk_projects.yaml 里 base 出现循环引用（%s），已忽略 %s 的 base",
                    " -> ".join(list(stack) + [base_key]),
                    key,
                )
            resolved[key] = dict(spec)
            return resolved[key]
        if base_key not in projects:
            if warned is not None and "projects:base-missing" not in warned:
                warned.add("projects:base-missing")
                logging.getLogger("afk.config").warning(
                    "afk_projects.yaml 里 %s 的 base 指向不存在的项目 %r，已按独立项目处理",
                    key,
                    base_key,
                )
            resolved[key] = dict(spec)
            return resolved[key]
        parent = resolve(base_key, stack + (base_key,))
        merged = _merge_spec(parent, spec)
        # 没有单独写 display_name 时，用「基项目名（实例名）」区分卡片，
        # 否则两张卡在界面上完全同名，用户分不清哪张跑哪套配置。
        if "display_name" not in spec:
            label = str(parent.get("display_name") or base_key)
            merged["display_name"] = "{0}（{1}）".format(label, key)
        resolved[key] = merged
        return merged

    for key in list(projects.keys()):
        projects[key] = resolve(key, (key,))


def _load_registry():
    data = load_yaml(PROJECTS_YAML, {})
    warned = _WARNED_SHAPE
    data.setdefault("projects", {})
    data.setdefault("groups", [])
    data.setdefault("reserved", [])
    _normalize_projects(data.get("projects"), data, warned)
    _normalize_groups(data.get("groups"), data, warned)
    _expand_bases(data.get("projects"), warned)
    _prune_group_members(data, warned)
    projects = data.get("projects")
    if isinstance(projects, dict):
        for key, spec in projects.items():
            if isinstance(spec, dict):
                spec["gui"] = _normalize_gui(spec, key)
    return data


def registry():
    return _cached(PROJECTS_YAML, _REGISTRY_CACHE, _load_registry)


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
