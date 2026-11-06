import codecs
import json
import logging
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path

import psutil

from . import config, configsync, monitor, notify, report
from .proc import popen as proc_popen, run as proc_run


def is_admin():
    if os.name != "nt":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# cap per-cycle tail reads so a log burst cannot balloon memory; the rest is
# picked up on the next cycle (partial lines stay buffered)
MAX_TAIL_CHUNK = 1 * 1024 * 1024


class FileTailer:
    def __init__(self, path, start_at_end=True):
        self.path = Path(path)
        self.pos = 0
        self.partial = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        if start_at_end:
            try:
                self.pos = self.path.stat().st_size
            except OSError:
                self.pos = 0

    def read_new(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.pos:
            self.pos = 0
            self.partial = ""
            self._decoder.reset()
        if size == self.pos:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                raw = f.read(MAX_TAIL_CHUNK)
                self.pos = f.tell()
        except OSError:
            return []
        if not raw:
            return []
        data = self._decoder.decode(raw)
        if not data:
            return []
        text = self.partial + data
        lines = text.splitlines()
        self.partial = ""
        if not data.endswith(("\n", "\r")):
            self.partial = lines.pop() if lines else ""
        return [line for line in lines if line.strip()]


class ProjectRun:
    def __init__(self, key, spec, attempt=1):
        self.key = key
        self.spec = spec
        self.attempt = attempt
        self.run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.process = None
        self.status = "pending"
        self.exit_code = None
        self.started_at = None
        self.finished_at = None
        self.hanging = False
        self.stopped = False
        self.last_message = ""
        self.report_dir = ""
        self.children = set()
        self.managed_emulator = False
        self.managed_main = False
        self.finished_by_pattern = False
        self.window_seen = False
        self.finished_event = threading.Event()
        self.out_log = config.PROJECT_LOGS / "{0}_{1}.out.log".format(key, self.run_id)
        self.err_log = config.PROJECT_LOGS / "{0}_{1}.err.log".format(key, self.run_id)

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    @property
    def terminal(self):
        return self.status in ("success", "success_unverified", "failed", "stopped", "hang")

    def to_dict(self):
        return {
            "key": self.key,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "hanging": self.hanging,
            "stopped": self.stopped,
            "last_message": self.last_message,
            "report_dir": self.report_dir,
            "managed_emulator": self.managed_emulator,
            "managed_main": self.managed_main,
            "window_seen": self.window_seen,
            "pid": self.process.pid if self.process else None,
        }


def apply_chain_order(members, order):
    """按 chain.order 给组内成员排序；未列出的项目保持原相对顺序垫底。"""
    rank = {str(k): i for i, k in enumerate(order or [])}
    return sorted(members, key=lambda m: rank.get(m, len(rank)))


class Supervisor:
    # chain_status 只可能是这五种；state.json 里读到别的东西（比如被手改成数字）
    # 一律当 idle 处理，避免界面上出现无法比较的状态。
    CHAIN_STATUSES = ("idle", "running", "stopped", "success", "failed")

    def __init__(self, bus, settings=None, repair=None, on_change=None):
        self.bus = bus
        self.settings = settings or config.settings()
        self.repair = repair
        self.notifier = notify.Notifier(bus, self.settings)
        self.on_change = on_change
        self.registry = config.registry()
        self.runs = {}
        self.chain_status = "idle"
        self._chain_thread = None
        self._chain_stop = threading.Event()
        self._chain_soft = threading.Event()
        self.startup_update_started = False
        self.startup_update_done = threading.Event()
        self._post_action_timer = None
        self._orphan_runs = {}
        # per-project launch counter for the current chain: a failed project is
        # retried at the end of the chain, but never more than the configured
        # cap in total (see settings.chain.max_attempts_per_project).
        self._chain_attempts = {}
        self._chain_max_attempts = 3
        self._lock = threading.RLock()
        self.bus.status_provider = self.status_snapshot
        self.bus.command_handler = self._handle_command
        self._restore_state()
        self._drain_spools()

    def _projects(self):
        return self.registry.get("projects", {})

    def _groups(self):
        return self.registry.get("groups", [])

    def background_group_ids(self):
        """标记 background: true 的组（不占用鼠标的任务，如云游戏/模拟器组）。

        空闲自动挂机的「仅运行不占用鼠标任务」只跑这些组。"""
        ids = []
        for group in self._groups():
            if group.get("background") and group.get("id"):
                ids.append(str(group["id"]))
        return ids

    def chain_running(self):
        """链是否正在跑；也把「手动单独启动的项目仍在运行」算作忙。"""
        if self.chain_status == "running":
            return True
        thread = self._chain_thread
        if thread is not None and thread.is_alive():
            return True
        return any(run.alive for run in self.runs.values())

    def _emit(self, project, kind, message, level="info", data=None):
        self.bus.emit(
            {
                "project": project,
                "kind": kind,
                "level": level,
                "message": message,
                "data": data or {},
            }
        )
        if self.on_change:
            try:
                self.on_change()
            except Exception as exc:
                logging.getLogger("afk.supervisor").warning("on_change callback failed: %s", exc)

    def _restore_state(self):
        # state.json 是上一次运行留下的快照，用户/杀毒/断电都可能把它写坏。
        # 恢复时不能假设结构正确：以前只判断「json.loads 成功」，于是
        #   * `"runs": [1,2,3]` → _orphan_runs 变成 list，
        #     cleanup_orphans() 里 `self._orphan_runs.keys()` 抛
        #     AttributeError: 'list' object has no attribute 'keys' ——
        #     而这发生在 start_project() 里，用户点「启动」直接炸；
        #   * `"runs": {"BAAH": "x"}` → run.get 抛 'str' has no attribute 'get'；
        #   * `"chain_status": 123` → 界面上链状态变成数字，所有比较都失效。
        # 现在逐项校验类型，任何一项不对就退回默认值并留一条 warning。
        try:
            if config.STATE_FILE.exists():
                data = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("顶层不是映射（读到 {0}）".format(type(data).__name__))
                status = data.get("chain_status", "idle")
                if isinstance(status, str) and status in self.CHAIN_STATUSES:
                    self.chain_status = status
                else:
                    logging.getLogger("afk.supervisor").warning(
                        "state.json 的 chain_status 不可用（%r），按 idle 处理", status)
                    self.chain_status = "idle"
                orphans = data.get("runs", {}) or {}
                if not isinstance(orphans, dict):
                    logging.getLogger("afk.supervisor").warning(
                        "state.json 的 runs 不是映射（读到 %s），忽略残留进程记录",
                        type(orphans).__name__)
                    orphans = {}
                # 值必须是映射才有 .get；逐条过滤，坏条目只丢自己
                self._orphan_runs = {k: v for k, v in orphans.items() if isinstance(v, dict)}
                dropped = len(orphans) - len(self._orphan_runs)
                if dropped:
                    logging.getLogger("afk.supervisor").warning(
                        "state.json 里有 %d 条残留进程记录格式不对，已忽略", dropped)
        except Exception as exc:
            logging.getLogger("afk.supervisor").warning(
                "state.json 读取失败，按全新状态启动：%s", exc)
            self.chain_status = "idle"
            self._orphan_runs = {}

    def _handle_command(self, payload):
        action = (payload or {}).get("action")
        if action == "stop":
            key = payload.get("project")
            if not key:
                return {"ok": False, "error": "project required"}
            return {"ok": bool(self.stop_project(key))}
        if action == "stop_all":
            self.stop_all()
            return {"ok": True}
        if action == "soft_stop":
            self.stop_chain_soon()
            return {"ok": True}
        if action == "open_gui":
            key = payload.get("project")
            if not key:
                return {"ok": False, "error": "project required"}
            return {"ok": bool(self.open_project_gui(key))}
        if action in ("chain", "run_chain"):
            # 空闲提示框的「立刻开始」/「仅后台任务」也走这里，方便命令行远程控制
            background = bool(payload.get("background"))
            return {"ok": bool(self.start_chain(background_only=background))}
        if action == "status":
            return {"ok": True, "status": self.status_snapshot()}
        return {"ok": False, "error": "unknown action"}

    def start_chain(self, background_only=False, on_finish=None):
        """按组启动任务链；background_only=True 时只跑标记 background 的组。"""
        self.settings = config.settings()
        self.registry = config.registry()
        group_ids = None
        if background_only:
            group_ids = self.background_group_ids()
            if not group_ids:
                self._emit(
                    "core",
                    "chain",
                    "no background group configured (set background: true on a group)",
                    level="warn",
                )
                return False
        return self.run_chain(group_ids=group_ids, on_finish=on_finish)

    def _drain_spools(self, keys=None):
        try:
            if keys is None:
                candidates = sorted(config.PROJECT_LOGS.glob("*_spool.jsonl"))
            else:
                candidates = [config.PROJECT_LOGS / "{0}_spool.jsonl".format(key) for key in keys]
            for path in candidates:
                self.bus.drain_spool(path)
        except Exception as exc:
            logging.getLogger("afk.supervisor").warning("spool drain failed: %s", exc)

    def cleanup_orphans(self, keys=None):
        # _restore_state() 已经把 state.json 里的坏条目过滤掉了，这里再兜一层：
        # cleanup_orphans 是在 start_project() 里被调用的，一旦抛异常，用户点
        # 「启动」就直接失败，没有任何界面提示。所以这里对我们自己持有的数据
        # 也只做「能处理就处理，不能就跳过」。
        if not isinstance(self._orphan_runs, dict):
            self._orphan_runs = {}
        for key in list(self._orphan_runs.keys()) if keys is None else list(keys):
            run = self._orphan_runs.get(key)
            self._orphan_runs.pop(key, None)
            if not isinstance(run, dict) or not run:
                continue
            pid = run.get("pid")
            if run.get("status") != "running" or not pid:
                continue
            if any(r.process is not None and r.process.pid == int(pid) for r in self.runs.values()):
                continue
            try:
                proc = psutil.Process(int(pid))
                if not proc.is_running():
                    continue
                spec = (self._projects() or {}).get(key, {})
                project = config.project_dir(spec) if spec else None
                try:
                    cwd = Path(proc.cwd())
                except Exception:
                    cwd = None
                if cwd is not None and project is not None and cwd != project:
                    continue
                for child in proc.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
                proc.kill()
                self._emit(key, "cleanup", "killed orphan process from previous run (pid={0})".format(pid), level="warn")
            except Exception as exc:
                self._emit(key, "cleanup", "could not clean orphan pid={0}: {1}".format(pid, exc), level="warn")

    def _persist(self):
        try:
            data = {
                "chain_status": self.chain_status,
                "updated": time.time(),
                "runs": {k: v.to_dict() for k, v in self.runs.items()},
                "project_status": {k: v.status for k, v in self.runs.items()},
            }
            config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            config.STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            # 状态持久化失败不能中断监督线程，但必须留痕，否则 state.json 会悄悄停止更新
            logging.getLogger("afk.supervisor").warning("failed to persist supervisor state: %s", exc)

    def status_snapshot(self):
        with self._lock:
            runs = {k: v.to_dict() for k, v in self.runs.items()}
            pending = [k for k in self._projects() if k not in runs]
            return {
                "chain_status": self.chain_status,
                "runs": runs,
                "pending": pending,
                "event_port": self.bus.port,
            }

    def _build_command(self, key, spec):
        project = config.project_dir(spec)
        fmt = dict(spec)
        fmt.setdefault("instance", spec.get("instance", ""))
        fmt.setdefault("config_name", spec.get("config_name", ""))
        launch_type = spec.get("launch_type", "python")
        if launch_type == "python":
            exe = project / spec.get("python", ".venv\\Scripts\\python.exe")
        else:
            exe = project / spec.get("exe", "")
        args = [str(exe)]
        for item in spec.get("args", []) or []:
            try:
                args.append(str(item).format(**fmt))
            except Exception:
                args.append(str(item))
        return args, project

    def _build_gui_command(self, key, spec):
        """GUI-mode command from the spec's `gui` block: {"args": [...]} uses the
        project venv python, {"exe": ...} runs an exe directly."""
        gui = spec.get("gui")
        if not gui:
            return None, config.project_dir(spec)
        project = config.project_dir(spec)
        fmt = dict(spec)
        if gui.get("exe"):
            exe = project / gui["exe"]
        else:
            exe = project / gui.get("python", spec.get("python", ".venv\\Scripts\\python.exe"))
        args = [str(exe)]
        for item in gui.get("args", []) or []:
            try:
                args.append(str(item).format(**fmt))
            except Exception:
                args.append(str(item))
        return args, project

    def _build_gui_env(self, key, spec, project):
        """Environment for GUI mode: no AFK monitor injection, and no UTF-8
        mode override — project GUIs are designed for the default locale.

        PYTHONUTF8 MUST NOT be set: it flips locale.getpreferredencoding(False)
        from cp936 to utf-8, and a child's ``subprocess.run(..., text=True)``
        then decodes the launcher exe's GBK output as UTF-8, raising
        UnicodeDecodeError inside ``_readerthread`` -> empty version ->
        ZZZ shows "原始启动器 未安装". PYTHONIOENCODING is unrelated to that
        path (it only sets sys.std* encoding), so it is safe to pin to utf-8."""
        env = os.environ.copy()
        for name in list(env):
            if name.startswith("AFK_"):
                env.pop(name, None)
        for name in ("PYTHONUTF8", "PYTHONIOENCODING"):
            env.pop(name, None)
        pythonpath = []
        for extra in (spec.get("extra_pythonpath", []) or []):
            p = Path(str(extra))
            if not p.is_absolute():
                p = project / extra
            pythonpath.append(str(p))
        existing = env.get("PYTHONPATH")
        if existing:
            pythonpath.append(existing)
        if pythonpath:
            env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Do NOT set PYTHONUTF8 here: UTF-8 mode rewrites
        # locale.getpreferredencoding(False) to utf-8, so the project's own
        # ``subprocess.run(..., text=True)`` calls decode GBK children (e.g.
        # OneDragon-Launcher.exe --version) as UTF-8 and blow up inside
        # _readerthread. The traceback is captured by the monitor hook and
        # reported as an error event, which makes every ZZZ run fail even when
        # the one-dragon exits 0. PYTHONIOENCODING is the correct knob: it pins
        # our own stdout/stderr to utf-8 for the tailer without touching the
        # locale, so GBK children keep decoding as cp936.
        for k, v in (spec.get("env", {}) or {}).items():
            env[str(k)] = str(v)
        return env

    def spawn_project_gui(self, key, extra_args=None, report_port=None):
        """Spawn a project's own configuration GUI (not a supervised task run)
        and return the Popen handle so the caller can locate/embed its window.

        report_port 传入时（仅 python 入口），用 gui/gui_bootstrap.py 包裹启动：
        引导脚本在项目进程内运行入口并把主窗口 HWND 上报给 Auto-AFK，
        窗口定位零搜索、不会误抓其他窗口。
        The spawned process is detached: it outlives Auto-AFK and is neither
        monitored nor cleaned up. Returns None on failure."""
        spec = self._projects().get(key)
        if spec is None:
            self._emit("core", "error", "unknown project: {0}".format(key), level="error")
            return None
        cmd, project = self._build_gui_command(key, spec)
        if cmd is None:
            self._emit(key, "gui", "no gui entry configured for this project", level="warn")
            return None
        if extra_args:
            cmd.extend(str(a) for a in extra_args)
        if report_port is not None and spec.get("launch_type", "python") == "python" and not (spec.get("gui") or {}).get("exe"):
            bootstrap = config.ROOT / "gui" / "gui_bootstrap.py"
            if bootstrap.exists():
                # [python, 引导脚本, 上报端口, 原脚本, 原参数...]
                cmd = [cmd[0], str(bootstrap), str(int(report_port))] + cmd[1:]
        env = self._build_gui_env(key, spec, project)
        # CREATE_NO_WINDOW hides the venv launcher's console; unlike
        # STARTF_USESHOWWINDOW SW_HIDE it does not hide the app's Qt window
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = proc_popen(
                cmd,
                cwd=str(project),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except Exception as exc:
            self._emit(key, "error", "failed to open GUI: {0}".format(exc), level="error")
            return None
        self._emit(key, "gui", "project GUI opened")
        return proc

    def open_project_gui(self, key):
        return self.spawn_project_gui(key) is not None

    def _build_env(self, key, run, project):
        env = os.environ.copy()
        env["AFK_MONITOR"] = "1"
        env["AFK_PROJECT"] = key
        env["AFK_RUN_ID"] = run.run_id
        env["AFK_EVENT_PORT"] = str(self.bus.port)
        env["AFK_EVENT_SPOOL"] = str(config.PROJECT_LOGS / "{0}_spool.jsonl".format(key))
        env["AFK_HEARTBEAT_SEC"] = str(self.settings.get("heartbeat_sec", 20))
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("PYTHONUTF8", None)
        pythonpath = [str(config.MONITOR_KIT), str(project)]
        for extra in (run.spec.get("extra_pythonpath", []) or []):
            extra_path = Path(str(extra))
            if not extra_path.is_absolute():
                extra_path = project / extra
            pythonpath.append(str(extra_path))
        existing = env.get("PYTHONPATH")
        if existing:
            pythonpath.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        for k, v in (self.registry.get("env", {}) or {}).items():
            env[str(k)] = str(v)
        for k, v in (run.spec.get("env", {}) or {}).items():
            env[str(k)] = str(v)
        return env

    def start_project(self, key, attempt=1):
        spec = self._projects().get(key)
        if spec is None:
            self._emit("core", "error", "unknown project: {0}".format(key), level="error")
            return None
        if not spec.get("enabled", True):
            self._emit(key, "status", "project disabled, skipped", level="warn")
            return None
        with self._lock:
            current = self.runs.get(key)
            if current is not None and current.alive:
                self._emit(key, "status", "already running", level="warn")
                return current
            self.cleanup_orphans([key])
            self._drain_spools([key])
            run = ProjectRun(key, spec, attempt)
            self.runs[key] = run

        try:
            configsync.ensure_links(key, spec)
            sync_results = configsync.sync_in(key, spec)
        except Exception as exc:
            # 配置同步（建 junction / 拷配置）会碰项目目录。项目目录不存在、
            # 盘没插、路径写错时这里会抛 FileNotFoundError——原来是裸调用，
            # 异常直接把 afk-start-<key> 线程打死：没有事件、没有日志、卡片永远
            # 停在「等待 / 等待启动」，用户点「启动」后界面毫无反应，也看不到
            # 任何原因（只能在 stderr 里看到 traceback）。这里转成一次失败的
            # run + 明确错误信息，卡片会显示「已失败」并带上原因。
            run.status = "failed"
            run.exit_code = -4
            run.finished_at = time.time()
            run.last_message = "配置同步失败：{0}".format(exc)
            run.finished_event.set()
            self._emit(key, "error", run.last_message, level="error")
            self._persist()
            return run
        if sync_results:
            self._emit(key, "config", "; ".join(sync_results))

        if spec.get("require_admin") and not is_admin():
            run.status = "failed"
            run.exit_code = -2
            run.finished_at = time.time()
            run.last_message = "需要管理员权限：请使用 Auto-AFK-Admin.bat 以管理员身份启动"
            run.finished_event.set()
            self._emit(key, "error", run.last_message, level="error")
            self._persist()
            return run

        cmd, project = self._build_command(key, spec)
        # manage_emulator 是项目级开关，pre_run.emulator.manage 是全局总闸：
        # 关掉全局就不再接管模拟器生命周期（用户可以自己手动开 MuMu）。
        emu_global = bool(((self.settings.get("pre_run") or {}).get("emulator") or {}).get("manage", True))
        if spec.get("manage_emulator") and emu_global:
            from .emulator import MuMuController

            try:
                result = MuMuController(self.bus, self.settings).ensure_running()
                run.managed_emulator = bool(result.get("started"))
                run.managed_main = bool(result.get("main_started"))
                if not result.get("ready"):
                    run.status = "failed"
                    run.exit_code = -3
                    run.finished_at = time.time()
                    run.last_message = "MuMu emulator not ready"
                    run.finished_event.set()
                    self._emit(key, "error", run.last_message, level="error")
                    self._persist()
                    return run
            except Exception as exc:
                self._emit(key, "error", "emulator setup failed: {0}".format(exc), level="warn")
        env = self._build_env(key, run, project)
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        run.started_at = time.time()
        out_handle = None
        err_handle = None
        try:
            out_handle = open(run.out_log, "wb")
            err_handle = open(run.err_log, "wb")
            run.process = proc_popen(
                cmd,
                cwd=str(project),
                env=env,
                stdout=out_handle,
                stderr=err_handle,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
            )
        except Exception as exc:
            run.status = "failed"
            run.exit_code = -1
            run.finished_at = time.time()
            run.finished_event.set()
            self._emit(key, "error", "failed to start: {0}".format(exc), level="error")
            self._persist()
            return run
        finally:
            for handle in (out_handle, err_handle):
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:
                        pass
        run.status = "running"
        self._emit(key, "status", "started pid={0} attempt={1}".format(run.process.pid, attempt), data={"cmd": cmd})
        self._persist()

        watcher = monitor.RunWatcher(key, run.run_id, spec)
        threading.Thread(target=self._supervise, args=(run, watcher), daemon=True, name="afk-supervise-" + key).start()
        return run

    def _level_for_line(self, line):
        upper = line.upper()
        if "TRACEBACK (MOST RECENT CALL LAST)" in upper:
            return "error"
        if "[WARN" in upper or " WARN " in upper or "警告" in line:
            return "warn"
        return "info"

    def _resolve_log_path(self, project, pattern):
        path = Path(pattern)
        if not path.is_absolute():
            path = project / pattern
        if "*" in str(path) or "?" in str(path):
            try:
                matches = sorted(
                    path.parent.glob(path.name),
                    key=lambda item: item.stat().st_mtime if item.exists() else 0,
                )
            except OSError:
                return None
            return matches[-1] if matches else None
        return path if path.exists() else None

    def _supervise(self, run, watcher):
        key = run.key
        spec = run.spec
        hang_timeout = float(spec.get("hang_timeout", self.settings.get("hang_timeout_sec", 1800)))
        stdout_tailer = FileTailer(run.out_log, start_at_end=False)
        stderr_tailer = FileTailer(run.err_log, start_at_end=False)
        project_dir = config.project_dir(spec)
        extra_tailers = []
        for rel in spec.get("log_files", []) or []:
            resolved = self._resolve_log_path(project_dir, rel)
            extra_tailers.append(
                {
                    "pattern": rel,
                    "resolved": resolved,
                    "tailer": FileTailer(resolved, start_at_end=True) if resolved else None,
                }
            )
        window_keywords = [str(k) for k in (spec.get("window_keywords") or []) if str(k).strip()]
        try:
            window_absent_sec = max(1.0, float(spec.get("window_absent_sec", 15)))
        except (TypeError, ValueError):
            window_absent_sec = 15.0
        window_finish = bool(spec.get("window_finish", False))
        window_absent_since = None
        hang_warned = False
        q = self.bus.subscribe()
        iterations = 0

        def drain(count_activity=True, create_from_start=False):
            for line in stdout_tailer.read_new():
                watcher.feed_text(line, count_activity=count_activity)
                self.bus.emit(
                    {
                        "project": key,
                        "run_id": run.run_id,
                        "kind": "log",
                        "level": self._level_for_line(line),
                        "message": line[:2000],
                        "data": {"source": "stdout"},
                    }
                )
            for line in stderr_tailer.read_new():
                watcher.feed_text(line, count_activity=count_activity)
                self.bus.emit(
                    {
                        "project": key,
                        "run_id": run.run_id,
                        "kind": "log",
                        "level": self._level_for_line(line),
                        "message": line[:2000],
                        "data": {"source": "stderr"},
                    }
                )
            for entry in extra_tailers:
                resolved = self._resolve_log_path(project_dir, entry["pattern"])
                if resolved != entry["resolved"]:
                    entry["resolved"] = resolved
                    # after the process exited, a freshly appeared log file is
                    # read from its beginning (it may only be written at exit)
                    entry["tailer"] = FileTailer(resolved, start_at_end=not create_from_start) if resolved else None
                if entry["tailer"] is not None:
                    new_lines = entry["tailer"].read_new()
                    if new_lines:
                        watcher.feed_text("\n".join(new_lines), count_activity=count_activity)

        def window_seen_now():
            return bool(window_keywords) and self._window_titles_contain(window_keywords)

        def check_window_absent():
            # YukinoChan-style debounce: the game window appeared once during this
            # run and has now been continuously absent for window_absent_sec.
            nonlocal window_absent_since
            if not window_keywords:
                return False
            if window_seen_now():
                run.window_seen = True
                window_absent_since = None
                return False
            if not run.window_seen:
                return False
            if window_absent_since is None:
                window_absent_since = time.time()
                return False
            return time.time() - window_absent_since >= window_absent_sec

        def grace_then_kill(seconds, message, level="warn"):
            # wait before killing but keep draining logs/events so the GUI stays live
            if seconds > 0:
                self._emit(key, "status", message, level=level)
                deadline = time.time() + seconds
                while run.alive and time.time() < deadline:
                    time.sleep(0.5)
                    drain()
            if run.alive:
                self.kill_tree(run.process)

        try:
            while run.alive:
                drained = 0
                try:
                    event = q.get(timeout=1.0)
                    watcher.feed(event)
                    drained += 1
                except queue.Empty:
                    pass
                while drained < 500:
                    try:
                        watcher.feed(q.get_nowait())
                        drained += 1
                    except queue.Empty:
                        break
                iterations += 1
                drain()
                if iterations <= 30 or iterations % 5 == 0:
                    self._track_children(run)

                if window_finish and check_window_absent():
                    run.finished_by_pattern = True
                    watcher.finish_hit = True
                    self._emit(
                        key,
                        "status",
                        "game window gone for {0:.0f}s, treating run as finished".format(window_absent_sec),
                    )
                    grace_then_kill(
                        float(spec.get("stop_on_finish_delay_sec", 5)),
                        "waiting {0:.0f}s before stopping (window finished)",
                    )
                    break
                if spec.get("stop_on_error") and watcher.error_messages and run.alive:
                    delay = float(spec.get("stop_on_error_delay_sec", 10))
                    grace_then_kill(delay, "error pattern detected, waiting {0:.0f}s before stopping".format(delay))
                    break
                if spec.get("stop_on_finish") and watcher.finish_hit and run.alive:
                    run.finished_by_pattern = True
                    delay = float(spec.get("stop_on_finish_delay_sec", 5))
                    grace_then_kill(delay, "finish marker detected, waiting {0:.0f}s before stopping".format(delay))
                    break
                hang_seconds = watcher.hang_seconds()
                if (
                    not hang_warned
                    and run.alive
                    and hang_timeout > 0
                    and hang_seconds >= hang_timeout * 5.0 / 6.0
                    and hang_seconds < hang_timeout
                ):
                    hang_warned = True
                    self._emit(
                        key,
                        "hang",
                        "no activity for {0:.0f}s (warning threshold {1:.0f}s), capturing screenshot".format(
                            hang_seconds, hang_timeout * 5.0 / 6.0
                        ),
                        level="warn",
                    )
                    report.take_screenshot(config.PROJECT_LOGS / "{0}_{1}_hangwarn.png".format(key, run.run_id))
                if hang_seconds > hang_timeout:
                    run.hanging = True
                    self._emit(key, "hang", "no activity for {0}s, killing process tree".format(int(hang_seconds)), level="error")
                    report.take_screenshot(config.PROJECT_LOGS / "{0}_{1}_hang.png".format(key, run.run_id))
                    self.kill_tree(run.process)
                    break
            try:
                run.exit_code = run.process.wait(timeout=30)
            except Exception:
                try:
                    run.exit_code = run.process.poll()
                except Exception:
                    run.exit_code = None
        finally:
            self.bus.unsubscribe(q)

        time.sleep(0.5)
        for event in self.bus.project_events(key, run.run_id):
            watcher.feed(event)
        drain(count_activity=False, create_from_start=True)

        # launcher fast-exit protection (YukinoChan): the process exited but the
        # game window it launched is still up -> wait for the window to close
        # before judging the result, so a fast-exiting launcher is not a failure.
        if (
            window_keywords
            and run.window_seen
            and not run.stopped
            and not run.hanging
            and window_seen_now()
        ):
            self._emit(key, "status", "process exited but game window is still up, waiting for it to close", level="warn")
            try:
                wait_deadline = time.time() + float(spec.get("window_exit_wait_sec", hang_timeout or 1800))
            except (TypeError, ValueError):
                wait_deadline = time.time() + 1800
            absent_since = None
            while time.time() < wait_deadline:
                time.sleep(1.0)
                drain(count_activity=False)
                if window_seen_now():
                    absent_since = None
                    continue
                if absent_since is None:
                    absent_since = time.time()
                elif time.time() - absent_since >= window_absent_sec:
                    watcher.finish_hit = True
                    if window_finish:
                        run.finished_by_pattern = True
                    self._emit(key, "status", "game window closed, resuming result judgment")
                    break
            drain(count_activity=False, create_from_start=True)

        run.finished_at = time.time()
        runtime = (run.finished_at - run.started_at) if run.started_at else None
        if run.finished_by_pattern:
            run.exit_code = 0
        run.status = watcher.classify(
            run.exit_code,
            stopped=run.stopped and not run.finished_by_pattern,
            hang=run.hanging,
            require_finish=bool(spec.get("require_finish", False)),
            min_runtime_sec=float(spec.get("min_runtime_sec", 0) or 0),
            runtime=runtime,
        )
        run.last_message = (watcher.error_text(500) or "").replace("\n", " ")[:500]
        self._emit(
            key,
            "result",
            "run finished: {0} (exit={1}, {2:.0f}s)".format(run.status, run.exit_code, runtime or 0),
            level="info" if run.status in ("success", "success_unverified") else "error",
            data={
                "status": run.status,
                "exit_code": run.exit_code,
                "attempt": run.attempt,
                "fail_reason": watcher.fail_reason,
                "warn_count": watcher.warn_count(),
            },
        )
        self._record_duration(key, run, runtime)
        self._cleanup_processes(run)
        emu_cfg = ((self.settings.get("pre_run") or {}).get("emulator") or {})
        if run.managed_emulator and emu_cfg.get("close_after_run", True):
            from .emulator import MuMuController

            try:
                MuMuController(self.bus, self.settings).stop(close_main=run.managed_main)
            except Exception as exc:
                self._emit(key, "error", "emulator shutdown failed: {0}".format(exc), level="warn")

        try:
            out = configsync.sync_out(key, spec)
            if out:
                self._emit(key, "config", "; ".join(out))
        except Exception as exc:
            logging.getLogger("afk.supervisor").warning("config sync-out failed for %s: %s", key, exc)

        if watcher.warn_count():
            self._emit(
                key,
                "warn",
                "completed with {0} task-level warning(s), first: {1}".format(
                    watcher.warn_count(), watcher.warn_messages[0][:300]
                ),
                level="warn",
                data={"warnings": watcher.warn_messages[-20:]},
            )
            if spec.get("report_on_warn") and run.status == "success":
                report_dir = self._make_report(key, "completed with warnings", run, watcher, None)
                run.report_dir = str(report_dir)
                self._emit(key, "report", "warning report saved: {0}".format(report_dir), level="warn")

        if run.status in ("failed", "hang") and not run.stopped and self.repair is not None:
            error_text = watcher.error_text()
            try:
                outcome = self.repair.handle_failure(key, run, error_text)
            except Exception as exc:
                outcome = {"fixed": False, "attempts": [], "reason": "repair engine error: {0}".format(exc)}
            max_retry = int(spec.get("retry", 0))
            in_chain = self.chain_status == "running"
            chain_budget = (not in_chain) or self._chain_attempts.get(key, 0) < self._chain_max_attempts
            if outcome.get("fixed") and run.attempt <= max_retry and chain_budget:
                # the in-process retry also spends the chain-wide launch budget,
                # so max_attempts_per_project holds no matter which layer retries
                if in_chain:
                    self._chain_attempts[key] = self._chain_attempts.get(key, 0) + 1
                self._emit(key, "repair", "repair reported success, retrying ({0}/{1})".format(run.attempt, max_retry))
                self._persist()
                time.sleep(float(self.settings.get("repair", {}).get("retry_delay_sec", 5)))
                self.start_project(key, attempt=run.attempt + 1)
                return
            reason = "unrecoverable failure: {0}".format(outcome.get("reason", "unknown"))
            report_dir = self._make_report(key, reason, run, watcher, outcome.get("attempts"))
            run.report_dir = str(report_dir)
            self._emit(key, "report", "error report saved: {0}".format(report_dir), level="error")

        if run.status == "success":
            self.notifier.send(
                "Auto-AFK: {0} 完成".format(key),
                "{0} 任务完成 (尝试 {1})".format(key, run.attempt),
                key="success_" + key,
            )
        elif run.status in ("failed", "hang"):
            self.notifier.send(
                "Auto-AFK: {0} 失败".format(key),
                run.last_message or "任务失败，请查看错误报告",
                key="fail_" + key + "_" + str(run.exit_code),
            )

        run.finished_event.set()
        self._persist()

    def _make_report(self, key, reason, run, watcher, repair_attempts):
        error_text = watcher.error_text() if watcher else ""
        signature = report.error_signature(error_text or reason)
        try:
            hours = float(self.settings.get("report_dedupe_hours", 20) or 0)
        except Exception:
            hours = 20.0
        recent = report.find_recent(signature, hours) if hours > 0 else None
        if recent:
            self._emit(key, "report", "same error signature within {0:.0f}h, reusing report: {1}".format(hours, recent), level="warn")
            return recent
        return str(report.create_report(key, reason, run, watcher, self.bus, repair_attempts))

    def _track_children(self, run):
        if not run.process or run.process.poll() is not None:
            return
        try:
            parent = psutil.Process(run.process.pid)
            for child in parent.children(recursive=True):
                run.children.add(child.pid)
        except Exception:
            pass

    def _cleanup_processes(self, run):
        killed = []
        if run.process is not None:
            try:
                dead_pid = run.process.pid
                for proc in psutil.process_iter(["pid", "ppid"]):
                    try:
                        if proc.info.get("ppid") == dead_pid:
                            run.children.add(proc.info["pid"])
                    except Exception:
                        continue
            except Exception:
                pass
        for pid in list(run.children):
            try:
                proc = psutil.Process(pid)
                if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                    continue
                for child in proc.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
                proc.kill()
                killed.append(pid)
            except Exception:
                continue
        run.children.clear()
        for name in run.spec.get("cleanup_processes", []) or []:
            target = str(name).lower()
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    pname = (proc.info.get("name") or "").lower()
                    if pname == target:
                        proc.kill()
                        killed.append(proc.info.get("pid"))
                except Exception:
                    continue
        if killed:
            self._emit(run.key, "cleanup", "cleaned leftover processes: {0}".format(sorted(set(killed))))

    def kill_tree(self, process):
        if process is None:
            return
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except Exception:
                    pass
            try:
                parent.kill()
            except Exception:
                pass
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _window_titles_contain(self, keywords):
        """Match visible top-level window titles against keywords (case-insensitive).

        Borrowed from YukinoChan: the game window is the closest signal to the
        player-visible task state, so title keywords drive completion tracking.
        """
        if not keywords or os.name != "nt":
            return False
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return False
        needles = [str(k).lower() for k in keywords if str(k).strip()]
        if not needles:
            return False
        user32 = ctypes.windll.user32
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        hits = []

        def _callback(hwnd, lparam):
            try:
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = (buf.value or "").lower()
                        if any(needle in title for needle in needles):
                            hits.append(title)
            except Exception:
                pass
            return True

        try:
            user32.EnumWindows(enum_proc(_callback), 0)
        except Exception:
            return False
        return bool(hits)

    def _record_duration(self, key, run, runtime):
        if runtime is None or runtime <= 0:
            return
        try:
            entry = {
                "ts": time.time(),
                "project": key,
                "run_id": run.run_id,
                "attempt": run.attempt,
                "status": run.status,
                "runtime_sec": round(float(runtime), 1),
            }
            config.DURATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(config.DURATIONS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logging.getLogger("afk.supervisor").warning("failed to record run duration: %s", exc)

    def stop_chain_soon(self):
        """Soft stop (YukinoChan-style): running projects finish on their own,
        no new groups are started. Contrast with stop_all which kills everything."""
        self._chain_soft.set()
        self._emit(
            "core",
            "chain",
            "soft stop requested: running projects will finish, no new groups will start",
            level="warn",
        )

    def stop_project(self, key):
        with self._lock:
            run = self.runs.get(key)
        if run is None or not run.alive:
            return False
        run.stopped = True
        self._emit(key, "status", "stop requested", level="warn")
        self.kill_tree(run.process)
        return True

    def stop_all(self):
        self._chain_stop.set()
        self.chain_status = "stopped"
        self.cancel_post_action()
        for key in list(self.runs.keys()):
            self.stop_project(key)
        self._persist()

    def run_startup_updates(self):
        """Update all projects once, right after Auto-AFK starts
        (pre_run.enabled + pre_run.at_startup + pre_run.update_projects).
        The chain's own pre-run skips project updates when this has run."""
        pre_run = self.settings.get("pre_run") or {}
        if not pre_run.get("enabled", True):
            return False
        if not pre_run.get("at_startup", True):
            return False
        if not pre_run.get("update_projects", True):
            return False
        if self.startup_update_started:
            return True
        self.startup_update_started = True

        def _worker():
            try:
                from .updater import Updater

                self._emit("core", "chain", "startup updates: updating all projects")
                Updater(self.bus, self.settings).update_all_projects(
                    should_stop=self._chain_stop.is_set
                )
                self._emit("core", "chain", "startup updates finished")
            except Exception as exc:
                self._emit("core", "chain", "startup updates failed: {0}".format(exc), level="error")
            finally:
                self.startup_update_done.set()

        threading.Thread(target=_worker, daemon=True, name="afk-startup-update").start()
        return True

    def run_chain(self, group_ids=None, on_finish=None):
        if self._chain_thread is not None and self._chain_thread.is_alive():
            self._emit("core", "status", "chain already running", level="warn")
            return False
        if os.environ.get("AFK_DRY_RUN") == "1":
            self._emit("core", "chain", "dry run: chain start skipped (AFK_DRY_RUN=1)", level="warn")
            return True
        self._chain_stop.clear()
        self._chain_soft.clear()
        self._chain_thread = threading.Thread(
            target=self._chain_worker, args=(group_ids, on_finish), daemon=True, name="afk-chain"
        )
        self._chain_thread.start()
        return True

    def _chain_plan(self, groups, chain_order, only_keys=None):
        """Build the [(group, members)] plan for one round.

        only_keys=None -> every enabled member (first round); otherwise only the
        listed keys, so a retry round touches exactly the projects that failed.
        """
        plan = []
        for group in groups:
            members = [
                m
                for m in group.get("members", [])
                if self._projects().get(m, {}).get("enabled", True) and (only_keys is None or m in only_keys)
            ]
            if not members:
                continue
            if chain_order:
                members = apply_chain_order(members, chain_order)
            plan.append((group, members))
        return plan

    def _launch_and_wait(self, members, parallel):
        """Launch the members and block until they settle (or the chain stops)."""
        for member in members:
            self._chain_attempts[member] = self._chain_attempts.get(member, 0) + 1
        # pass the chain-wide attempt number so ProjectRun.attempt reflects how
        # many times the project has really been launched in this chain (a retry
        # round is attempt 2, 3, ...), which is what the report and the repair
        # budget read
        if parallel:
            for member in members:
                self.start_project(member, attempt=self._chain_attempts.get(member, 1))
            while True:
                if self._chain_stop.is_set():
                    return
                runs = [self.runs.get(m) for m in members]
                # a member that never started (unknown key) counts as done so
                # the wait loop cannot spin forever on None
                if all(r is None or r.terminal for r in runs):
                    return
                time.sleep(1.0)
        else:
            for member in members:
                if self._chain_stop.is_set() or self._chain_soft.is_set():
                    return
                self.start_project(member, attempt=self._chain_attempts.get(member, 1))
                while True:
                    if self._chain_stop.is_set():
                        return
                    current = self.runs.get(member)
                    if current is None or current.terminal:
                        break
                    time.sleep(1.0)

    def _chain_worker(self, group_ids, on_finish):
        groups = self._groups()
        if group_ids:
            groups = [g for g in groups if g.get("id") in group_ids]
        self.chain_status = "running"
        self._emit("core", "chain", "chain started: {0}".format([g.get("id") for g in groups]))
        self._persist()
        self.cleanup_orphans()
        if self.repair is not None and hasattr(self.repair, "reset"):
            self.repair.reset()

        pre_run = self.settings.get("pre_run") or {}
        if pre_run.get("enabled", True):
            from .updater import Updater

            updater = Updater(self.bus, self.settings)
            self._emit("core", "chain", "pre-run updates started")
            if pre_run.get("update_emulator", True):
                from .emulator import MuMuController

                MuMuController(self.bus, self.settings).update_check()
            if self.startup_update_started and pre_run.get("at_startup", True):
                # projects were already updated right after startup; just wait
                # for that run to finish instead of updating everything again
                self._emit("core", "chain", "projects updated at startup, waiting for it to finish")
                while not self.startup_update_done.wait(timeout=2.0):
                    if self._chain_stop.is_set():
                        break
            elif pre_run.get("update_projects", True) and not self._chain_stop.is_set():
                updater.update_all_projects(should_stop=self._chain_stop.is_set)
            self._emit("core", "chain", "pre-run updates finished")
            if self._chain_stop.is_set():
                self.chain_status = "stopped"
                self._persist()
                if on_finish:
                    try:
                        on_finish(self.chain_status)
                    except Exception as exc:
                        logging.getLogger("afk.supervisor").warning("on_finish callback failed: %s", exc)
                return
        chain_cfg = self.settings.get("chain") or {}
        chain_order = [str(k) for k in (chain_cfg.get("order") or [])]
        # A failing project no longer aborts the whole chain: it is skipped so the
        # remaining projects still run, and retried once every project has had its
        # turn. Each project is launched at most max_attempts_per_project times in
        # total across all rounds (the accounting is on launches, not on the
        # in-process retry counter, which stays independent).
        continue_on_failure = bool(chain_cfg.get("continue_on_failure", True))
        retry_failed_at_end = bool(chain_cfg.get("retry_failed_at_end", True))
        try:
            cap = int(chain_cfg.get("max_attempts_per_project", 3))
        except (TypeError, ValueError):
            cap = 3
        cap = max(1, cap)
        self._chain_attempts = {}
        self._chain_max_attempts = cap

        overall_ok = True
        aborted = False
        failed_keys = []
        for round_index in range(1, cap + 1):
            if self._chain_stop.is_set() or self._chain_soft.is_set():
                break
            if round_index == 1:
                plan = self._chain_plan(groups, chain_order, None)
            else:
                if not retry_failed_at_end:
                    break
                plan = self._chain_plan(groups, chain_order, failed_keys)
                if not plan:
                    break
                self._emit(
                    "core",
                    "chain",
                    "retry round {0}/{1}: re-running the project(s) that failed: {2}".format(
                        round_index - 1, cap - 1, failed_keys
                    ),
                )
            if not plan:
                break
            round_failed = []
            for group, members in plan:
                if self._chain_stop.is_set() or self._chain_soft.is_set() or aborted:
                    break
                runnable = [m for m in members if self._chain_attempts.get(m, 0) < cap]
                capped = [m for m in members if m not in runnable]
                if capped:
                    self._emit(
                        "core",
                        "chain",
                        "attempt cap reached ({0} run(s) each), not retrying: {1}".format(cap, capped),
                        level="warn",
                    )
                if not runnable:
                    continue
                parallel = bool(group.get("parallel", True))
                self._emit("core", "chain", "group {0} started: {1} (parallel={2})".format(group.get("id"), runnable, parallel))
                self._launch_and_wait(runnable, parallel)
                if self._chain_stop.is_set():
                    break
                failed = [
                    m
                    for m in runnable
                    if (self.runs.get(m) is None or self.runs[m].status not in ("success", "success_unverified", "stopped"))
                ]
                if failed:
                    round_failed.extend(failed)
                    self._emit("core", "chain", "group {0} finished with failures: {1}".format(group.get("id"), failed), level="error")
                    if not continue_on_failure and not group.get("continue_on_failure", False):
                        self._emit(
                            "core",
                            "chain",
                            "chain aborted: group {0} failed and continue_on_failure is off".format(group.get("id")),
                            level="warn",
                        )
                        aborted = True
                        break
                else:
                    self._emit("core", "chain", "group {0} finished ok".format(group.get("id")))
            failed_keys = round_failed
            if self._chain_stop.is_set() or self._chain_soft.is_set() or aborted:
                break
            if not failed_keys:
                break
        else:
            if failed_keys:
                self._emit(
                    "core",
                    "chain",
                    "giving up on {0}: attempt cap of {1} run(s) each was reached".format(failed_keys, cap),
                    level="error",
                )
        stopped = self._chain_stop.is_set() or self._chain_soft.is_set()
        # 先算出结果，但**暂不公布** chain_status：它在本次收尾（写完成标记、持久化、
        # 后置动作、on_finish）全部做完后才赋值，作为「整条链彻底结束」的唯一信号。
        # 否则任何「看到终态就去读完成标记 / 切换数据目录」的代码都会踩到中间窗口
        # （实测：测试在此时 restore() 隔离目录，会让完成标记写进用户真实 logs）。
        if stopped or aborted:
            final_status = "stopped" if stopped else "failed"
            overall_ok = False
        else:
            overall_ok = not failed_keys
            final_status = "success" if overall_ok else "failed"
        if final_status == "success":
            try:
                from . import completion

                completion.mark_chain_success(
                    projects=[key for key, run in self.runs.items() if run.status == "success"]
                )
            except Exception as exc:
                self._emit(
                    "core",
                    "idle",
                    "failed to record daily completion: {0}".format(exc),
                    level="warn",
                )
            else:
                self._emit("core", "idle", "today's chain completed; idle auto-run will skip")
        self.chain_status = final_status
        self._emit(
            "core",
            "chain",
            "chain finished: {0}".format(self.chain_status),
            level="info" if overall_ok else "error",
        )
        self._persist()
        if overall_ok and not self._chain_stop.is_set() and not self._chain_soft.is_set():
            self._schedule_post_action()
        if on_finish:
            try:
                on_finish(self.chain_status)
            except Exception as exc:
                logging.getLogger("afk.supervisor").warning("on_finish callback failed: %s", exc)

    def _schedule_post_action(self):
        action = (self.settings.get("post_actions") or {}).get("after_all", "none")
        if action in (None, "", "none"):
            return
        delay = float((self.settings.get("post_actions") or {}).get("delay_sec", 60))
        self._emit("core", "post_action", "post action '{0}' scheduled in {1}s".format(action, int(delay)))
        self._post_action_timer = threading.Timer(delay, self._run_post_action, args=(action,))
        self._post_action_timer.daemon = True
        self._post_action_timer.start()

    def cancel_post_action(self):
        if self._post_action_timer is not None:
            self._post_action_timer.cancel()
            self._post_action_timer = None
            self._emit("core", "post_action", "post action cancelled", level="warn")

    def _run_post_action(self, action):
        try:
            if action == "shutdown":
                proc_run(["shutdown", "/s", "/t", "0"], capture_output=True)
            elif action == "sleep":
                proc_run(["powershell", "-NoProfile", "-Command", "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"], capture_output=True)
            elif action == "hibernate":
                proc_run(["shutdown", "/h"], capture_output=True)
            elif action == "exit":
                # 关闭 Auto-AFK 本身：GUI 侧监听该事件后退出
                self._emit("core", "post_action", "post action executed: exit", data={"action": "exit"})
                return
            self._emit("core", "post_action", "post action executed: {0}".format(action))
        except Exception as exc:
            self._emit("core", "post_action", "post action failed: {0}".format(exc), level="error")
