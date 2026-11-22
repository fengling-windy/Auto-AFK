"""项目状态页：列出每个挂机项目的版本、来源仓库与关键运行参数。

为什么要单独一页：总览的项目卡只放「状态 + 四个按钮」，空间不够再塞路径、
版本、仓库地址这些只读信息；「项目注册表」那一页是原始 YAML 文本，看懂它
需要先懂这套配置。这一页是给人看的汇总：这个项目从哪来、什么版本、按什么
参数跑、配置文件在哪。
"""

import subprocess
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from afk_core import config
from gui import icons
from gui.modern import AnimatedButton, ElidedLabel, enable_smooth_scroll

# 这些键不重复展示：display_name 已经在标题上、args 太长单独一行、
# 其余的 patterns/env/gui 之类属于实现细节，注册表页里能看到全文。
HIDDEN_KEYS = {
    "display_name",
    "base",
    "enabled",
    "args",
    "success_patterns",
    "error_patterns",
    "links",
    "sync_files",
    "env",
    "gui",
    "reserved",
}

# 参数名给人看的中文标签；没有的就直接用键名。
KEY_LABELS = {
    "launch_type": "启动方式",
    "path": "目录",
    "python": "解释器",
    "exe": "可执行文件",
    "config_dir": "配置目录",
    "config_name": "配置文件",
    "instance": "实例",
    "retry": "重试次数",
    "hang_timeout": "卡死超时(秒)",
    "require_admin": "需要管理员",
    "require_finish": "要求完成标记",
    "min_runtime_sec": "最短运行(秒)",
    "repo": "仓库",
    "version": "版本",
    "description": "说明",
}


def read_version(project_dir):
    """读子项目的 git 版本号（describe 优先，退化到短 hash）。

    子项目目录在 .gitignore 里，可能是 git clone 也可能只是一个解压出来的
    发行包——没有 .git 时返回空串，界面上显示「未知」，不报错。
    """
    from pathlib import Path

    path = Path(project_dir)
    if not path.is_dir():
        return ""
    for args in (
        ["git", "describe", "--tags", "--always", "--dirty"],
        ["git", "rev-parse", "--short", "HEAD"],
    ):
        try:
            out = subprocess.run(
                args,
                cwd=str(path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        text = (out.stdout or "").strip()
        if out.returncode == 0 and text:
            return text
    return ""


class _ProjectBlock(QFrame):
    """一个项目的汇总块。"""

    def __init__(self, key, spec, color, parent=None):
        super().__init__(parent)
        self.key = key
        self.spec = spec or {}
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        bar = QFrame()
        bar.setFixedSize(4, 18)
        bar.setStyleSheet("background: {0}; border-radius: 2px;".format(color))
        head.addWidget(bar)
        name = str(self.spec.get("display_name", key))
        title = ElidedLabel(name, min_width=80, max_hint_width=520)
        title.setObjectName("sectionTitleCard")
        title.setToolTip(name)
        head.addWidget(title, 1)
        self.state_label = QLabel("")
        self.state_label.setObjectName("hintLabel")
        head.addWidget(self.state_label)
        self.repo_btn = AnimatedButton("打开仓库", variant="mini")
        self.repo_btn.setIcon(icons.make("file", "#8b97ad", 12))
        self.repo_btn.setVisible(bool(self.spec.get("repo")))
        self.repo_btn.clicked.connect(self._open_repo)
        head.addWidget(self.repo_btn)
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.grid = grid
        self._rows = 0

        args = self.spec.get("args") or []
        if args:
            self._add_row("启动参数", " ".join(str(a) for a in args))
        for key_name, value in self.spec.items():
            if key_name in HIDDEN_KEYS:
                continue
            label = KEY_LABELS.get(key_name, key_name)
            self._add_row(label, self._format(value, key_name))
        # 注册表里没写 version 键时也要留一行：真实版本是后台去子项目目录里跑
        # git describe 读出来的，读完要有地方显示。历史缺陷：只在 spec 里有
        # version 键时才建这一行，于是「新增挂机项目」建出来（不填 version）
        # 的项目，后台扫到了版本也没有标签可写，界面上永远看不到版本号。
        if not hasattr(self, "_version_label"):
            self._add_row(KEY_LABELS["version"], "未知")

    @staticmethod
    def _format(value, key_name=""):
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        if isinstance(value, dict):
            return " / ".join("{0}: {1}".format(k, v) for k, v in value.items())
        return str(value)

    def _add_row(self, label, value):
        cap = QLabel(label)
        cap.setObjectName("hintLabel")
        cap.setFixedWidth(88)
        cap.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        text = ElidedLabel(str(value), min_width=60, max_hint_width=760)
        text.setObjectName("cardDetail")
        text.setToolTip(str(value))
        self.grid.addWidget(cap, self._rows, 0)
        self.grid.addWidget(text, self._rows, 1)
        self._rows += 1
        if label == KEY_LABELS["version"]:
            self._version_label = text

    def set_version(self, text):
        if not hasattr(self, "_version_label"):
            return
        if not text:
            # 子项目目录不是 git clone（发行包、手工解压）时扫不出东西，这时
            # 别把注册表里手写的 version 抹成「未知」——那是用户填的唯一线索。
            if self._version_label.text() not in ("", "未知"):
                return
            text = "未知"
        self._version_label.setText(text)
        self._version_label.setToolTip(text)

    def set_state(self, enabled):
        self.state_label.setText("已启用" if enabled else "已禁用")

    def _open_repo(self):
        repo = str(self.spec.get("repo") or "").strip()
        if not repo:
            return
        url = repo if repo.startswith("http") else "https://github.com/" + repo.strip("/")
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))


class ProjectStatusPage(QWidget):
    """项目状态页（版本 / 来源 / 关键参数）。"""

    # 后台线程读完版本后把 (key, version) 丢回 GUI 线程
    version_ready = Signal(str, str)

    def __init__(self, registry_getter=None, color_getter=None, log_fn=None, parent=None):
        super().__init__(parent)
        self._registry_getter = registry_getter or config.registry
        self._color_getter = color_getter or (lambda key: "#4fc3f7")
        self._log = log_fn or (lambda *a, **k: None)
        self._blocks = {}
        self._version_thread = None
        # 待扫队列：reload 时把目标追加进来，已经在跑的线程自己会继续消费，
        # 所以新增/改路径的项目也能拿到版本号（见 _start_version_scan）。
        self._scan_pending = []
        self._scan_lock = threading.Lock()
        self.version_ready.connect(self._apply_version)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("项目状态")
        title.setObjectName("sectionTitleCard")
        head.addWidget(title)
        head.addStretch(1)
        self.refresh_btn = AnimatedButton("刷新", variant="mini")
        self.refresh_btn.clicked.connect(self.reload)
        head.addWidget(self.refresh_btn)
        outer.addLayout(head)

        self.hint = QLabel(
            "显示每个项目从哪来、当前版本、按什么参数运行。"
            "版本号取自子项目目录的 git 信息，没有 git 目录时显示「未知」。"
        )
        self.hint.setObjectName("hintLabel")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        enable_smooth_scroll(scroll)
        inner = QWidget()
        inner.setObjectName("central")
        self._list_layout = QVBoxLayout(inner)
        self._list_layout.setContentsMargins(2, 2, 8, 8)
        self._list_layout.setSpacing(10)
        self._list_layout.addStretch(1)
        scroll.setWidget(inner)
        self._scroll = scroll
        outer.addWidget(scroll, 1)

        self.reload()

    # -- 构建 ---------------------------------------------------------------
    def reload(self):
        """按当前注册表重建列表（切换页面、保存注册表后都会调）。"""
        registry = self._registry_getter() or {}
        projects = registry.get("projects", {}) or {}
        # 清掉旧块（保留尾部 stretch）
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._blocks = {}
        for key, spec in projects.items():
            block = _ProjectBlock(key, spec, self._color_getter(key))
            block.set_state(bool((spec or {}).get("enabled", True)))
            self._blocks[key] = block
            self._list_layout.insertWidget(self._list_layout.count() - 1, block)
        self._start_version_scan(projects)

    def _start_version_scan(self, projects):
        """后台读各子项目 git 版本（每次 git 调用几十毫秒，串起来会让切页卡顿）。

        历史缺陷：只要上一轮扫描线程还活着就直接 return。用户在「项目注册表」
        里存盘（或新增项目）会立刻触发 reload，「新增挂机项目」建出来的那一张
        卡就永远停在「未知」——线程早就拿着旧的项目清单跑完了，没人再扫它。
        现在改成往同一个队列里追加目标：线程还活着就往队列里补，它自己会继续
        消费；线程已经结束就新起一个。
        """
        targets = []
        for key, spec in projects.items():
            spec = spec or {}
            path = spec.get("path")
            if not path:
                continue
            targets.append((key, str(config.ROOT / str(path))))
        if not targets:
            return
        with self._scan_lock:
            self._scan_pending.extend(
                t for t in targets if t not in self._scan_pending
            )
        if self._version_thread is not None and self._version_thread.is_alive():
            return
        self._version_thread = threading.Thread(
            target=self._version_worker, daemon=True, name="afk-versions"
        )
        self._version_thread.start()

    def _version_worker(self):
        """逐个消费待扫队列；扫完队列空才退出（期间新追加的目标也会被扫到）。"""
        while True:
            with self._scan_lock:
                if not self._scan_pending:
                    return
                key, path = self._scan_pending.pop(0)
            try:
                version = read_version(path)
            except Exception:
                version = ""
            self.version_ready.emit(key, version)

    def _apply_version(self, key, version):
        block = self._blocks.get(key)
        if block is not None:
            block.set_version(version)
