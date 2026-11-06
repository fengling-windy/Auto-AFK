"""修复引擎的「补丁把源文件写坏」自愈能力。

真实事故：2026-11-06 07:55 那次链启动，`BAAH/main.py` 被补丁引擎写成
「打好补丁的正确内容 + 一段残留的旧 `re_process` 尾巴」，落成

    File "E:\\GameAFK\\Auto-AFK\\BAAH\\main.py", line 52
      BAAH_core_process()
    IndentationError: unexpected indent

项目再也起不来。而 `afk_core/repair.py` 当时 10 条规则全是 pip / git / adb /
子模块一类，**一条都匹配不上**，最终只能记成

    unrecoverable failure: no repair rule matched

用户必须自己进项目目录执行 `git checkout -- main.py` 才能恢复。

现在 reqair_rules.yaml 第一条 `patch-syntax-broken` 匹配
`(IndentationError|TabError|SyntaxError)`，动作 `restore_patch_source`：

1. 用 `git status --porcelain` 找出「已跟踪 + 被修改 + 文件里有 AFK-MONITOR
   标记 + 现在编译不过」的 .py 文件 —— 四条同时成立才动它，避免误伤用户
   自己写坏的代码或未跟踪文件；
2. 对这些文件执行 `git checkout -- <rel>`，把它们换回上游原样；
3. 再按当前补丁定义 `cmd_apply` 一遍，让项目回到「干净的补丁态」。

判据：
1. 被补丁写坏的文件能被识别并还原，还原后能编译；
2. 还原后补丁块重新在位（不是裸着上游文件）；
3. 没有损坏文件时明确失败，不会乱 checkout；
4. 未跟踪文件、非 .py 文件、不带标记的文件都不在还原范围内。
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  <- " + str(detail)))
    if not ok:
        FAILED.append(name)


def _patcher():
    spec = importlib.util.spec_from_file_location("afk_patcher_smoke_repair", ROOT / "monitor_kit" / "patcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLEAN = (
    "import sys\n"
    "\n"
    "\n"
    "def run():\n"
    "    try:\n"
    "        print('work')\n"
    "    except Exception:\n"
    "        print('failed')\n"
)

BLOCK = {
    "id": "smoke-repair-entry",
    "file": "main.py",
    "anchor": "def run():",
    "insert": (
        "    try:\n"
        "        import afk_hook as _afk_hook\n"
        "        _afk_hook.install(project='SmokeRepair')\n"
        "    except Exception:\n"
        "        pass\n"
    ),
}


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=smoke", "-c", "user.email=smoke@example.invalid"] + list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _compiles(path):
    try:
        compile(_read(path), str(path), "exec")
        return True, ""
    except SyntaxError as exc:
        return False, "{0}: {1} (line {2})".format(type(exc).__name__, exc.msg, exc.lineno)


def main():
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_repairpatch_")
    try:
        return _run()
    finally:
        guard.restore()


def _run():
    from afk_core.events import EventBus
    from afk_core.repair import RepairEngine

    tmp = Path(tempfile.mkdtemp(prefix="afk_repair_patch_"))
    project = tmp / "SmokeRepair"
    project.mkdir()
    main_py = project / "main.py"
    _write(main_py, CLEAN)
    other_py = project / "helper.py"
    _write(other_py, CLEAN.replace("def run():", "def helper():"))

    init = _git(project, "init", "-q")
    check("测试仓库初始化成功", init.returncode == 0, init.stderr)
    _git(project, "add", "-A")
    commit = _git(project, "commit", "-q", "-m", "clean")
    check("测试仓库有基线提交", commit.returncode == 0, commit.stderr)

    patcher = _patcher()
    original = dict(patcher.PATCHES)
    patcher.PATCHES = {"SmokeRepair": {"path": "SmokeRepair", "blocks": [BLOCK]}}
    try:
        # 先打一次合法补丁：这就是正常状态下用户机器上的样子
        patcher.cmd_apply(tmp, ["SmokeRepair"])
        patched = _read(main_py)
        check("打完补丁后文件能编译", _compiles(main_py)[0], _compiles(main_py)[1])
        check("打完补丁后标记块在位", "AFK-MONITOR:smoke-repair-entry" in patched)

        # 制造事故现场：在补丁内容后面追加残留尾巴（就是 BAAH 当时的样子）
        _write(main_py, patched + "    \n    re_process\n\n        print('stale')\n")
        ok_now, detail_now = _compiles(main_py)
        check("事故复现：追加残留尾巴后文件编译不过", not ok_now, detail_now)

        status = _git(project, "status", "--porcelain").stdout
        check("事故文件的 git 状态是「已跟踪且被修改」", " M main.py" in status or "M  main.py" in status, status)

        bus = EventBus()
        engine = RepairEngine(
            bus,
            settings={"repair": {"enabled": True, "max_attempts": 3}},
            patcher=patcher,
            patcher_root=tmp,
        )
        outcome = engine.handle_failure(
            "SmokeRepair",
            None,
            'File "main.py", line 12\n    re_process\n    ^\nIndentationError: unexpected indent',
        )
        check("修复规则 patch-syntax-broken 命中并被判定为已修复", outcome.get("fixed") is True, outcome)
        check("命中的规则 id 就是 patch-syntax-broken",
              outcome.get("reason") == "patch-syntax-broken", outcome.get("reason"))

        ok_after, detail_after = _compiles(main_py)
        check("自愈之后 main.py 又能编译了", ok_after, detail_after)
        healed = _read(main_py)
        check("自愈之后补丁块重新在位（不是被裸着还原）", "AFK-MONITOR:smoke-repair-entry" in healed)
        check("自愈之后残留尾巴已经消失", "re_process" not in healed)

        # 没坏的时候不能乱来（换一个新引擎：上一个已经把 max_attempts 用掉了）
        engine2 = RepairEngine(
            bus,
            settings={"repair": {"enabled": True, "max_attempts": 3}},
            patcher=patcher,
            patcher_root=tmp,
        )
        outcome2 = engine2.handle_failure("SmokeRepair", None, "IndentationError: nope")
        check("文件已经是干净的补丁态时不再改动它",
              _read(main_py) == healed, "干净的文件被改了")
        check("文件已经是干净的补丁态时报「找不到被写坏的文件」",
              outcome2.get("fixed") is False and "没有找到" in " ".join(
                  str(a.get("detail", "")) for a in outcome2.get("attempts", [])),
              outcome2.get("attempts"))

        # 用户自己写坏的文件（没有 AFK-MONITOR 标记）不能碰
        _write(other_py, CLEAN + "def broken(:\n")
        before = _read(other_py)
        engine3 = RepairEngine(
            bus,
            settings={"repair": {"enabled": True, "max_attempts": 3}},
            patcher=patcher,
            patcher_root=tmp,
        )
        outcome3 = engine3.handle_failure("SmokeRepair", None, "SyntaxError: invalid syntax")
        check("不带 AFK-MONITOR 标记的坏文件不会被还原", _read(other_py) == before, "内容被改了")
        check("没有可还原目标时整条规则判定为未修复", outcome3.get("fixed") is False, outcome3)
    finally:
        patcher.PATCHES = original
        shutil.rmtree(tmp, ignore_errors=True)

    print("")
    if FAILED:
        print("REPAIR-PATCH FAIL: {0} 项".format(len(FAILED)))
        for name in FAILED:
            print("  - " + name)
        return 1
    print("REPAIR-PATCH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
