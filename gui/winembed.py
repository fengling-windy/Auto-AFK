"""Win32 外部窗口查找/嵌入辅助（供项目界面嵌入页使用）。

只依赖 ctypes + psutil（psutil 缺失时优雅降级为仅 PID 精确匹配）。
所有函数在非 Windows 平台安全返回空值。

查找策略（find_window_for_spawn，保证不抓取无关窗口）：
1. PID 进程树：窗口属于 spawn 进程及其子孙——最可靠
2. 标题关键词：窗口标题包含 spec.gui.gui_window_keywords 之一——处理
   单实例应用（窗口挂在已有进程）与提权重启（进程树脱离）的场景；
   关键词由项目注册表显式配置，未配置则不启用

不做"新增窗口 diff"之类的模糊兜底：无法区分目标 GUI 与桌面上同时出现
的其他窗口，宁可报失败让用户重试，也不嵌错窗口。
"""

import ctypes
import os
import time

if os.name == "nt":
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _GWL_EXSTYLE = -20
    _WS_EX_TOOLWINDOW = 0x00000080
    _DWMWA_CLOAKED = 14

SW_HIDE = 0
SW_SHOW = 5
SW_SHOWNORMAL = 1
SW_RESTORE = 9
WM_CLOSE = 0x0010


def _is_windows():
    return os.name == "nt"


def _is_toolwindow(hwnd):
    try:
        style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        return bool(style & _WS_EX_TOOLWINDOW)
    except Exception:
        return False


def _is_cloaked(hwnd):
    """UWP/挂起的隐形窗口（Win11 常见幽灵窗口）。"""
    try:
        cloaked = wintypes.DWORD(0)
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        return result == 0 and cloaked.value != 0
    except Exception:
        return False


def descendant_pids(root_pid):
    """root_pid 及其全部子孙进程的 PID 集合（GUI 进程可能再拉起子进程）。"""
    pids = {int(root_pid)}
    if not _is_windows():
        return pids
    try:
        import psutil

        parent = psutil.Process(int(root_pid))
        for child in parent.children(recursive=True):
            pids.add(child.pid)
    except Exception:
        pass
    return pids


def window_pid(hwnd):
    try:
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def window_title(hwnd):
    try:
        length = _user32.GetWindowTextLengthW(int(hwnd))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(int(hwnd), buf, length + 1)
        return buf.value
    except Exception:
        return ""


def window_matches(hwnd, pids=None, title_keywords=None):
    """嵌入前归属校验：窗口属于 pids（可动态扩充的进程树）或标题匹配关键词。"""
    if not _is_windows():
        return False
    hwnd = int(hwnd)
    if pids and window_pid(hwnd) in pids:
        return True
    if title_keywords:
        title = window_title(hwnd)
        if title and any(str(k).lower() in title.lower() for k in title_keywords):
            return True
    return False


def list_windows(include_hidden=False):
    """枚举带标题的顶层窗口：[(hwnd, pid, title, area)]。

    默认只列可见窗口；include_hidden=True 时包含隐藏窗口（如最小化到
    托盘的单实例 GUI），用于"界面已驻留托盘"场景的找回。
    """
    if not _is_windows():
        return []
    results = []

    def _cb(hwnd, lparam):
        visible = bool(_user32.IsWindowVisible(hwnd))
        if not visible and not include_hidden:
            return True
        if _is_toolwindow(hwnd) or _is_cloaked(hwnd):
            return True
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        rect = wintypes.RECT()
        area = 0
        if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        results.append((int(hwnd), int(pid.value), buf.value, area))
        return True

    _user32.EnumWindows(_ENUM_PROC(_cb), 0)
    return results


def restore_hwnd(hwnd):
    """恢复隐藏/最小化的窗口（托盘驻留的单实例 GUI 找回后调用）。"""
    if not _is_windows():
        return
    try:
        _user32.ShowWindow(int(hwnd), SW_RESTORE)
    except Exception:
        pass


def find_window_for_spawn(pids=None, title_keywords=None, timeout=60.0, poll=0.6, proc=None):
    """轮询查找新启动 GUI 的主窗口，返回 hwnd 或 None（超时/进程退出且无望）。

    每个周期按优先级尝试：
    1. 窗口属于 pids（spawn 进程树，动态刷新子孙）
    2. 窗口标题包含 title_keywords 之一（提权重启后窗口属于新进程，靠标题找到）

    proc 传入 Popen 时监测进程退出：退出后若无标题关键词可依（或关键词
    仍未命中），通过 fail_reason 返回明确原因并提前结束，不再傻等。
    返回 (hwnd, fail_reason)；hwnd 非 None 表示成功。
    """
    if not _is_windows():
        return None, "仅 Windows 支持窗口嵌入"
    pids = {int(p) for p in (pids or set())}
    keywords = [str(k) for k in (title_keywords or []) if str(k).strip()]
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        # 刷新进程树（子进程可能在运行中新拉起）
        if proc is not None and proc.poll() is None:
            try:
                pids |= descendant_pids(proc.pid)
            except Exception:
                pass
        for hwnd, pid, title, area in list_windows():
            if area <= 0:
                continue
            if pid in pids:
                return hwnd, None
            if keywords and any(k.lower() in title.lower() for k in keywords):
                return hwnd, None
        if keywords:
            # 隐藏窗口（单实例 GUI 驻留托盘）：标题命中即恢复显示并采用。
            # 只按显式配置的标题关键词找，绝不模糊匹配，不会误抓其他窗口。
            for hwnd, pid, title, area in list_windows(include_hidden=True):
                if area <= 0 or _user32.IsWindowVisible(hwnd):
                    continue
                if any(k.lower() in title.lower() for k in keywords):
                    restore_hwnd(hwnd)
                    time.sleep(0.3)
                    return hwnd, None
        if proc is not None and proc.poll() is not None:
            # spawn 的进程已退出：若配置了标题关键词，可能提权/单实例后窗口
            # 属于别的进程，继续靠标题等待；否则直接失败
            if not keywords:
                code = proc.returncode
                return None, "界面进程已退出（code {0}），未出现窗口".format(code)
        time.sleep(poll)
    if proc is not None and proc.poll() is not None and not keywords:
        return None, "界面进程已退出（code {0}），未出现窗口".format(proc.returncode)
    if not keywords and proc is not None and proc.poll() is None:
        return None, "等待 {0} 秒未出现界面窗口".format(int(timeout))
    return None, "等待 {0} 秒未出现界面窗口".format(int(timeout))


def _pid_alive(pid):
    if not _is_windows():
        return False
    try:
        import psutil

        return psutil.pid_exists(int(pid))
    except Exception:
        return True


def window_alive(hwnd):
    """嵌入的窗口是否仍然存在（进程退出/窗口关闭后变 False）。

    只查 IsWindow 不查可见性：静默启动时窗口被我们隐藏，不能算消失。"""
    if not _is_windows():
        return False
    try:
        return bool(_user32.IsWindow(int(hwnd)))
    except Exception:
        return False


def show_hwnd(hwnd, show=True):
    """显示/隐藏外部窗口（静默启动时先藏起来，嵌入前再显示）。"""
    if not _is_windows():
        return
    try:
        _user32.ShowWindow(int(hwnd), SW_SHOW if show else SW_HIDE)
    except Exception:
        pass


def wait_http(port, timeout=60.0, poll=0.5):
    """轮询本机 HTTP 端口直到有响应（NiceGUI 等网页 GUI 的就绪判定）。"""
    import http.client

    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=1.5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            return True
        except Exception:
            time.sleep(poll)
    return False


def pick_free_port():
    """挑一个当前空闲的本机端口（网页 GUI 用固定端口便于内嵌）。"""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def close_hwnd(hwnd):
    """向外部窗口投递 WM_CLOSE（礼貌关闭：项目 GUI 自己走关闭流程）。"""
    if not _is_windows():
        return
    try:
        _user32.PostMessageW(int(hwnd), WM_CLOSE, 0, 0)
    except Exception:
        pass


def kill_tree(root_pid):
    """结束 root_pid 及其全部子孙进程（Auto-AFK 退出时清理启动的界面进程）。"""
    if not _is_windows():
        return
    try:
        import psutil

        parent = psutil.Process(int(root_pid))
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
        try:
            parent.kill()
        except Exception:
            pass
    except Exception:
        pass
