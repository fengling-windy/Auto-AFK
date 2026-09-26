import codecs
import json
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
                raw = f.read()
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
            "pid": self.process.pid if self.process else None,
        }


class Supervisor:
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
        self._post_action_timer = None
        self._orphan_runs = {}
        self._lock = threading.RLock()
        self.bus.status_provider = self.status_snapshot
        self.bus.command_handler = self._handle_command
        self._restore_state()
        self._drain_spools()

    def _projects(self):
        return self.registry.get("projects", {})

    def _groups(self):
        return self.registry.get("groups", [])

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
            except Exception:
                pass

    def _restore_state(self):
        try:
            if config.STATE_FILE.exists():
                data = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
                self.chain_status = data.get("chain_status", "idle")
                self._orphan_runs = data.get("runs", {}) or {}
        except Exception:
            self.chain_status = "idle"

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
        return {"ok": False, "error": "unknown action"}

    def _drain_spools(self, keys=None):
        try:
            if keys is None:
                candidates = sorted(config.PROJECT_LOGS.glob("*_spool.jsonl"))
            else:
                candidates = [config.PROJECT_LOGS / "{0}_spool.jsonl".format(key) for key in keys]
            for path in candidates:
                self.bus.drain_spool(path)
        except Exception:
            pass

    def cleanup_orphans(self, keys=None):
        for key in list(self._orphan_runs.keys()) if keys is None else list(keys):
            run = self._orphan_runs.get(key)
            if not run:
                continue
            self._orphan_runs.pop(key, None)
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
        except Exception:
            pass

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
        env["PYTHONUTF8"] = "1"
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

        configsync.ensure_links(key, spec)
        sync_results = configsync.sync_in(key, spec)
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
        if spec.get("manage_emulator"):
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
        q = self.bus.subscribe()
        iterations = 0
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
                for line in stdout_tailer.read_new():
                    watcher.feed_text(line, count_activity=True)
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
                    watcher.feed_text(line, count_activity=True)
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
                        entry["tailer"] = FileTailer(resolved, start_at_end=True) if resolved else None
                    if entry["tailer"] is not None:
                        new_lines = entry["tailer"].read_new()
                        if new_lines:
                            watcher.feed_text("\n".join(new_lines), count_activity=True)
                if iterations <= 30 or iterations % 5 == 0:
                    self._track_children(run)
                if spec.get("stop_on_error") and watcher.error_messages and run.alive:
                    delay = float(spec.get("stop_on_error_delay_sec", 10))
                    if delay > 0:
                        self._emit(key, "status", "error pattern detected, waiting {0:.0f}s before stopping".format(delay), level="warn")
                        deadline = time.time() + delay
                        while run.alive and time.time() < deadline:
                            time.sleep(0.5)
                    if run.alive:
                        self._emit(key, "status", "stopping process after error pattern", level="warn")
                        self.kill_tree(run.process)
                    break
                if spec.get("stop_on_finish") and watcher.finish_hit and run.alive:
                    delay = float(spec.get("stop_on_finish_delay_sec", 5))
                    if delay > 0:
                        self._emit(key, "status", "finish marker detected, waiting {0:.0f}s before stopping".format(delay))
                        deadline = time.time() + delay
                        while run.alive and time.time() < deadline:
                            time.sleep(0.5)
                    run.finished_by_pattern = True
                    if run.alive:
                        self._emit(key, "status", "stopping process after finish")
                        self.kill_tree(run.process)
                    break
                if watcher.hang_seconds() > hang_timeout:
                    run.hanging = True
                    self._emit(key, "hang", "no activity for {0}s, killing process tree".format(int(watcher.hang_seconds())), level="error")
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
        for line in stdout_tailer.read_new():
            watcher.feed_text(line)
        for line in stderr_tailer.read_new():
            watcher.feed_text(line)
        for entry in extra_tailers:
            if entry["tailer"] is None:
                resolved = self._resolve_log_path(project_dir, entry["pattern"])
                if resolved:
                    entry["tailer"] = FileTailer(resolved, start_at_end=False)
            if entry["tailer"] is not None:
                new_lines = entry["tailer"].read_new()
                if new_lines:
                    watcher.feed_text("\n".join(new_lines))

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
        except Exception:
            pass

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
            if outcome.get("fixed") and run.attempt <= max_retry:
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

    def run_chain(self, group_ids=None, on_finish=None):
        if self._chain_thread is not None and self._chain_thread.is_alive():
            self._emit("core", "status", "chain already running", level="warn")
            return False
        if os.environ.get("AFK_DRY_RUN") == "1":
            self._emit("core", "chain", "dry run: chain start skipped (AFK_DRY_RUN=1)", level="warn")
            return True
        self._chain_stop.clear()
        self._chain_thread = threading.Thread(
            target=self._chain_worker, args=(group_ids, on_finish), daemon=True, name="afk-chain"
        )
        self._chain_thread.start()
        return True

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
            if pre_run.get("update_projects", True) and not self._chain_stop.is_set():
                updater.update_all_projects(should_stop=self._chain_stop.is_set)
            self._emit("core", "chain", "pre-run updates finished")
            if self._chain_stop.is_set():
                self.chain_status = "stopped"
                self._persist()
                if on_finish:
                    try:
                        on_finish(self.chain_status)
                    except Exception:
                        pass
                return
        overall_ok = True
        for group in groups:
            if self._chain_stop.is_set():
                break
            members = [m for m in group.get("members", []) if self._projects().get(m, {}).get("enabled", True)]
            if not members:
                continue
            parallel = bool(group.get("parallel", True))
            self._emit("core", "chain", "group {0} started: {1} (parallel={2})".format(group.get("id"), members, parallel))
            if parallel:
                for member in members:
                    self.start_project(member)
                while True:
                    if self._chain_stop.is_set():
                        break
                    runs = [self.runs.get(m) for m in members]
                    if all(r is not None and r.terminal for r in runs):
                        break
                    time.sleep(1.0)
            else:
                for member in members:
                    if self._chain_stop.is_set():
                        break
                    self.start_project(member)
                    while True:
                        if self._chain_stop.is_set():
                            break
                        current = self.runs.get(member)
                        if current is not None and current.terminal:
                            break
                        time.sleep(1.0)
            if self._chain_stop.is_set():
                break
            failed = [m for m in members if (self.runs.get(m) is None or self.runs[m].status not in ("success", "success_unverified", "stopped"))]
            if failed:
                overall_ok = False
                self._emit("core", "chain", "group {0} finished with failures: {1}".format(group.get("id"), failed), level="error")
                if not group.get("continue_on_failure", False):
                    break
            self._emit("core", "chain", "group {0} finished ok".format(group.get("id")))
        if self._chain_stop.is_set():
            self.chain_status = "stopped"
        else:
            self.chain_status = "success" if overall_ok else "failed"
        self._emit(
            "core",
            "chain",
            "chain finished: {0}".format(self.chain_status),
            level="info" if overall_ok else "error",
        )
        self._persist()
        if overall_ok and not self._chain_stop.is_set():
            self._schedule_post_action()
        if on_finish:
            try:
                on_finish(self.chain_status)
            except Exception:
                pass

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
            self._emit("core", "post_action", "post action executed: {0}".format(action))
        except Exception as exc:
            self._emit("core", "post_action", "post action failed: {0}".format(exc), level="error")
