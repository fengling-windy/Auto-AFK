import importlib.util
import logging
import os
import shutil
from pathlib import Path

from . import config
from .proc import run as proc_run

log = logging.getLogger("afk.doctor")

TOOLS = [
    ("git", ["git", "--version"]),
    ("uv", ["uv", "--version"]),
    ("node", ["node", "--version"]),
    ("pnpm", ["pnpm", "--version"]),
    ("go", ["go", "version"]),
    ("adb", ["adb", "version"]),
]

FALLBACK_PATHS = {
    "go": [
        Path(r"C:\Program Files\Go\bin\go.exe"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Go" / "bin" / "go.exe",
    ],
    "pnpm": [
        Path(os.environ.get("APPDATA", "")) / "npm" / "pnpm.cmd",
    ],
}


def _resolve_tool(name, exe):
    found = shutil.which(exe)
    if found:
        return found
    for candidate in FALLBACK_PATHS.get(name, []):
        try:
            if candidate.exists():
                return str(candidate)
        except OSError:
            continue
    return None


def _find_genshin():
    import json as _json

    candidates = []
    # doctor.game_exe 是用户显式指定的路径，优先级最高：置顶后下面的候选
    # 依次回退，这样非默认安装位置不需要改代码。
    override = str(((config.settings().get("doctor") or {}).get("game_exe") or "")).strip()
    if override:
        candidates.append(Path(override))
    config_path = config.ROOT / "configs" / "BetterGI" / "User" / "config.json"
    if config_path.exists():
        try:
            data = _json.loads(config_path.read_text(encoding="utf-8-sig"))
            install_path = (data.get("genshinStartConfig") or {}).get("installPath") or ""
            if install_path:
                candidates.append(Path(str(install_path)))
        except Exception as exc:
            # BetterGI 的 config.json 是它自己写的，形状会随版本变。读不出来
            # 只是少了「用户配的安装路径」这一个线索，后面还有默认候选，但
            # 排障时要能看出为什么没找到游戏。
            log.warning("read BetterGI config for genshin path failed: %s: %s",
                        type(exc).__name__, exc)
    candidates += [
        Path(r"E:\miHoYo Launcher\games\Genshin Impact Game\YuanShen.exe"),
        Path(r"E:\miHoYo Launcher\games\Genshin Impact Game\GenshinImpact.exe"),
        Path(r"C:\Program Files\Genshin Impact\Genshin Impact Game\YuanShen.exe"),
        Path(r"D:\Program Files\Genshin Impact\Genshin Impact Game\YuanShen.exe"),
        Path(r"E:\Genshin Impact\Genshin Impact Game\YuanShen.exe"),
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
            if candidate.is_dir() and (candidate / "YuanShen.exe").is_file():
                return str(candidate / "YuanShen.exe")
        except OSError:
            continue
    return ""


def _tool_check(name, cmd):
    path = _resolve_tool(name, cmd[0])
    if not path:
        return {"name": name, "status": "fail", "detail": "not found on PATH"}
    try:
        proc = proc_run([path] + cmd[1:], capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=30)
        first = (proc.stdout or proc.stderr).strip().splitlines()
        detail = first[0] if first else ""
        return {"name": name, "status": "ok", "detail": detail, "path": path}
    except Exception as exc:
        return {"name": name, "status": "warn", "detail": str(exc), "path": path}


def _venv_check(project_dir, python_rel):
    python = project_dir / python_rel
    if not python.exists():
        return {"status": "fail", "detail": "venv python missing: {0}".format(python)}
    try:
        proc = proc_run([str(python), "-c", "import sys; print(sys.version.split()[0])"], capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
        if proc.returncode == 0:
            return {"status": "ok", "detail": "python " + proc.stdout.strip()}
        return {"status": "fail", "detail": proc.stderr.strip()[-300:]}
    except Exception as exc:
        return {"status": "fail", "detail": str(exc)}


def _patch_check(project, patcher=None, root=None):
    try:
        module = patcher
        if module is None:
            spec = importlib.util.spec_from_file_location("afk_patcher", config.MONITOR_KIT / "patcher.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        if project not in getattr(module, "PATCHES", {}):
            return {"status": "ok", "detail": "log-based monitoring (no source patch needed)"}
        report = module.cmd_verify(root or config.ROOT, [project])
        states = [(item, state) for item, state in report.get(project, [])]
        if not states:
            return {"status": "warn", "detail": "no patch definitions"}
        # syntax-broken：补丁块都在位，但打完补丁的源文件编译不过。2026-11-06
        # 那次 BAAH 起不来就是这个状态，而旧检查只看标记在不在、报的是 ok，
        # 体检完全没提示。项目直接起不来，必须报 fail 并把行号带出来。
        broken = [(item, state) for item, state in states if state == "syntax-broken"]
        if broken:
            return {
                "status": "fail",
                "detail": "源文件语法已损坏，项目起不来：" + "; ".join(item for item, _ in broken),
            }
        if all(state == "ok" for _, state in states):
            return {"status": "ok", "detail": "{0}/{0} blocks ok".format(len(states))}
        return {"status": "warn", "detail": ", ".join(sorted({s for _, s in states}))}
    except Exception as exc:
        return {"status": "warn", "detail": str(exc)}


def run_checks():
    results = []
    try:
        from .supervisor import is_admin

        elevated = is_admin()
    except Exception:
        elevated = False
    results.append(
        {
            "group": "runtime",
            "name": "admin",
            "status": "ok" if elevated else "warn",
            "detail": "running elevated" if elevated else "not elevated; March7thAssistant/BAAH require admin (use Auto-AFK-Admin.bat)",
        }
    )
    for name, cmd in TOOLS:
        item = _tool_check(name, cmd)
        item["group"] = "tools"
        results.append(item)

    for key, spec in (config.registry().get("projects", {}) or {}).items():
        project_dir = config.project_dir(spec)
        results.append(
            {
                "group": "project",
                "name": key + ":source",
                "status": "ok" if project_dir.exists() else "fail",
                "detail": str(project_dir) if project_dir.exists() else "missing: {0}".format(project_dir),
            }
        )
        if spec.get("launch_type", "python") == "python":
            item = _venv_check(project_dir, spec.get("python", ".venv\\Scripts\\python.exe"))
            item.update({"group": "project", "name": key + ":venv"})
            results.append(item)
        exe = project_dir / spec.get("exe", "")
        if spec.get("launch_type") == "exe":
            results.append(
                {
                    "group": "project",
                    "name": key + ":exe",
                    "status": "ok" if exe.exists() else "fail",
                    "detail": str(exe),
                }
            )
        item = _patch_check(key)
        item.update({"group": "project", "name": key + ":patches"})
        results.append(item)
        store = config.config_dir_for(key, spec)
        results.append(
            {
                "group": "project",
                "name": key + ":config",
                "status": "ok" if store.exists() else "warn",
                "detail": str(store),
            }
        )

    maaend_install = config.ROOT / "MaaEnd" / "install"
    results.append(
        {
            "group": "maaend",
            "name": "MaaEnd:install",
            "status": "ok" if (maaend_install / "mxu.exe").exists() else "fail",
            "detail": str(maaend_install / "mxu.exe"),
        }
    )

    baah_adb = config.ROOT / "BAAH" / "tools" / "adb" / "adb.exe"
    baah_aria2 = config.ROOT / "BAAH" / "tools" / "aria2" / "aria2c.exe"
    results.append(
        {
            "group": "project",
            "name": "BAAH:tools",
            "status": "ok" if baah_adb.exists() and baah_aria2.exists() else "fail",
            "detail": "adb={0} aria2={1}".format(baah_adb.exists(), baah_aria2.exists()),
        }
    )

    genshin_path = _find_genshin()
    results.append(
        {
            "group": "runtime",
            "name": "genshin:install",
            "status": "ok" if genshin_path else "warn",
            "detail": genshin_path if genshin_path else "Genshin Impact not found (install it or set path in BetterGI)",
        }
    )

    bettergi_tasks = 0
    if (config.ROOT / "BetterGI" / "publish" / "BetterGI.exe").exists():
        import json as _json

        one_dragon_dir = config.ROOT / "configs" / "BetterGI" / "User" / "OneDragon"
        if one_dragon_dir.exists():
            for item in one_dragon_dir.glob("*.json"):
                try:
                    data = _json.loads(item.read_text(encoding="utf-8-sig"))
                    bettergi_tasks = max(
                        bettergi_tasks,
                        sum(1 for enabled in (data.get("TaskEnabledList") or {}).values() if enabled),
                    )
                except Exception:
                    continue
    results.append(
        {
            "group": "project",
            "name": "BetterGI:tasks",
            "status": "ok" if bettergi_tasks > 0 else "warn",
            "detail": "enabled one-dragon tasks: {0}".format(bettergi_tasks),
        }
    )

    emu_cfg = ((config.settings().get("pre_run") or {}).get("emulator") or {})
    device_exe = Path(str(emu_cfg.get("device_exe", "")))
    manager_exe = Path(str(emu_cfg.get("manager_exe", "")))
    # doctor.emulator_exe 是用户显式指定的模拟器主程序，用于 device_exe/manager_exe
    # 都没配（或装在非默认位置）时给出可用信号，否则这条检查永远 warn。
    emu_override = str(((config.settings().get("doctor") or {}).get("emulator_exe") or "")).strip()
    emu_override_ok = bool(emu_override) and Path(emu_override).exists()
    emu_ok = (device_exe.exists() and manager_exe.exists()) or emu_override_ok
    results.append(
        {
            "group": "runtime",
            "name": "emulator:tools",
            "status": "ok" if emu_ok else "warn",
            "detail": "device={0} manager={1} emulator_exe={2}".format(
                device_exe.exists(), manager_exe.exists(), emu_override_ok
            ),
        }
    )
    try:
        from .emulator import MuMuController

        controller = MuMuController(None, config.settings())
        running = controller.is_running()
        results.append(
            {
                "group": "runtime",
                "name": "emulator:state",
                "status": "ok" if running else "warn",
                "detail": "running={0} version={1}".format(running, controller.version() or "unknown"),
            }
        )
    except Exception as exc:
        results.append({"group": "runtime", "name": "emulator:state", "status": "warn", "detail": str(exc)})

    emulator_names = [n.lower() for n in (config.settings().get("doctor", {}) or {}).get("emulator_processes", ["mumuplayer", "nemuplayer", "dnplayer", "lmdplayer"])]
    try:
        import psutil

        running = []
        for proc in psutil.process_iter(["name"]):
            try:
                pname = (proc.info.get("name") or "").lower()
                if any(token in pname for token in emulator_names):
                    running.append(proc.info.get("name"))
            except Exception:
                continue
        results.append(
            {
                "group": "runtime",
                "name": "emulator",
                "status": "ok" if running else "warn",
                "detail": ", ".join(sorted(set(running))) if running else "no known emulator process detected",
            }
        )
    except Exception as exc:
        results.append({"group": "runtime", "name": "emulator", "status": "warn", "detail": str(exc)})

    game_tokens = [t.lower() for t in (config.settings().get("doctor", {}) or {}).get("game_processes", ["endfield", "starrail", "bluearchive", "arknights"])]
    try:
        import psutil

        found = []
        for proc in psutil.process_iter(["name"]):
            try:
                pname = (proc.info.get("name") or "").lower()
                if any(token in pname for token in game_tokens):
                    found.append(proc.info.get("name"))
            except Exception:
                continue
        results.append(
            {
                "group": "runtime",
                "name": "games",
                "status": "ok" if found else "warn",
                "detail": ", ".join(sorted(set(found))) if found else "no known game process detected",
            }
        )
    except Exception as exc:
        results.append({"group": "runtime", "name": "games", "status": "warn", "detail": str(exc)})

    return results


def run_project_checks(key, spec=None):
    """只针对单个项目的环境检测（项目配置页「环境检测」按钮用）。"""
    registry_projects = config.registry().get("projects", {}) or {}
    spec = spec or registry_projects.get(key)
    results = []
    if not spec:
        return [{"group": "project", "name": key + ":registry", "status": "fail",
                 "detail": "注册表里没有这个项目"}]
    launch_type = spec.get("launch_type", "python")

    if spec.get("require_admin"):
        try:
            from .supervisor import is_admin

            elevated = is_admin()
        except Exception:
            elevated = False
        results.append({
            "group": "runtime", "name": "admin",
            "status": "ok" if elevated else "warn",
            "detail": "已提权" if elevated else "未提权：该项目需要管理员权限（请用 Auto-AFK-Admin.bat 启动）",
        })

    needed = ["git"]
    if launch_type == "python" or key == "MaaEnd":
        needed.append("uv")
    if key == "MaaEnd":
        needed += ["go", "pnpm"]
    if key == "BAAH":
        needed.append("adb")
    tool_cmds = dict(TOOLS)
    for name in needed:
        cmd = tool_cmds.get(name)
        if cmd is None:
            continue
        item = _tool_check(name, cmd)
        item["group"] = "tools"
        results.append(item)
    if key == "BetterGI":
        dotnet = _resolve_tool("dotnet", "dotnet")
        results.append({
            "group": "tools", "name": "dotnet",
            "status": "ok" if dotnet else "warn",
            "detail": dotnet or "未找到 dotnet（BetterGI 需要 .NET 8 桌面运行时）",
        })

    project_dir = config.project_dir(spec)
    results.append({
        "group": "project", "name": key + ":source",
        "status": "ok" if project_dir.exists() else "fail",
        "detail": str(project_dir) if project_dir.exists() else "missing: {0}".format(project_dir),
    })
    if launch_type == "python":
        item = _venv_check(project_dir, spec.get("python", ".venv\\Scripts\\python.exe"))
        item.update({"group": "project", "name": key + ":venv"})
        results.append(item)
    elif launch_type == "exe":
        exe = project_dir / spec.get("exe", "")
        results.append({
            "group": "project", "name": key + ":exe",
            "status": "ok" if exe.exists() else "fail", "detail": str(exe),
        })
    item = _patch_check(key)
    item.update({"group": "project", "name": key + ":patches"})
    results.append(item)
    store = config.config_dir_for(key, spec)
    results.append({
        "group": "project", "name": key + ":config",
        "status": "ok" if store.exists() else "warn", "detail": str(store),
    })

    if key == "BAAH":
        adb = project_dir / "tools" / "adb" / "adb.exe"
        aria = project_dir / "tools" / "aria2" / "aria2c.exe"
        results.append({
            "group": "project", "name": "BAAH:tools",
            "status": "ok" if adb.exists() and aria.exists() else "fail",
            "detail": "adb={0} aria2={1}".format(adb.exists(), aria.exists()),
        })
    if key == "MaaEnd":
        mxu = project_dir / "install" / "mxu.exe"
        results.append({
            "group": "maaend", "name": "MaaEnd:install",
            "status": "ok" if mxu.exists() else "fail", "detail": str(mxu),
        })
    if key == "BetterGI":
        genshin = _find_genshin()
        results.append({
            "group": "runtime", "name": "genshin:install",
            "status": "ok" if genshin else "warn",
            "detail": genshin or "未找到原神客户端（请安装或在 BetterGI 里设置路径）",
        })
        tasks = 0
        one_dragon = config.ROOT / "configs" / "BetterGI" / "User" / "OneDragon"
        if one_dragon.exists():
            import json as _json

            for item_path in one_dragon.glob("*.json"):
                try:
                    data = _json.loads(item_path.read_text(encoding="utf-8-sig"))
                    tasks = max(tasks, sum(1 for v in (data.get("TaskEnabledList") or {}).values() if v))
                except Exception:
                    continue
        results.append({
            "group": "project", "name": "BetterGI:tasks",
            "status": "ok" if tasks > 0 else "warn",
            "detail": "enabled one-dragon tasks: {0}".format(tasks),
        })
    if spec.get("manage_emulator"):
        emu_cfg = ((config.settings().get("pre_run") or {}).get("emulator") or {})
        device_exe = Path(str(emu_cfg.get("device_exe", "")))
        manager_exe = Path(str(emu_cfg.get("manager_exe", "")))
        ok = device_exe.exists() and manager_exe.exists()
        results.append({
            "group": "runtime", "name": "emulator:tools",
            "status": "ok" if ok else "warn",
            "detail": "device={0} manager={1}".format(device_exe.exists(), manager_exe.exists()),
        })
    return results


def summary(results):
    ok = sum(1 for r in results if r["status"] == "ok")
    warn = sum(1 for r in results if r["status"] == "warn")
    fail = sum(1 for r in results if r["status"] == "fail")
    return {"ok": ok, "warn": warn, "fail": fail, "total": len(results)}
