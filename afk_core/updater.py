import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import config
from .proc import popen as proc_popen, run as proc_run


class Updater:
    def __init__(self, bus, settings=None):
        self.bus = bus
        self.settings = settings or config.settings()

    def _emit(self, project, message, level="info", data=None):
        self.bus.emit(
            {
                "project": project,
                "kind": "update",
                "level": level,
                "message": message,
                "data": data or {},
            }
        )

    def _env(self):
        env = os.environ.copy()
        extra = []
        for candidate in (Path(r"C:\Program Files\Go\bin"), Path(os.environ.get("APPDATA", "")) / "npm"):
            try:
                if candidate.exists():
                    extra.append(str(candidate))
            except OSError:
                continue
        if extra:
            env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "never"
        return env

    def _run(self, cmd, cwd=None, timeout=900):
        try:
            proc = proc_run(
                [str(item) for item in cmd],
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout,
                env=self._env(),
            )
            return proc.returncode, ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
        except subprocess.TimeoutExpired:
            return -1, "timeout after {0}s".format(timeout)
        except Exception as exc:
            return -1, str(exc)

    def _patcher(self):
        spec = importlib.util.spec_from_file_location("afk_patcher", config.MONITOR_KIT / "patcher.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _git(self, project, args, timeout=900):
        return self._run(
            ["git", "-c", "credential.helper=", "-c", "http.https://github.com/.extraheader="] + list(args),
            cwd=project,
            timeout=timeout,
        )

    def _head(self, project):
        code, out = self._git(project, ["rev-parse", "HEAD"], timeout=60)
        return out.strip() if code == 0 else ""

    def _changed_files(self, project, before, after):
        if not before or not after or before == after:
            return []
        code, out = self._git(project, ["diff", "--name-only", before, after], timeout=120)
        if code != 0:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    def _matches(self, files, patterns):
        if not patterns:
            return True
        for name in files:
            for pattern in patterns:
                if name.startswith(pattern) or Path(name).match(pattern):
                    return True
        return False

    def _hook_command(self, key, spec, hook, project):
        cmd = [str(item).format(project=str(project), root=str(config.ROOT)) for item in hook.get("cmd", [])]
        if not cmd:
            return False, "empty command"
        label = hook.get("label", " ".join(cmd[:3]))
        self._emit(key, "update hook: {0}".format(label))
        code, out = self._run(cmd, cwd=hook.get("cwd", project), timeout=int(hook.get("timeout_sec", 1800)))
        if code == 0:
            self._emit(key, "update hook ok: {0}".format(label))
        else:
            self._emit(key, "update hook failed ({0}): {1}".format(code, out[-500:]), level="warn")
        return code == 0, out

    def _hook_copy_tree(self, key, spec, hook, project):
        src = Path(str(hook.get("src", "")).format(project=str(project), root=str(config.ROOT)))
        dst = Path(str(hook.get("dst", "")).format(project=str(project), root=str(config.ROOT)))
        if hook.get("only_if_missing") and dst.exists():
            return True, "already present"
        if not src.exists():
            self._emit(key, "copy_tree source missing: {0}".format(src), level="warn")
            return False, "source missing"
        shutil.copytree(src, dst, dirs_exist_ok=True)
        self._emit(key, "copied {0} -> {1}".format(src, dst))
        return True, "copied"

    def _hook_baah_deps(self, key, spec, project):
        req = project / "requirements.txt"
        core = project / "requirements-core.txt"
        if not req.exists():
            return False, "requirements.txt missing"
        raw = req.read_text(encoding="utf-8", errors="ignore")
        import re

        match = re.search(r"(?s)###CORE###(.*?)###CORE_END###", raw)
        if not match:
            return False, "CORE section missing"
        core.write_text(match.group(1).strip() + "\r\n", encoding="utf-8")
        python = project / spec.get("python", ".venv\\Scripts\\python.exe")
        code, out = self._run(["uv", "pip", "install", "--python", str(python), "-r", str(core)], cwd=project, timeout=1800)
        if code != 0:
            self._emit(key, "baah deps install failed: {0}".format(out[-400:]), level="warn")
        return code == 0, out

    def update_project(self, key, spec=None):
        spec = spec or (config.registry().get("projects", {}) or {}).get(key, {})
        project = config.project_dir(spec)
        result = {"project": key, "updated": False, "steps": []}
        if not project.exists():
            self._emit(key, "project dir missing, skip update", level="warn")
            return result
        if not (project / ".git").exists():
            result["steps"].append("not a git repo, skipped")
            return result

        self._emit(key, "pre-run update: fetching {0}".format(key))
        patcher = self._patcher()
        try:
            patcher.cmd_revert(config.ROOT, [key])
        except Exception as exc:
            self._emit(key, "patch revert before update failed: {0}".format(exc), level="warn")
        before = self._head(project)
        code, out = self._git(project, ["pull", "--ff-only"], timeout=900)
        if code != 0 and "no tracking information" in out:
            branch_code, branch = self._git(project, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=60)
            branch = branch.strip()
            if branch_code == 0 and branch:
                code, out = self._git(project, ["pull", "--ff-only", "origin", branch], timeout=900)
        if code != 0:
            self._emit(key, "git pull failed (kept local state): {0}".format(out[-300:]), level="warn")
        if spec.get("update_submodules", True):
            self._git(project, ["submodule", "update", "--init", "--recursive"], timeout=1800)
        after = self._head(project)
        result["updated"] = bool(before and after and before != after)
        changed = self._changed_files(project, before, after) if result["updated"] else []
        if changed:
            self._emit(key, "updated to {0}, {1} file(s) changed".format(after[:8], len(changed)), data={"changed": changed[:50]})
        else:
            self._emit(key, "already up to date ({0})".format((after or before)[:8]))

        try:
            apply_report = patcher.cmd_apply(config.ROOT, [key])
            states = [state for _, state in apply_report.get(key, [])]
            if any(str(state).startswith("error") or state in ("anchor-missing", "file-missing") for state in states):
                self._emit(key, "patch re-apply has issues: {0}".format(states), level="warn")
        except Exception as exc:
            self._emit(key, "patch re-apply failed: {0}".format(exc), level="error")

        for hook in spec.get("update_hooks", []) or []:
            when = hook.get("when", "changed")
            if when == "changed" and not result["updated"]:
                continue
            if when == "changed" and hook.get("match_files") and not self._matches(changed, hook["match_files"]):
                continue
            hook_type = hook.get("type", "command")
            if hook_type == "command":
                ok, _ = self._hook_command(key, spec, hook, project)
            elif hook_type == "copy_tree":
                ok, _ = self._hook_copy_tree(key, spec, hook, project)
            elif hook_type == "baah_deps":
                ok, _ = self._hook_baah_deps(key, spec, project)
            else:
                ok = False
                self._emit(key, "unknown hook type: {0}".format(hook_type), level="warn")
            result["steps"].append({hook.get("label", hook_type): "ok" if ok else "failed"})
        return result

    def update_all_projects(self, should_stop=None):
        results = []
        for key, spec in (config.registry().get("projects", {}) or {}).items():
            if should_stop is not None and should_stop():
                self._emit("core", "pre-run update stopped by user", level="warn")
                break
            if not spec.get("enabled", True):
                continue
            try:
                results.append(self.update_project(key, spec))
            except Exception as exc:
                self._emit(key, "update crashed: {0}".format(exc), level="error")
                results.append({"project": key, "updated": False, "steps": [str(exc)]})
        return results
