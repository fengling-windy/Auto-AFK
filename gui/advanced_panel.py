"""设置页里的「高级设置」区块：错误报告 / 项目注册表 / 设置原文 / 修复规则 / 环境自检。

历史（2026-11-23 用户反馈）：这五项原先是总览页下方 AnimatedTabWidget 的页签，
和「实时日志」挤在同一条页签栏里。那排行当导航看起来太重，实时日志本身也不需要
页签，于是页签整套去掉——实时日志直接在总览页显示，其余五项收进设置页末尾的
折叠卡片，默认收起，要用的时候点开。

为什么拆成独立模块而不是继续堆在 MainWindow：这五块都是「一个编辑器 + 几个按钮」
的同构结构，放在主窗口里会把 _build_ui 再撑长两百行（它已经 300 行了）。
这里只管展示与按钮回调，落盘校验、环境自检执行、打开目录都通过构造参数注入，
主窗口仍然是唯一的业务入口。
"""

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QFont

from afk_core import config, report
from gui.modern import AnimatedButton, enable_smooth_scroll


class _Collapsible(QFrame):
    """一个可折叠的区块：标题行（点击展开/收起）+ 内容区。

    默认收起：这五块都是低频操作（看错误报告、手改 yaml、跑自检），展开着会把
    设置页拉得很长，用户还得往下滚才能看到常用的「运行顺序」。
    """

    def __init__(self, title, hint="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._expanded = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.toggle_btn = AnimatedButton("", variant="mini")
        self.toggle_btn.setObjectName("mini")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.clicked.connect(self.toggle)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitleCard")
        head.addWidget(self.toggle_btn)
        head.addWidget(self.title_label, 1)
        outer.addLayout(head)

        if hint:
            self.hint_label = QLabel(hint)
            self.hint_label.setObjectName("hintLabel")
            self.hint_label.setWordWrap(True)
            outer.addWidget(self.hint_label)

        self._content = QWidget()
        self.body = QVBoxLayout(self._content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)
        outer.addWidget(self._content)
        self._content.setVisible(False)
        self._sync_button()

    def _sync_button(self):
        self.toggle_btn.setText("收起" if self._expanded else "展开")
        self.toggle_btn.setChecked(self._expanded)

    def is_expanded(self):
        return self._expanded

    def set_expanded(self, expanded):
        self._expanded = bool(expanded)
        self._content.setVisible(self._expanded)
        self._sync_button()

    def toggle(self):
        self.set_expanded(not self._expanded)


class AdvancedPanel(QWidget):
    """设置页末尾的「高级设置」：五个可折叠区块。

    参数都是回调，主窗口注入自己的实现（落盘校验要弹 QMessageBox、自检要起后台
    线程、打开目录要用 os.startfile），这里只负责布局与按钮。
    """

    def __init__(self, save_text=None, load_text=None, open_path=None,
                 run_doctor=None, parent=None):
        super().__init__(parent)
        self._save_text_cb = save_text
        self._load_text_cb = load_text
        self._open_path_cb = open_path
        self._run_doctor_cb = run_doctor

        # 「设置原文」编辑器的脏标记：用户在别处（头部开关 / 设置页控件）改了配置，
        # 回到这里按「保存设置」会把过期文本整份写回，刚改的配置被静默覆盖。
        self._settings_edit_dirty = False
        self._settings_edit_loading = False
        self._reports_loaded = False
        self._reports_mtime = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        cap = QLabel("高级设置")
        cap.setObjectName("sectionTitle")
        outer.addWidget(cap)

        self.sections = {}
        outer.addWidget(self._build_reports_section())
        outer.addWidget(self._build_registry_section())
        outer.addWidget(self._build_settings_section())
        outer.addWidget(self._build_repair_section())
        outer.addWidget(self._build_doctor_section())
        outer.addStretch(1)

    # -- 各个区块 ---------------------------------------------------------------
    def _mono_edit(self):
        edit = QPlainTextEdit()
        edit.setFont(QFont("Consolas", 9))
        enable_smooth_scroll(edit)
        return edit

    def _button_row(self, save_text, path, load_label="重新加载"):
        row = QHBoxLayout()
        row.setSpacing(8)
        save = AnimatedButton(save_text, variant="primary")
        save.setObjectName("primary")
        save.clicked.connect(lambda: self.save_text(self._current_edit_for(path), path))
        reload_btn = AnimatedButton(load_label)
        reload_btn.clicked.connect(lambda: self.load_text(self._current_edit_for(path), path))
        open_btn = AnimatedButton("打开所在目录")
        open_btn.clicked.connect(lambda: self._open_path(Path(path).parent))
        row.addWidget(save)
        row.addWidget(reload_btn)
        row.addWidget(open_btn)
        row.addStretch(1)
        return row

    def _current_edit_for(self, path):
        """按文件路径找回对应的编辑器（按钮回调里只拿到 path）。"""
        path = Path(path)
        if path == Path(config.SETTINGS_YAML):
            return self.settings_edit
        if path == Path(config.PROJECTS_YAML):
            return self.registry_edit
        if path == Path(config.REPAIR_YAML):
            return self.repair_edit
        return self.settings_edit

    def _build_reports_section(self):
        section = _Collapsible(
            "错误报告",
            "项目异常退出时会生成一份报告（退出码、最后几十行日志、修复动作）。"
            "列表按目录修改时间自动刷新：有报告跑出来、或被清理过，打开本区块就能看到。",
        )
        controls = QHBoxLayout()
        refresh_btn = AnimatedButton("刷新")
        refresh_btn.clicked.connect(self.refresh_reports)
        open_btn = AnimatedButton("打开选中报告")
        open_btn.clicked.connect(self.open_selected_report)
        controls.addWidget(refresh_btn)
        controls.addWidget(open_btn)
        controls.addStretch(1)
        section.body.addLayout(controls)
        self.report_list = QListWidget()
        self.report_list.setMinimumHeight(140)
        enable_smooth_scroll(self.report_list)
        self.report_list.itemDoubleClicked.connect(lambda _item: self.open_selected_report())
        section.body.addWidget(self.report_list)
        self.sections["reports"] = section
        return section

    def _build_registry_section(self):
        section = _Collapsible(
            "项目注册表",
            "afk_projects.yaml 原文。改完点「保存项目注册表」，总览的卡片与导航会跟着刷新。",
        )
        self.registry_edit = self._mono_edit()
        self.load_text(self.registry_edit, config.PROJECTS_YAML)
        section.body.addLayout(
            self._button_row("保存项目注册表", config.PROJECTS_YAML)
        )
        self.registry_edit.setMinimumHeight(160)
        section.body.addWidget(self.registry_edit)
        self.sections["registry"] = section
        return section

    def _build_settings_section(self):
        section = _Collapsible(
            "设置原文",
            "settings.yaml 原文。平时用上面的控件改就行，这里留给配置文件里没有对应"
            "控件的条目。保存前会校验 YAML 语法与顶层结构。",
        )
        self.settings_edit = self._mono_edit()
        self.settings_edit.textChanged.connect(self._on_settings_text_changed)
        self.load_text(self.settings_edit, config.SETTINGS_YAML)
        section.body.addLayout(self._button_row("保存设置", config.SETTINGS_YAML))
        self.settings_edit.setMinimumHeight(160)
        section.body.addWidget(self.settings_edit)
        self.sections["settings"] = section
        return section

    def _build_repair_section(self):
        section = _Collapsible(
            "修复规则",
            "repair_rules.yaml 原文：项目失败时的自动修复动作（重启模拟器、清缓存…）。",
        )
        self.repair_edit = self._mono_edit()
        self.load_text(self.repair_edit, config.REPAIR_YAML)
        section.body.addLayout(self._button_row("保存修复规则", config.REPAIR_YAML))
        self.repair_edit.setMinimumHeight(140)
        section.body.addWidget(self.repair_edit)
        self.sections["repair"] = section
        return section

    def _build_doctor_section(self):
        section = _Collapsible(
            "环境自检",
            "检查 git / uv / node / pnpm / 管理员权限 / 模拟器连通性，结果直接列在下面。",
        )
        controls = QHBoxLayout()
        run_btn = AnimatedButton("运行自检", variant="primary")
        run_btn.setObjectName("primary")
        run_btn.clicked.connect(self.run_doctor)
        controls.addWidget(run_btn)
        controls.addStretch(1)
        section.body.addLayout(controls)
        self.doctor_view = QPlainTextEdit()
        self.doctor_view.setReadOnly(True)
        self.doctor_view.setFont(QFont("Consolas", 9))
        self.doctor_view.setMinimumHeight(140)
        enable_smooth_scroll(self.doctor_view)
        section.body.addWidget(self.doctor_view)
        self.sections["doctor"] = section
        return section

    # -- 折叠状态 ---------------------------------------------------------------
    def expand(self, name):
        """展开指定区块并收起其他区块（用户点「自检」时不该同时占着报告列表）。"""
        for key, section in self.sections.items():
            section.set_expanded(key == name)
        target = self.sections.get(name)
        if target is not None:
            target.setVisible(True)
        if name == "reports":
            # 展开就该看到当前磁盘状态：报告列表原本只在点「刷新」时填，
            # 用户展开后看到的是空列表（磁盘上其实有几十份）。
            self.maybe_refresh_reports()

    def section(self, name):
        return self.sections.get(name)

    def expanded_name(self):
        for key, section in self.sections.items():
            if section.is_expanded():
                return key
        return None

    # -- 文本读写 ---------------------------------------------------------------
    def load_text(self, edit, path):
        """把文件内容读进编辑器；读取失败时把错误写进控件而不是抛。"""
        # 往「设置原文」里灌内容本身不算用户编辑，必须挡住 textChanged 的脏
        # 标记。历史缺陷：这里直接 setPlainText，_on_settings_text_changed 立刻
        # 把 _settings_edit_dirty 置 True，之后 _mutate_settings 想同步编辑器
        # 就被「用户正在写，别覆盖」挡回去，编辑器停在旧内容上；用户再点保存
        # 就把头部刚改的值整份覆盖回旧值（实测 pre_run.enabled 改 False 后又被
        # 写回 True）。
        is_settings = edit is getattr(self, "settings_edit", None)
        if is_settings:
            self._settings_edit_loading = True
        try:
            try:
                # config.read_text 会退避重试：别的线程刚做了原子替换时目标文件会被
                # 短暂占用，直接 read_text 抛 PermissionError，界面显示「# 读取失败」
                # ——用户以为文件坏了，其实只是撞了一下。
                text = config.read_text(Path(path))
            except Exception as exc:
                text = "# 读取失败: {0}".format(exc)
            edit.setPlainText(text)
        finally:
            if is_settings:
                self._settings_edit_loading = False

    def _on_settings_text_changed(self):
        if self._settings_edit_loading:
            return
        self._settings_edit_dirty = True

    def mark_settings_loaded(self):
        self._settings_edit_dirty = False

    def sync_settings_editor(self):
        """设置文件在别处被改后，若编辑器没被用户动过就跟着刷新。"""
        if self._settings_edit_dirty:
            return
        self._settings_edit_loading = True
        try:
            self.load_text(self.settings_edit, config.SETTINGS_YAML)
            self._settings_edit_dirty = False
        finally:
            self._settings_edit_loading = False

    def save_text(self, edit, path):
        """保存编辑器内容；yaml 先做语法与顶层结构校验，失败就弹框且不落盘。"""
        if self._save_text_cb is not None:
            self._save_text_cb(edit, path)
            return
        config.write_text_atomic(Path(path), edit.toPlainText())

    # -- 错误报告 ---------------------------------------------------------------
    def _reports_dir_mtime(self):
        try:
            return config.REPORTS_DIR.stat().st_mtime_ns
        except OSError:
            return None

    def refresh_reports(self):
        """重扫报告目录并重建列表；尽量保留用户当前选中的报告。"""
        selected = None
        current = self.report_list.currentItem()
        if current is not None:
            selected = current.data(Qt.UserRole)
        self.report_list.clear()
        for item in report.list_reports(limit=200):
            entry = "{0}  [{1}] {2}".format(
                item["name"], item.get("project", ""), item.get("reason", "")
            )
            list_item = QListWidgetItem(entry)
            list_item.setData(Qt.UserRole, item["dir"])
            self.report_list.addItem(list_item)
        self._reports_loaded = True
        self._reports_mtime = self._reports_dir_mtime()
        if self.report_list.count() == 0:
            placeholder = QListWidgetItem("（还没有错误报告：项目异常退出时才会生成）")
            placeholder.setFlags(Qt.NoItemFlags)
            placeholder.setData(Qt.UserRole, None)
            self.report_list.addItem(placeholder)
        elif selected:
            for row in range(self.report_list.count()):
                if self.report_list.item(row).data(Qt.UserRole) == selected:
                    self.report_list.setCurrentRow(row)
                    break

    def maybe_refresh_reports(self):
        """进入设置页时调用：目录没变就复用列表（一次 stat，微秒级）。"""
        if self._reports_loaded and self._reports_mtime == self._reports_dir_mtime():
            return
        self.refresh_reports()

    def open_selected_report(self):
        item = self.report_list.currentItem()
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        self._open_path(path)

    # -- 回调包装 ---------------------------------------------------------------
    def _open_path(self, path):
        if self._open_path_cb is not None:
            self._open_path_cb(path)
            return
        target = Path(path)
        if not target.exists():
            QMessageBox.warning(self, "打开失败", "路径不存在：\n{0}".format(target))
            return
        try:
            os.startfile(str(target))
        except OSError as exc:
            QMessageBox.warning(self, "打开失败", "{0}\n{1}".format(target, exc))

    def run_doctor(self):
        if self._run_doctor_cb is None:
            QMessageBox.information(self, "环境自检", "没有可用的自检实现。")
            return
        self._run_doctor_cb()
