import os
import subprocess


def hidden_kwargs():
    if os.name != "nt":
        return {}
    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    except Exception:
        # 拿不到 STARTUPINFO 就退回只用 CREATE_NO_WINDOW：窗口一样不弹，
        # 只是某些启动器（.bat / .vbs）会闪一下。这里静默是有意的，
        # 每次起子进程都刷一条 warning 会把真正的问题淹掉。
        pass
    return kwargs


def host_env(drop_prefix=("QT_",), env=None):
    """子进程该用的环境：继承当前进程，但去掉 Qt 的会话级变量。

    历史缺陷（2026-11-06 定位）：MuMu 的前端全是 Qt5 程序
    （MuMuManager.exe / MuMuNxUpdater.exe / MuMuNxMain.exe），一旦继承了
    `QT_QPA_PLATFORM=offscreen`（我们的冒烟与离屏预览脚本会设它），它们就
    找不到 windows 平台插件 → qFatal → `0xC0000409`
    （FAST_FAIL_FATAL_APP_EXIT，Windows 事件日志里 Faulting module = Qt5Core.dll），
    并弹「This application failed to start because no Qt platform plugin could be
    initialized」。更糟的是弹框会挂在那里不退出，我们的 `_run` 只能等超时。

    实测对照（crashes 取 Windows 事件日志增量）：
        干净环境                          exit=0        crashes +0
        QT_QPA_PLATFORM=offscreen         exit=0xC0000409  crashes +1
        QT_QPA_PLATFORM=windows           exit=0        crashes +0
    所以起任何第三方 Qt 程序都必须把这组变量摘掉。
    """
    base = dict(os.environ if env is None else env)
    if not drop_prefix:
        return base
    prefixes = tuple(p.upper() for p in drop_prefix)
    for key in [k for k in base if str(k).upper().startswith(prefixes)]:
        base.pop(key, None)
    return base


def run(*args, **kwargs):
    merged = hidden_kwargs()
    merged.update(kwargs)
    return subprocess.run(*args, **merged)


def popen(*args, **kwargs):
    merged = hidden_kwargs()
    merged.update(kwargs)
    return subprocess.Popen(*args, **merged)
