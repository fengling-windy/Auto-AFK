"""真实嵌入冒烟测试（仅 Windows + 真实 windows 平台）。

用 tkinter 拉起一个外部窗口，走完 spawn → winembed 找窗 → createWindowContainer
嵌入 → 进程关闭后 tick 检测的全链路，验证原生嵌入在真机上可用。

用法（不要加 QT_QPA_PLATFORM=offscreen）：
    .venv/Scripts/python.exe tests/smoke_embed_real.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if os.name != "nt":
    print("EMBED-REAL SKIP (non-windows)")
    sys.exit(0)

from PySide6.QtCore import Qt
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from gui import winembed

FAILURES = []


def check(name, ok):
    print("{0} {1}".format("PASS" if ok else "FAIL", name))
    if not ok:
        FAILURES.append(name)


def pump(app, ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


TK_SCRIPT = (
    "import tkinter as tk\n"
    "r = tk.Tk()\n"
    "r.title('AFK-Embed-Test')\n"
    "r.geometry('420x300+60+60')\n"
    "r.mainloop()\n"
)


def main():
    try:
        app = QApplication(sys.argv)
    except Exception as exc:
        print("EMBED-REAL SKIP (no desktop: {0})".format(exc))
        return 0

    # 1. 用 gui_bootstrap.py 引导启动 tkinter 窗口——Auto-AFK 直接启动窗口的真实链路：
    #    引导脚本在目标进程内运行入口，把主窗口 HWND 通过 UDP 主动上报
    import socket as _socket

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    target_script = ROOT / "logs" / "_embed_tk_target.py"
    target_script.parent.mkdir(parents=True, exist_ok=True)
    target_script.write_text(TK_SCRIPT, encoding="utf-8")
    rep_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    rep_sock.bind(("127.0.0.1", 0))
    rep_sock.settimeout(1.0)
    report_port = rep_sock.getsockname()[1]
    bootstrap = ROOT / "gui" / "gui_bootstrap.py"
    proc = subprocess.Popen(
        [sys.executable, str(bootstrap), str(report_port), str(target_script)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    try:
        # 2. 等待目标进程内引导上报（零搜索：窗口由目标进程自己上报）
        pids = winembed.descendant_pids(proc.pid)
        check("进程树 PID 收集", proc.pid in pids)
        hwnd = None
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                data, _addr = rep_sock.recvfrom(256)
            except _socket.timeout:
                continue
            text = bytes(data).decode("ascii", errors="ignore").strip()
            if text.startswith("HWND"):
                hwnd = int(text.split()[1])
                break
        rep_sock.close()
        check("引导脚本上报主窗口 HWND", bool(hwnd))
        check("上报窗口属于目标进程", bool(hwnd) and winembed.window_pid(hwnd) in pids)

        # 3. 嵌入（宿主深色背景，tk 默认浅灰窗口形成对比便于验证）
        host = QWidget()
        host.setWindowTitle("Auto-AFK 嵌入测试宿主")
        host.setStyleSheet("background: #16181d;")
        layout = QVBoxLayout(host)
        caption = QLabel("嵌入区下方（宿主内容）")
        caption.setAlignment(Qt.AlignCenter)
        caption.setStyleSheet("color: #dfe6f5; background: transparent;")
        layout.addWidget(caption)
        qwin = QWindow.fromWinId(int(hwnd))
        qwin.show()
        container = QWidget.createWindowContainer(qwin, host)
        container.setMinimumHeight(240)
        layout.addWidget(container, 1)
        host.resize(760, 560)
        host.show()
        pump(app, 1800)

        # QWidget.grab() 抓不到原生子窗口（window container 由系统合成）。
        # 先做几何验证（不受遮挡影响）：createWindowContainer 会把外部窗口
        # 尺寸调整为容器尺寸；宿主在前台时再用屏幕截图做渲染采样。
        import ctypes
        from ctypes import wintypes as _wt

        rect = _wt.RECT()
        have_geo = ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(rect))
        dpr = app.primaryScreen().devicePixelRatio()
        geo_ok = False
        if have_geo:
            geo_ok = (
                abs((rect.right - rect.left) - container.width() * dpr) < 40
                and abs((rect.bottom - rect.top) - container.height() * dpr) < 40
            )
        check("嵌入后窗口几何由宿主接管", bool(geo_ok))

        screen = app.primaryScreen().grabWindow(0)
        img = screen.toImage()
        host_active = QApplication.activeWindow() is host
        if host_active:
            light = 0
            sampled = 0
            for x in range(int(max(0, rect.left)), int(min(img.width() - 1, rect.right)), 16):
                for y in range(int(max(0, rect.top)), int(min(img.height() - 1, rect.bottom)), 16):
                    sampled += 1
                    c = img.pixel(x, y)
                    r, g, b = (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF
                    if r > 170 and g > 170 and b > 170:
                        light += 1
            # 信息性结果：前台状态/DPI/z-order 都会影响屏幕采样，不作为失败条件
            #（嵌入正确性以「上报归属 + 几何接管 + 存活检测」三项硬断言为准）
            if sampled > 10:
                print("INFO 渲染采样：{0}/{1} 亮色像素（{2:.0%}）".format(light, sampled, light / sampled))
        else:
            print("SKIP 嵌入区域渲染采样（宿主被遮挡，以几何验证为准）")

        check("归属校验放行真实窗口", winembed.window_matches(int(hwnd), pids=pids, title_keywords=None))

        shot = ROOT / "logs" / "embed_real.png"
        screen.save(str(shot))
        check("屏幕级截图已保存", shot.exists() and shot.stat().st_size > 5000)

        check("窗口存活检测（存在）", winembed.window_alive(int(hwnd)))

        # 4. 进程退出后 tick 应检测到
        proc.kill()
        proc.wait(timeout=10)
        gone = False
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            pump(app, 300)
            if not winembed.window_alive(int(hwnd)):
                gone = True
                break
        check("进程关闭后窗口判定失效", gone)
        host.hide()
    finally:
        try:
            proc.kill()
        except Exception:
            pass

    # -- 网页模式（BAAH 类）：本地 HTTP 服务 + QWebEngineView 内嵌 ---------------
    import http.server
    import socketserver
    import threading

    page_flag = {"loaded": False}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><body style='background:#1c2b4a'><h1 style='color:#fff'>AFK-WEB-EMBED-OK</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    web_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtCore import QUrl

        web_host = QWidget()
        web_host.setWindowTitle("Auto-AFK 网页嵌入测试宿主")
        web_host.setStyleSheet("background: #10141d;")
        web_layout = QVBoxLayout(web_host)
        view = QWebEngineView()
        view.loadFinished.connect(lambda ok: page_flag.update(loaded=ok))
        web_layout.addWidget(view, 1)
        web_host.resize(820, 580)
        web_host.show()
        view.load(QUrl("http://127.0.0.1:{0}/".format(web_port)))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not page_flag["loaded"]:
            pump(app, 100)
        check("内置浏览器加载本地网页", page_flag["loaded"])
        pump(app, 600)
        grab = view.grab().toImage()
        colors = set()
        for x in range(0, grab.width(), 16):
            for y in range(0, grab.height(), 16):
                colors.add(grab.pixel(x, y))
        check("网页内容已渲染（像素多样化）", len(colors) > 3)
        web_host.hide()
    except Exception as exc:
        check("内置浏览器加载本地网页（异常: {0}）".format(exc), False)
    finally:
        httpd.shutdown()

    # -- 进程秒退 + 无标题关键词：立即失败并给出原因，不傻等不误抓 ---------------
    dead = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    dead.wait(timeout=10)
    t0 = time.monotonic()
    hwnd2, reason2 = winembed.find_window_for_spawn(
        pids=winembed.descendant_pids(dead.pid), timeout=20.0, proc=dead
    )
    elapsed = time.monotonic() - t0
    check("进程秒退时立即失败（不傻等 20 秒）", hwnd2 is None and elapsed < 8)
    check("失败原因包含退出码", bool(reason2) and "已退出" in reason2)

    # -- 引导启动 + 本地模块 import（复刻三月七 "No module named 'utils'" 失败模式）--
    boot_dir = ROOT / "logs" / "_boot_fix_check"
    (boot_dir / "utils").mkdir(parents=True, exist_ok=True)
    (boot_dir / "utils" / "__init__.py").write_text("", encoding="utf-8")
    (boot_dir / "utils" / "dpi.py").write_text("CONFIGURED = True\n", encoding="utf-8")
    (boot_dir / "entry_app.py").write_text(
        "from utils.dpi import CONFIGURED\n"
        "import tkinter as tk\n"
        "r = tk.Tk()\n"
        "r.title('AFK-Boot-Import-Test')\n"
        "r.geometry('380x260+80+80')\n"
        "assert CONFIGURED\n"
        "r.mainloop()\n",
        encoding="utf-8",
    )
    rep_sock2 = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    rep_sock2.bind(("127.0.0.1", 0))
    rep_sock2.settimeout(1.0)
    boot_port = rep_sock2.getsockname()[1]
    boot_proc = subprocess.Popen(
        [sys.executable, str(bootstrap), str(boot_port), str(boot_dir / "entry_app.py")],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    try:
        boot_hwnd = None
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                data, _addr = rep_sock2.recvfrom(256)
            except _socket.timeout:
                if boot_proc.poll() is not None:
                    break
                continue
            text = bytes(data).decode("ascii", errors="ignore").strip()
            if text.startswith("HWND"):
                boot_hwnd = int(text.split()[1])
                break
        out_tail = b""
        if boot_hwnd is None:
            try:
                out_tail = boot_proc.communicate(timeout=3)[0] or b""
            except Exception:
                pass
        check(
            "引导启动支持项目本地模块 import（utils）",
            boot_hwnd is not None or b"No module named" not in out_tail,
        )
        check("本地 import 型 GUI 上报窗口", bool(boot_hwnd))
    finally:
        try:
            boot_proc.kill()
        except Exception:
            pass

    # -- 归属校验：伪造句柄不属于任何已知进程/关键词 → 拒绝 ----------------------
    check("归属校验拒绝伪造句柄", not winembed.window_matches(0xDEADBEEF, pids={dead.pid}, title_keywords=None))

    # -- 托盘驻留场景：隐藏窗口按标题找回并恢复显示 ------------------------------
    hidden_script = (
        "import tkinter as tk\n"
        "r = tk.Tk()\n"
        "r.title('AFK-Hidden-Test')\n"
        "r.geometry('360x240+40+40')\n"
        "r.withdraw()\n"
        "r.mainloop()\n"
    )
    hidden_target = ROOT / "logs" / "_embed_tk_hidden.py"
    hidden_target.write_text(hidden_script, encoding="utf-8")
    hidden_proc = subprocess.Popen(
        [sys.executable, str(hidden_target)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    try:
        time.sleep(2.0)
        hwnd3, reason3 = winembed.find_window_for_spawn(
            pids=winembed.descendant_pids(hidden_proc.pid),
            title_keywords=["AFK-Hidden-Test"],
            timeout=10.0,
            proc=hidden_proc,
        )
        check("找回托盘驻留的隐藏窗口", bool(hwnd3))
        check("找回后窗口已恢复显示", bool(hwnd3) and winembed.window_alive(hwnd3))
    finally:
        try:
            hidden_proc.kill()
        except Exception:
            pass

    print()
    if FAILURES:
        print("EMBED-REAL {0} FAIL ({1})".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("EMBED-REAL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
