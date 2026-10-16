"""Auto-AFK GUI 引导启动器（在项目自己的 venv/解释器里运行）。

用法：
    python gui_bootstrap.py <report_port> <项目入口脚本> [入口参数...]

职责：
1. 以项目原入口的身份运行脚本（sys.argv / __name__ / __file__ 与直接运行一致）
2. 守护线程枚举本进程创建的主窗口，把 HWND 通过 UDP 上报给 Auto-AFK
   （127.0.0.1:<report_port>），Auto-AFK 据此直接嵌入窗口——零搜索、零误抓

只用标准库，兼容各项目自己的解释器版本。
"""

import ctypes
import os
import socket
import sys
import threading
import time

REPORT_INTERVAL_SEC = 1.0
REPORT_DEADLINE_SEC = 60.0
POLL_SEC = 0.3

if os.name == "nt":
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _dwmapi = ctypes.windll.dwmapi
    _ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _GWL_EXSTYLE = -20
    _WS_EX_TOOLWINDOW = 0x00000080
    _DWMWA_CLOAKED = 14


def _report_hwnd():
    """返回本进程当前的主窗口 HWND（可见、带标题、非工具窗、面积最大）。"""
    if os.name != "nt":
        return 0
    current_pid = _kernel32.GetCurrentProcessId()
    best = [0, 0]  # hwnd, area

    def _cb(hwnd, lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        try:
            style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            if style & _WS_EX_TOOLWINDOW:
                return True
            cloaked = wintypes.DWORD(0)
            if _dwmapi.DwmGetWindowAttribute(
                hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            ) == 0 and cloaked.value != 0:
                return True
        except Exception:
            pass
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != current_pid:
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        if area > best[1]:
            best[0], best[1] = int(hwnd), area
        return True

    try:
        _user32.EnumWindows(_ENUM_PROC(_cb), 0)
    except Exception:
        pass
    return best[0]


def _watch(report_port):
    """窗口出现后通过 UDP 上报 HWND（连报几次抗丢包），随后停止。"""
    deadline = time.time() + REPORT_DEADLINE_SEC
    reported = 0
    sock = None
    while time.time() < deadline and reported < 3:
        try:
            hwnd = _report_hwnd()
            if hwnd:
                if sock is None:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto("HWND {0}".format(hwnd).encode("ascii"), ("127.0.0.1", report_port))
                reported += 1
                time.sleep(0.5)
                continue
        except Exception:
            pass
        time.sleep(POLL_SEC)
    if sock is not None:
        try:
            sock.close()
        except Exception:
            pass


def main():
    argv = sys.argv[1:]
    if len(argv) < 2:
        print("usage: gui_bootstrap.py <report_port> <script> [args...]")
        return 2
    report_port = int(argv[0])
    script = argv[1]
    # 还原项目入口脚本视角的命令行：sys.argv = [脚本路径, 原参数...]
    sys.argv = [script] + argv[2:]
    # 还原直接运行 `python 脚本` 的模块搜索语义：sys.path[0] = 脚本所在目录
    #（runpy.run_path 不会做这件事，会导致项目本地模块 import 失败）
    script_dir = os.path.dirname(os.path.abspath(script))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    threading.Thread(target=_watch, args=(report_port,), daemon=True, name="afk-hwnd-report").start()
    runpy_run(script)
    return 0


def runpy_run(script):
    import runpy

    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    sys.exit(main())
