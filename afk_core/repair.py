import re
import shutil
from pathlib import Path

from . import config
from .proc import popen as proc_popen, run as proc_run


class RepairEngine:
    def __init__(self, bus, settings=None, approve=None):
        self.bus = bus
        self.settings = settings or {}
        self.rules = (config.repair_rules() or {}).get("rules", [])
        self.approve = approve
        self.attempt_counts = {}

    def reset(self):
        self.attempt_counts = {}

    def reload(self, settings=None):
        """重新读取 settings 与 repair_rules.yaml。

        历史缺陷：两者都只在构造时读一次。用户在「修复规则」tab 里加了一条
        规则并保存，文件里有 11 条、引擎里仍是开窗时那 10 条 —— 新规则永远
        不生效，只能重启程序；同理在「设置」里关掉 repair.enabled 也不生效，
        挂机时照样按旧配置重试。
        """
        if settings is not None:
            self.settings = settings
        self.rules = (config.repair_rules() or {}).get("rules", [])
        self.attempt_counts = {}
        return self.rules

    def _emit(self, project, message, level="warn", data=None):
        self.bus.emit(
            {
                "project": project,
                "kind": "repair",
                "level": level,
                "message": message,
                "data": data or {},
            }
        )

    def handle_failure(self, project, run, text):
        attempts = []
        if not self.settings.get("repair", {}).get("enabled", True):
            return {"fixed": False, "attempts": attempts, "reason": "repair disabled"}
        max_rounds = int(self.settings.get("repair", {}).get("max_rounds", 2))
        auto_unsafe = bool(self.settings.get("repair", {}).get("auto_unsafe", False))
        text = text or ""
        for rule in self.rules:
            match = rule.get("match")
            if not match:
                continue
            if rule.get("project") and rule["project"] not in ("*", project):
                continue
            m = re.search(match, text, re.IGNORECASE | re.MULTILINE)
            if not m:
                continue
            rule_id = rule.get("id", match[:40])
            max_attempts = int(rule.get("max_attempts", 1))
            key = (project, rule_id)
            used = self.attempt_counts.get(key, 0)
            if used >= max_attempts:
                self._emit(project, "repair rule {0} exhausted ({1}/{2})".format(rule_id, used, max_attempts))
                continue
            self.attempt_counts[key] = used + 1
            self._emit(project, "repair rule matched: {0}".format(rule_id), data={"rule": rule_id, "groups": list(m.groups())})
            rule_ok = True
            for action in rule.get("actions", []):
                safe = bool(action.get("safe", False))
                if not safe and not auto_unsafe:
                    if self.approve is None or not self.approve(project, rule_id, action):
                        attempts.append({"rule": rule_id, "action": action.get("type"), "status": "skipped-unsafe"})
                        self._emit(project, "unsafe action skipped (approval required): {0}".format(action.get("type")))
                        rule_ok = False
                        continue
                result = self._run_action(project, action, m)
                attempts.append({"rule": rule_id, "action": action.get("type"), "status": "ok" if result[0] else "failed", "detail": result[1]})
                self._emit(
                    project,
                    "repair action {0}: {1}".format(action.get("type"), "ok" if result[0] else "failed"),
                    level="warn" if result[0] else "error",
                    data={"detail": result[1]},
                )
                if not result[0]:
                    rule_ok = False
            if rule_ok:
                return {"fixed": True, "attempts": attempts, "reason": rule_id, "max_rounds": max_rounds}
        return {"fixed": False, "attempts": attempts, "reason": "no repair rule matched"}

    def _run_action(self, project, action, match):
        action_type = action.get("type")
        handler = getattr(self, "_action_" + action_type, None)
        if handler is None:
            return False, "unknown action type: {0}".format(action_type)
        try:
            return handler(project, action, match)
        except Exception as exc:
            return False, "action error: {0}".format(exc)

    def _project_path(self, project):
        spec = (config.registry().get("projects", {}) or {}).get(project, {})
        return config.project_dir(spec)

    def _venv_python(self, project):
        spec = (config.registry().get("projects", {}) or {}).get(project, {})
        python = spec.get("python", ".venv\\Scripts\\python.exe")
        return self._project_path(project) / python

    def _capture_value(self, action, match, default=""):
        pattern = action.get("package") or action.get("value") or default
        if match and isinstance(pattern, str) and "$" in pattern:
            template = re.sub(r"\$(\d+)", r"\\g<\1>", pattern)
            try:
                return match.expand(template)
            except Exception:
                return pattern
        if isinstance(pattern, str):
            return pattern
        return default

    def _action_pip_install(self, project, action, match):
        package = self._capture_value(action, match)
        if not package:
            return False, "no package captured"
        python = self._venv_python(project)
        if not python.exists():
            return False, "venv python missing: {0}".format(python)
        cmd = ["uv", "pip", "install", "--python", str(python), package]
        proc = proc_run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=600)
        if proc.returncode != 0:
            mirror = action.get("mirror", "https://pypi.tuna.tsinghua.edu.cn/simple")
            cmd = ["uv", "pip", "install", "--python", str(python), "-i", mirror, package]
            proc2 = proc_run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=600)
            return proc2.returncode == 0, "{0}\n{1}".format(proc.stderr[-500:], proc2.stderr[-500:])
        return True, "installed {0}".format(package)

    def _action_submodule_update(self, project, action, match):
        cwd = self._project_path(project)
        proc = proc_run(["git", "submodule", "update", "--init", "--recursive"], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=1800)
        return proc.returncode == 0, proc.stdout[-500:] + proc.stderr[-500:]

    def _action_adb_restart(self, project, action, match):
        proc_run(["adb", "kill-server"], capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
        proc = proc_run(["adb", "start-server"], capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
        return proc.returncode == 0, proc.stderr[-300:]

    def _action_start_process(self, project, action, match):
        target = self._capture_value(action, match)
        if not target:
            return False, "no path configured"
        path = Path(target)
        if not path.exists():
            return False, "path not found: {0}".format(path)
        proc_popen([str(path)], close_fds=True)
        return True, "started {0}".format(path)

    def _action_run_script(self, project, action, match):
        script = self._capture_value(action, match)
        path = Path(script)
        if not path.exists():
            return False, "script not found: {0}".format(path)
        proc = proc_run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)], capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=3600)
        return proc.returncode == 0, (proc.stdout[-500:] + proc.stderr[-500:])

    def _action_restore_config(self, project, action, match):
        from . import configsync

        spec = (config.registry().get("projects", {}) or {}).get(project, {})
        results = configsync.restore(project, spec)
        return True, "; ".join(results) if results else "nothing to restore"

    def _action_rerun(self, project, action, match):
        return True, "rerun requested"

    def _action_git_pull(self, project, action, match):
        cwd = self._project_path(project)
        proc = proc_run(["git", "pull", "--ff-only"], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=900)
        if proc.returncode != 0:
            return False, proc.stderr[-500:]
        sub = proc_run(["git", "submodule", "update", "--init", "--recursive"], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=1800)
        return sub.returncode == 0, (proc.stdout[-300:] + sub.stderr[-300:])

    def _action_clear_cache(self, project, action, match):
        spec = (config.registry().get("projects", {}) or {}).get(project, {})
        target = self._capture_value(action, match, spec.get("cache_dir", ""))
        if not target:
            return False, "no cache dir configured"
        path = Path(target)
        if not path.exists():
            return True, "cache dir absent"
        shutil.rmtree(path, ignore_errors=True)
        return True, "cleared {0}".format(path)
