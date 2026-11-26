"""开机自启：写/删 HKCU\\...\\CurrentVersion\\Run 注册表项。

只用当前用户级 Run 键（HKCU），不需要管理员权限、不写系统级 HKLM，也不碰
任务计划程序，因此删除和排查都很直接（注册表里一眼可见 Auto-AFK 项）。

两种启动方式：
  * tray （默认）：经 tools\\launch_gui.vbs 静默拉起 GUI（带 --tray），
                   开机后驻留托盘，配合空闲检测实现「回来就用」，全程无黑窗、无 UAC；
  * admin         ：经 tools\\launch_admin.vbs 提权启动，开机时会弹一次 UAC
                   （游戏的 M7A/BAAH 需要管理员权限时选这个）。
"""

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Auto-AFK"

MODE_TRAY = "tray"
MODE_ADMIN = "admin"
MODES = (MODE_TRAY, MODE_ADMIN)
MODE_LABELS = {MODE_TRAY: "静默托盘（无 UAC）", MODE_ADMIN: "管理员（开机弹一次 UAC）"}

_LAUNCHERS = {MODE_TRAY: "launch_gui.vbs", MODE_ADMIN: "launch_admin.vbs"}


def supported():
    return os.name == "nt"


def _root():
    from . import config

    return Path(config.ROOT)


def build_command(mode=MODE_TRAY, extra_args=("--tray",)):
    """返回写进注册表 Run 键的完整命令行。"""
    if mode not in MODES:
        mode = MODE_TRAY
    args = [str(a) for a in (extra_args or ())]
    exe = _root() / "Auto-AFK.exe"
    if exe.is_file() and mode == MODE_TRAY:
        command = '"{0}"'.format(exe)
    else:
        vbs = _root() / "tools" / _LAUNCHERS[mode]
        command = 'wscript.exe "{0}"'.format(vbs)
    if args:
        command += " " + " ".join(args)
    return command


def _open_key(write=False):
    import winreg

    access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if write else 0)
    if write:
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def current():
    """读回注册表里的自启项；不存在返回 None。"""
    if not supported():
        return None
    import winreg

    try:
        with _open_key() as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return value
    except (FileNotFoundError, OSError):
        return None


def enable(mode=MODE_TRAY, extra_args=("--tray",)):
    """写入自启项；返回 {"ok", "mode", "command"} 或 {"ok": False, "error"}。"""
    if not supported():
        return {"ok": False, "error": "autostart is only supported on Windows"}
    import winreg

    if mode not in MODES:
        mode = MODE_TRAY
    command = build_command(mode, extra_args)
    try:
        with _open_key(write=True) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "mode": mode, "command": command, "value_name": VALUE_NAME}


def disable():
    """删除自启项；本来就不存在也算成功（removed=False）。"""
    if not supported():
        return {"ok": False, "error": "autostart is only supported on Windows"}
    import winreg

    try:
        with _open_key(write=True) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return {"ok": True, "removed": False}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "removed": True}


def status():
    """当前自启状态，供 GUI / CLI / 环境自检显示。"""
    command = current()
    mode = MODE_TRAY
    if command and "launch_admin.vbs" in command.lower():
        mode = MODE_ADMIN
    return {
        "supported": supported(),
        "enabled": bool(command),
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "command": command or "",
        "value_name": VALUE_NAME,
        "run_key": "HKCU\\" + RUN_KEY,
    }


def apply(enabled, mode=MODE_TRAY, extra_args=("--tray",)):
    """按开关意图设置自启，返回 status() 同构结果（附带 ok / error）。"""
    result = enable(mode, extra_args) if enabled else disable()
    merged = status()
    merged["ok"] = bool(result.get("ok"))
    if not result.get("ok"):
        merged["error"] = result.get("error", "unknown error")
    return merged


def _fallback_pythonw():
    """GUI 方式的备用解释器路径（launch_gui.vbs 缺失时提示用）。"""
    candidate = _root() / ".venv" / "Scripts" / "pythonw.exe"
    return str(candidate if candidate.exists() else Path(sys.executable))
