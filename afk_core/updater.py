import importlib.util
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config
from .numval import as_int
from .proc import host_env, run as proc_run


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
        # 更新钩子会跑子项目自己的脚本（其中也有 Qt 程序）；父进程带的
        # QT_QPA_PLATFORM=offscreen 会让它们 qFatal → 0xC0000409，先摘掉。
        env = host_env()
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
        code, out = self._run(
            cmd,
            cwd=hook.get("cwd", project),
            timeout=as_int(
                hook.get("timeout_sec", 1800),
                1800,
                low=1,
                what="update_hooks.timeout_sec",
            ),
        )
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
            # syntax-broken 也在这里：补丁把文件写成语法错误时 cmd_apply 已经
            # 拒绝落盘（原文件保留），但必须让用户知道这次补丁没生效，
            # 否则项目会带着「旧补丁」继续跑，问题在下一次启动才炸。
            fatal = [s for s in states if str(s).startswith("error")
                     or s in ("anchor-missing", "file-missing", "syntax-broken")]
            if fatal:
                self._emit(key, "patch re-apply has issues: {0}".format(states), level="error")
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
        """并行更新所有启用的项目（不再逐个等待）。

        为什么要并行：每个项目的更新包含 git pull（可能要等远端）、子模块拉取、
        以及可能的构建钩子（MaaEnd 的 build-and-install 动辄数分钟）。串行执行时
        总耗时是所有项目之和；这些项目各自有独立的 git 工作树、独立的
        `.afk_monitor_manifest.json`，互相不写对方的文件，因此可以安全并行，
        总耗时降到最慢的那个项目。

        并发度由 `pre_run.update_concurrency` 控制：
          * 0（默认）= 不限，一次全部放出去；
          * N>0       = 最多同时 N 个（对磁盘/网络敏感的场景可调小）。

        返回值按注册表顺序排列，保证调用方看到的顺序与过去一致。
        """
        projects = [
            (key, spec)
            for key, spec in (config.registry().get("projects", {}) or {}).items()
            if spec.get("enabled", True)
        ]
        if not projects:
            return []

        workers = self._concurrency(len(projects))
        self._emit(
            "core",
            "update: updating {0} project(s) in parallel (concurrency={1})".format(
                len(projects), "unlimited" if workers >= len(projects) else workers
            ),
        )

        results = {}
        stopped = threading.Event()

        def _stop_requested():
            if should_stop is not None and should_stop():
                if not stopped.is_set():
                    stopped.set()
                    self._emit("core", "pre-run update stopped by user", level="warn")
                return True
            return False

        def _work(item):
            key, spec = item
            if stopped.is_set() or _stop_requested():
                # 返回 None 表示「本轮没有真正处理这个项目」：与旧的串行实现
                # （should_stop 后直接 break）保持一致——结果里不应出现未处理的项目。
                return key, None
            try:
                return key, self.update_project(key, spec)
            except Exception as exc:
                self._emit(key, "update crashed: {0}".format(exc), level="error")
                return key, {"project": key, "updated": False, "steps": [str(exc)]}

        # 先全部提交，再收集：这样即使某个项目很慢，其它项目也已经在同时跑
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="afk-update") as pool:
            futures = {pool.submit(_work, item): item[0] for item in projects}
            for future in as_completed(futures):
                key, result = future.result()
                if result is not None:
                    results[key] = result

        return [results[key] for key, _spec in projects if key in results]

    def _concurrency(self, total):
        """并发度：0 或非法值 -> 全部并行；否则限制在 1..total。"""
        try:
            value = as_int(
                (self.settings.get("pre_run") or {}).get("update_concurrency", 0),
                0,
                low=0,
                what="pre_run.update_concurrency",
            )
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            return max(1, total)
        return max(1, min(value, total))
