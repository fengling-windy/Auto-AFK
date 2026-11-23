import importlib.util
import re
import shutil
from pathlib import Path

from . import config
from .numval import as_int
from .proc import host_env, popen as proc_popen, run as proc_run


class RepairEngine:
    def __init__(self, bus, settings=None, approve=None, patcher=None, patcher_root=None):
        self.bus = bus
        self.settings = settings or {}
        self.rules = (config.repair_rules() or {}).get("rules", [])
        self.approve = approve
        self.attempt_counts = {}
        # 补丁模块与它使用的 root 是注入点：正常运行时都是 None（现加载 +
        # config.ROOT），测试可以传自己的模块与临时根目录，避免为了造故障去改
        # config.MONITOR_KIT / config.ROOT 这种真实交付路径上的全局量。
        self.patcher = patcher
        self.patcher_root = patcher_root

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
        max_rounds = as_int(
            (self.settings.get("repair") or {}).get("max_rounds", 2),
            2,
            low=0,
            what="repair.max_rounds",
        )
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
            max_attempts = as_int(
                rule.get("max_attempts", 1),
                1,
                low=1,
                what="repair_rules.{0}.max_attempts".format(rule_id),
            )
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
        proc_popen([str(path)], close_fds=True, env=host_env())
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

    def _patcher(self):
        if self.patcher is not None:
            return self.patcher
        spec = importlib.util.spec_from_file_location("afk_patcher_repair", config.MONITOR_KIT / "patcher.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _patch_root(self):
        return Path(self.patcher_root) if self.patcher_root is not None else config.ROOT

    def _git(self, project, args, timeout=120):
        return proc_run(
            ["git", "-c", "credential.helper=", "-c", "http.https://github.com/.extraheader="] + list(args),
            cwd=str(project),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
        )

    @staticmethod
    def _compile_error(path):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return "read failed: {0}".format(exc)
        try:
            compile(text, str(path), "exec")
        except SyntaxError as exc:
            return "{0}: {1} (line {2})".format(type(exc).__name__, exc.msg, exc.lineno)
        except ValueError as exc:
            return str(exc)
        return None

    def _broken_marked_files(self, project):
        """找出「被补丁改过、而且现在编译不过」的 py 文件。

        只认 git 里已跟踪、且内容里带 AFK-MONITOR 标记的文件：前者保证
        `git checkout --` 能把它换回上游原样，后者保证这确实是我们的补丁
        造成的（不是用户自己写坏的、也不是上游自己坏的）。
        """
        proc = self._git(project, ["status", "--porcelain"])
        if proc.returncode != 0:
            return [], "git status failed: {0}".format((proc.stderr or "").strip()[:200])
        broken = []
        for line in (proc.stdout or "").splitlines():
            if len(line) < 4:
                continue
            code = line[:2]
            # 只处理「已跟踪且被修改」，未跟踪(??)/删除(D) 都跳过
            if code.strip() != "M":
                continue
            rel = line[3:].strip().strip('"')
            if not rel.lower().endswith(".py"):
                continue
            path = project / rel
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "AFK-MONITOR" not in text:
                continue
            error = self._compile_error(path)
            if error:
                broken.append((rel, error))
        return broken, ""

    def _action_restore_patch_source(self, project, action, match):
        """把被补丁写坏的源文件换回上游原样，再按当前定义重打补丁。

        历史缺陷：2026-11-06 那次 `BAAH/main.py` 落成「打好补丁的内容 + 一段
        残留旧尾巴」→ `IndentationError: unexpected indent`，项目再也起不来。
        修复引擎 10 条规则一条都匹配不上（全是 pip / git / adb 类动作），只能
        记成 `unrecoverable failure: no repair rule matched`，用户得自己进项目
        目录 `git checkout -- main.py`。语法故障恰恰是补丁引擎自己造成的，
        应该能自愈。

        为什么不能只 revert 补丁块：损坏形态是「正确内容 + 额外追加的垃圾」，
        删掉标记块垃圾还在，文件依然编译不过。只有 git checkout 能把文件拉回
        上游原样，然后再 apply 一次就是干净的补丁态。
        """
        spec = (config.registry().get("projects", {}) or {}).get(project, {})
        project_dir = config.project_dir(spec) if spec else None
        try:
            module = self._patcher()
        except Exception as exc:
            return False, "补丁模块加载失败：{0}".format(exc)
        # 项目目录以补丁定义里的 path 为准：真正被写文件的是它，不是注册表里
        # 可能在别的层级的 path（例如 ZZZ 用的是绝对路径）。
        patch_spec = (getattr(module, "PATCHES", {}) or {}).get(project) or {}
        if patch_spec.get("path"):
            project_dir = self._patch_root() / patch_spec["path"]
        if project_dir is None or not Path(project_dir).exists():
            return False, "找不到项目目录：{0}".format(project_dir)
        project_dir = Path(project_dir)
        if not (project_dir / ".git").exists():
            return False, "项目目录不是 git 仓库，无法还原源文件：{0}".format(project_dir)
        broken, error = self._broken_marked_files(project_dir)
        if error:
            return False, error
        if not broken:
            return False, "没有找到被补丁写坏（编译不过且带 AFK-MONITOR 标记）的 py 文件"
        restored = []
        for rel, _detail in broken:
            proc = self._git(project_dir, ["checkout", "--", rel])
            if proc.returncode != 0:
                return False, "git checkout -- {0} 失败：{1}".format(rel, (proc.stderr or "").strip()[:300])
            still = self._compile_error(project_dir / rel)
            if still:
                return False, "{0} 还原到上游后仍编译不过（上游自己就是坏的）：{1}".format(rel, still)
            restored.append(rel)
        try:
            applied = module.cmd_apply(self._patch_root(), [project]).get(project, [])
        except Exception as exc:
            return False, "还原成功但重新打补丁失败：{0}".format(exc)
        problems = [
            (item, state)
            for item, state in applied
            if str(state).startswith("error") or state in ("anchor-missing", "file-missing", "syntax-broken")
        ]
        if problems:
            return False, "还原成功但重新打补丁有问题：{0}".format(problems)
        return True, "restored {0} and re-applied patches".format(", ".join(restored))
