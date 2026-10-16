"""项目界面嵌入页：把各项目自己的配置 GUI 嵌入本窗口。

三种模式：
- 窗口嵌入：spawn → 按 PID 进程树/标题关键词/新增窗口 diff 找到窗口 →
  QWindow.fromWinId + createWindowContainer 嵌入（PySide6/exe 类 GUI）
- 网页嵌入（spec.gui.web）：spawn 时传固定端口 + --no-show → 等 HTTP 就绪 →
  QWebEngineView 内嵌显示（NiceGUI 等网页 GUI，不开系统浏览器）
- 静默模式（silent=True）：网页服务/窗口就绪后先隐藏，用户进入本页再显示

本页不自动启动：必须由用户点击「启动界面」按钮触发。
"""

import os
import threading
import time

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from gui import winembed
from gui.modern import AnimatedButton, StatusDot
from afk_core.supervisor import is_admin

# 侧栏指示灯：绿=界面已启动（含启动中/后台），灰=未启动
DOT_ON = "#34d399"
DOT_OFF = "#5c667d"


class ProjectPage(QWidget):
    STATE_IDLE = "idle"          # 未启动
    STATE_STARTING = "starting"  # 进程拉起中/找窗口(等服务)中
    STATE_EMBEDDED = "embedded"  # 已嵌入本窗口
    STATE_HIDDEN = "hidden"      # 静默启动：界面已就绪并隐藏
    STATE_FAILED = "failed"      # 启动失败/超时未找到

    hwnd_found = Signal(int)
    web_ready = Signal(int)
    find_failed = Signal()
    # 日志请求：_spawn_and_locate 在后台线程运行，而 log_fn（MainWindow._append_log）
    # 会写 log_view 控件。跨线程直接操作 Qt 控件可能崩溃或静默失效，因此统一用
    # Signal 把日志投递回 GUI 线程（Qt 会按接收者所在线程排队执行）。
    log_requested = Signal(str, str)

    FIND_TIMEOUT_SEC = 60.0
    REPORT_WAIT_SEC = 50.0   # 等待引导脚本上报 HWND
    FALLBACK_FIND_SEC = 15.0  # 上报超时后的回退匹配时长
    WEB_CHECK_EVERY = 10  # tick 间隔约 0.3s，每 ~3s 探测一次网页服务

    def __init__(self, key, spec, color, supervisor, log_fn, parent=None):
        super().__init__(parent)
        self.key = key
        self.spec = spec
        self.color = color
        self.supervisor = supervisor
        self._log_fn = log_fn
        self.log_requested.connect(self._on_log_requested)
        self._gui_cfg = spec.get("gui") or {}
        self._is_web = bool(self._gui_cfg.get("web"))
        self._state = self.STATE_IDLE
        self._hwnd = None
        self._port = None
        self._container = None
        self._web_view = None
        self._web_profile = None
        self._silent = False
        self._stopped = False
        self._find_thread = None
        self._web_fail_count = 0
        self._tick_count = 0
        self._spawn_pids = set()
        self._fail_reason = None
        self._proc = None

        self.hwnd_found.connect(self._on_hwnd_found)
        self.web_ready.connect(self._on_web_ready)
        self.find_failed.connect(self._on_find_failed)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        bar = QWidget()
        bar.setObjectName("pageBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(14, 10, 14, 10)
        bar_layout.setSpacing(10)
        self.dot = StatusDot(color)
        self.name_label = QLabel(str(spec.get("display_name", key)).split("（")[0])
        self.name_label.setObjectName("pageTitle")
        self.state_label = QLabel("未启动")
        self.state_label.setObjectName("pageState")
        self.start_btn = AnimatedButton("启动界面", variant="primary")
        self.start_btn.setObjectName("primary")
        self.logs_btn = AnimatedButton("打开日志")
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.logs_btn.clicked.connect(self._open_logs)
        bar_layout.addWidget(self.dot)
        bar_layout.addWidget(self.name_label)
        bar_layout.addSpacing(6)
        bar_layout.addWidget(self.state_label)
        bar_layout.addStretch(1)
        bar_layout.addWidget(self.start_btn)
        bar_layout.addWidget(self.logs_btn)
        root.addWidget(bar)

        self.body = QStackedWidget()
        self.placeholder = QWidget()
        placeholder_layout = QVBoxLayout(self.placeholder)
        placeholder_layout.setAlignment(Qt.AlignCenter)
        mode_hint = "启动后在内置浏览器中显示" if self._is_web else "Auto-AFK 直接启动项目界面并内嵌到本页"
        self.placeholder_text = QLabel(
            "项目界面未启动\n\n点击右上角「启动界面」一次，{0}".format(mode_hint)
        )
        self.placeholder_text.setObjectName("pagePlaceholder")
        self.placeholder_text.setAlignment(Qt.AlignCenter)
        placeholder_layout.addWidget(self.placeholder_text)
        self.retry_btn = AnimatedButton("重新检测")
        self.retry_btn.setVisible(False)
        placeholder_layout.addWidget(self.retry_btn, 0, Qt.AlignHCenter)
        self.retry_btn.clicked.connect(self._retry_find)
        self.body.addWidget(self.placeholder)
        root.addWidget(self.body, 1)

        self._set_state(self.STATE_IDLE, "未启动")

    # -- 状态 ------------------------------------------------------------------
    def _set_state(self, state, text, dot_color=None, pulse=False):
        self._state = state
        self.state_label.setText(text)
        self.dot.set_state(dot_color or self.color, pulse=pulse)
        self.start_btn.setText(self._start_btn_text(state))
        self.start_btn.setEnabled(state in (self.STATE_IDLE, self.STATE_FAILED))
        self.retry_btn.setVisible(state == self.STATE_FAILED)

    def _start_btn_text(self, state):
        if state == self.STATE_EMBEDDED:
            return "已嵌入"
        if state == self.STATE_HIDDEN:
            return "已在后台"
        if state == self.STATE_STARTING:
            return "启动中..."
        return "启动界面"

    def dot_state(self):
        """侧栏指示灯：界面已启动（启动中/已嵌入/后台）返回绿色。"""
        return DOT_ON if self._state in (
            self.STATE_STARTING, self.STATE_EMBEDDED, self.STATE_HIDDEN
        ) else DOT_OFF

    def _open_logs(self):
        self._log(self.key, "info", "打开项目日志")

    # -- 启动 ------------------------------------------------------------------
    def ensure_started(self, silent=False):
        """拉起项目 GUI 并定位窗口/等待服务；silent=True 时就绪后先隐藏。"""
        if self._state == self.STATE_EMBEDDED:
            return
        if self._state == self.STATE_HIDDEN:
            # 用户主动点进来：把隐藏的界面显示出来
            if self._is_web:
                self._embed_web(self._port)
            else:
                self._embed(self._hwnd)
            return
        if self._state == self.STATE_STARTING and self._find_thread is not None and self._find_thread.is_alive():
            self._silent = self._silent and silent
            return
        self._silent = bool(silent)
        self._stopped = False
        self._set_state(self.STATE_STARTING, "正在启动界面进程...", pulse=True)
        self._log(self.key, "info", "启动项目界面{0}".format("（静默）" if silent else ""))
        self._find_thread = threading.Thread(target=self._spawn_and_locate, daemon=True, name="afk-embed-" + self.key)
        self._find_thread.start()

    def _on_start_clicked(self):
        if self._state in (self.STATE_IDLE, self.STATE_FAILED):
            self.ensure_started(silent=False)

    def _retry_find(self):
        if self._is_web and self._port is not None and winembed.wait_http(self._port, timeout=1.5):
            self._embed_web(self._port)
            return
        if not self._is_web and self._hwnd is not None and winembed.window_alive(self._hwnd):
            self._embed(self._hwnd)
            return
        self._hwnd = None
        self._port = None
        self.ensure_started(silent=False)

    def _uses_bootstrap(self):
        """python 入口的 GUI 用引导脚本启动（进程内上报 HWND）；exe 不适用。"""
        return (
            self.spec.get("launch_type", "python") == "python"
            and not self._is_web
            and not self._gui_cfg.get("exe")
        )

    def _spawn_and_locate(self):
        # 需要管理员权限的项目：非管理员启动时项目会自行弹 UAC 提权重启，
        # 进程树脱离且依赖用户确认，直接给出明确提示而不是等待/误抓
        if self.spec.get("require_admin") and not is_admin():
            self._fail_reason = "该项目界面需要管理员权限：请用「Auto-AFK(管理员)」启动后再试"
            self.find_failed.emit()
            return
        report_sock = None
        report_port = None
        if self._uses_bootstrap():
            import socket as _socket

            report_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            report_sock.bind(("127.0.0.1", 0))
            report_port = report_sock.getsockname()[1]
        if self._is_web:
            port = winembed.pick_free_port()
            extra = []
            no_show_arg = self._gui_cfg.get("no_show_arg", "--no-show")
            if no_show_arg:
                extra.append(str(no_show_arg))
            extra.extend([str(self._gui_cfg.get("port_arg", "--port")), str(port)])
            proc = self.supervisor.spawn_project_gui(self.key, extra_args=extra)
            if proc is None:
                self.find_failed.emit()
                return
            self._proc = proc
            if self._stopped:
                return
            if winembed.wait_http(port, timeout=self.FIND_TIMEOUT_SEC):
                if not self._stopped:
                    self.web_ready.emit(int(port))
            elif not self._stopped:
                self.find_failed.emit()
            return
        proc = self.supervisor.spawn_project_gui(self.key, report_port=report_port)
        if proc is None:
            self.find_failed.emit()
            return
        self._proc = proc
        self._spawn_pids = winembed.descendant_pids(proc.pid)
        hwnd = None
        if report_sock is not None:
            # 首选：项目进程内的引导脚本主动上报主窗口 HWND（零搜索、零误抓）
            hwnd = self._recv_reported_hwnd(report_sock, timeout=self.REPORT_WAIT_SEC)
            try:
                report_sock.close()
            except Exception:
                pass
            if self._stopped:
                return
            if hwnd is not None and not winembed.window_matches(
                hwnd, pids=self._spawn_pids, title_keywords=self._gui_cfg.get("gui_window_keywords")
            ):
                self._log(self.key, "warn", "上报窗口归属校验未通过，回退到进程树匹配")
                hwnd = None
        if hwnd is None:
            # 回退：PID 进程树 + 显式标题关键词（无模糊匹配）
            hwnd, reason = winembed.find_window_for_spawn(
                pids=self._spawn_pids,
                title_keywords=self._gui_cfg.get("gui_window_keywords"),
                timeout=self.FALLBACK_FIND_SEC,
                proc=proc,
            )
        else:
            reason = None
        if self._stopped:
            return
        if hwnd:
            self.hwnd_found.emit(int(hwnd))
        else:
            self._fail_reason = reason
            self.find_failed.emit()

    def _recv_reported_hwnd(self, sock, timeout):
        """等待引导脚本通过 UDP 上报的主窗口 HWND；超时返回 None。

        首次上报后继续收集一小段窗口：有的 GUI 会先弹启动画面再建主窗，
        取最后一个上报（最新的窗口）。"""
        import socket as _socket

        deadline = time.monotonic() + float(timeout)
        sock.settimeout(0.4)
        last_hwnd = None
        settle_deadline = None
        while time.monotonic() < deadline and not self._stopped:
            try:
                data, _addr = sock.recvfrom(256)
            except _socket.timeout:
                if last_hwnd is not None and settle_deadline is not None and time.monotonic() >= settle_deadline:
                    break
                continue
            except Exception:
                return last_hwnd
            try:
                text = bytes(data).decode("ascii", errors="ignore").strip()
                if text.startswith("HWND"):
                    last_hwnd = int(text.split()[1])
                    if settle_deadline is None:
                        settle_deadline = time.monotonic() + 2.5
            except Exception:
                continue
        return last_hwnd

    def _log(self, project, level, message):
        """记录日志：始终经 Signal 投递到 GUI 线程执行。

        这个方法只做「发信号」这一件事——它可能被后台线程（afk-embed-*）调用，
        直接写 log_view 属于跨线程操作 Qt 控件。
        """
        try:
            self.log_requested.emit(str(level), str(message))
        except Exception:
            pass

    def _on_log_requested(self, level, message):
        """在 GUI 线程真正写日志（由 log_requested 信号投递进来）。"""
        try:
            self._log_fn(self.key, level, message)
        except Exception:
            pass

    def _on_find_failed(self):
        if self._stopped:
            return
        reason = getattr(self, "_fail_reason", None) or "未检测到界面窗口（可重试或独立使用）"
        self._set_state(self.STATE_FAILED, reason, dot_color="#f87171")
        self._log(self.key, "warn", reason)

    # -- 嵌入：窗口模式 ----------------------------------------------------------
    def _on_hwnd_found(self, hwnd):
        if self._stopped:
            return
        self._hwnd = int(hwnd)
        # 嵌入前归属校验（双保险）：窗口必须属于 spawn 进程树或标题匹配配置关键词
        keywords = self._gui_cfg.get("gui_window_keywords")
        if not winembed.window_matches(self._hwnd, pids=self._spawn_pids, title_keywords=keywords):
            self._log(self.key, "warn", "发现的窗口不属于本项目，已拒绝嵌入")
            self._fail_reason = "发现的窗口不属于本项目，已拒绝嵌入"
            self.find_failed.emit()
            return
        if self._silent and not self.isVisible():
            # 静默启动：藏起来，等用户切到该页再嵌入
            winembed.show_hwnd(self._hwnd, show=False)
            self._set_state(self.STATE_HIDDEN, "已在后台运行（点击本页嵌入显示）")
            self._log(self.key, "info", "界面窗口已静默隐藏，切换到本页时嵌入")
            return
        self._embed(self._hwnd)

    def _embed(self, hwnd):
        if hwnd is None or not winembed.window_alive(hwnd):
            self._set_state(self.STATE_FAILED, "窗口已失效，请重新启动")
            return
        try:
            qwin = QWindow.fromWinId(int(hwnd))
            qwin.show()
            container = QWidget.createWindowContainer(qwin, self)
        except Exception as exc:
            self._log(self.key, "error", "嵌入界面窗口失败: {0}".format(exc))
            self._set_state(self.STATE_FAILED, "嵌入失败，可重试")
            return
        self._container = container
        self.body.addWidget(container)
        self.body.setCurrentWidget(container)
        self._set_state(self.STATE_EMBEDDED, "已嵌入（项目界面随本窗口缩放）")
        self._log(self.key, "info", "项目界面已嵌入窗口")

    # -- 嵌入：网页模式 ----------------------------------------------------------
    def _on_web_ready(self, port):
        if self._stopped:
            return
        self._port = int(port)
        if self._silent and not self.isVisible():
            self._set_state(self.STATE_HIDDEN, "网页界面已在后台运行（点击本页显示）")
            self._log(self.key, "info", "网页界面已就绪并静默隐藏，切换到本页时显示")
            return
        self._embed_web(self._port)

    def _embed_web(self, port):
        url = "http://127.0.0.1:{0}/".format(int(port))
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
        except Exception as exc:
            self._log(self.key, "warn", "内置浏览器不可用（{0}），改用系统浏览器打开".format(exc))
            try:
                os.startfile(url)
                self._set_state(self.STATE_FAILED, "已用系统浏览器打开（无法内嵌显示）", dot_color="#fb923c")
            except Exception:
                self._set_state(self.STATE_FAILED, "无法显示网页界面")
            return
        if self._web_view is not None:
            try:
                self._web_view.stop()
            except Exception:
                pass
            self._web_view.deleteLater()
            self._web_view = None
        if self._web_profile is not None:
            self._web_profile.deleteLater()
            self._web_profile = None
        view = QWebEngineView()
        # 每页独立的非持久化 profile：全新会话，不带上一轮的 cookie/storage，
        # 避免会话恢复引起的页面重载，也不与用户浏览器互相污染
        profile = QWebEngineProfile(self)
        page = QWebEnginePage(profile, view)
        view.setPage(page)
        self._web_profile = profile
        view.load(QUrl(url))
        self._web_view = view
        self.body.addWidget(view)
        self.body.setCurrentWidget(view)
        self._web_fail_count = 0
        self._set_state(self.STATE_EMBEDDED, "已嵌入（内置浏览器）")
        self._log(self.key, "info", "网页界面已嵌入：{0}".format(url))

    def _remove_embed(self):
        if self._web_view is not None:
            try:
                self._web_view.stop()
            except Exception:
                pass
            self._web_view.deleteLater()
            self._web_view = None
        if self._web_profile is not None:
            self._web_profile.deleteLater()
            self._web_profile = None
        if self._container is not None:
            self._container.deleteLater()
            self._container = None
        self._hwnd = None
        self._port = None
        self.body.setCurrentWidget(self.placeholder)

    # -- 轮询 ------------------------------------------------------------------
    def tick(self):
        """由 MainWindow._poll 周期调用：检测嵌入的界面是否被关闭。"""
        self._tick_count += 1
        if self._state == self.STATE_EMBEDDED and not self._is_web and self._hwnd is not None:
            if not winembed.window_alive(self._hwnd):
                self._remove_embed()
                self._set_state(self.STATE_IDLE, "界面已关闭")
                self._log(self.key, "info", "项目界面窗口已关闭")
            return
        if self._state == self.STATE_EMBEDDED and self._is_web and self._port is not None:
            # 每 ~3s 探测一次网页服务是否存活（0.5s 短超时，尽量不阻塞 UI 线程）
            if self._tick_count % self.WEB_CHECK_EVERY:
                return
            if winembed.wait_http(self._port, timeout=0.5, poll=0.1):
                self._web_fail_count = 0
            else:
                self._web_fail_count += 1
                if self._web_fail_count >= 3:
                    self._remove_embed()
                    self._set_state(self.STATE_IDLE, "网页服务已停止")
                    self._log(self.key, "info", "网页界面服务已停止")

    def shutdown(self):
        """Auto-AFK 退出时关闭本项目启动的界面：窗口投递 WM_CLOSE，进程树结束。"""
        self._stopped = True
        if self._hwnd is not None and winembed.window_alive(self._hwnd):
            winembed.close_hwnd(self._hwnd)
        proc = self._proc
        if proc is not None and proc.poll() is None:
            winembed.kill_tree(proc.pid)

    def stop(self):
        self._stopped = True
