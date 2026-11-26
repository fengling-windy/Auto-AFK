"""运行顺序页：可拖动重排任务卡片、设置相邻并行组、为同项目指定不同配置并添加多条任务。"""

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from afk_core import config, listval, project_ops
from gui import themes
from gui.cards import CARDS_MIN_HEIGHT, CardsScrollArea, ProjectCard
from gui.modern import (
    AnimatedButton,
    FlowHost,
    ParallelLinks,
    add_shadow,
    current_theme,
    enable_smooth_scroll,
    links_color,
)
from gui.spring import pick_drag_slot


class RunOrderPage(QWidget):
    """独立「运行顺序」页：支持同项目多配置任务与并行连线。"""

    order_changed = Signal()
    start_requested = Signal(str)
    stop_requested = Signal(str)
    logs_requested = Signal(str)
    gui_requested = Signal(str)

    DRAG_ANIM_MS = 90

    def __init__(
        self,
        registry_getter=None,
        settings_getter=None,
        color_getter=None,
        log_fn=None,
        runs_getter=None,
        parent=None,
    ):
        super().__init__(parent)
        self._registry_getter = registry_getter or config.registry
        self._settings_getter = settings_getter or config.settings
        self._color_getter = color_getter or (
            lambda key: themes.project_fallback_color(current_theme(), key)
        )
        self._log = log_fn or (lambda *a, **k: None)
        self._runs_getter = runs_getter or (lambda: {})
        self.cards = {}
        self._tasks = []
        self._parallel_blocks = []
        self._parallel_pending = None
        self._drag_key = None
        self._drag_grab = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(10)

        bar = QFrame()
        bar.setObjectName("header")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 11, 16, 11)
        bar_layout.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("运行顺序")
        title.setObjectName("sectionTitleCard")
        hint = QLabel("按住卡片任意区域拖动调整执行先后；点相邻卡片「并行」同时运行；可为同一项目添加多个不同配置的任务。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        title_col.addWidget(title)
        title_col.addWidget(hint)
        bar_layout.addLayout(title_col, 1)

        add_box = QHBoxLayout()
        add_box.setSpacing(6)
        self.add_project_combo = QComboBox()
        self.add_project_combo.setMinimumWidth(140)
        self.add_project_combo.currentIndexChanged.connect(self._refresh_add_config_combo)
        self.add_config_combo = QComboBox()
        self.add_config_combo.setMinimumWidth(120)
        self.add_task_btn = AnimatedButton("+ 添加任务", variant="primary")
        self.add_task_btn.setObjectName("primary")
        self.add_task_btn.clicked.connect(self._on_add_task)
        add_box.addWidget(self.add_project_combo)
        add_box.addWidget(self.add_config_combo)
        add_box.addWidget(self.add_task_btn)
        bar_layout.addLayout(add_box)
        outer.addWidget(bar)

        self.cards_host = FlowHost(h_spacing=20, v_spacing=12, vertical_center=True)
        self.links = ParallelLinks(self.cards_host)
        self.links.set_color(links_color())
        self.cards_host.set_anim_step_hook(self.links.update)
        self.cards_host.set_resize_hook(lambda: self.links.setGeometry(self.cards_host.rect()))

        self.cards_scroll = CardsScrollArea()
        self.cards_scroll.setObjectName("cardsScroll")
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QFrame.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards_scroll.setMinimumHeight(CARDS_MIN_HEIGHT)
        self.cards_scroll.setWidget(self.cards_host)
        self.cards_scroll.viewport().installEventFilter(self)
        enable_smooth_scroll(self.cards_scroll)
        outer.addWidget(self.cards_scroll, 1)

        self.reload_tasks()

    # -- 数据与控件构建 -------------------------------------------------------
    def _populate_add_combos(self, registry=None):
        projects = (registry or self._registry_getter() or {}).get("projects", {}) or {}
        current = self.add_project_combo.currentData()
        self.add_project_combo.blockSignals(True)
        self.add_project_combo.clear()
        for key, spec in projects.items():
            short = str((spec or {}).get("display_name", key)).split("（")[0]
            self.add_project_combo.addItem(short, key)
        idx = self.add_project_combo.findData(current)
        self.add_project_combo.setCurrentIndex(max(0, idx))
        self.add_project_combo.blockSignals(False)
        self.add_task_btn.setEnabled(bool(projects))
        self._refresh_add_config_combo(registry=registry)

    def _refresh_add_config_combo(self, _idx=0, registry=None):
        proj = str(self.add_project_combo.currentData() or "")
        projects = (registry or self._registry_getter() or {}).get("projects", {}) or {}
        spec = projects.get(proj) or {}
        self.add_config_combo.blockSignals(True)
        self.add_config_combo.clear()
        self.add_config_combo.addItem("默认配置", "")
        if proj:
            for label, val in project_ops.discover_project_configs(proj, spec):
                self.add_config_combo.addItem(label, val)
        self.add_config_combo.blockSignals(False)

    def _make_card(self, task, spec, config_options):
        tid = task["id"]
        proj = task["project"]
        card = ProjectCard(
            tid,
            spec,
            self._color_getter(proj),
            self.start_requested.emit,
            self.stop_requested.emit,
            self.logs_requested.emit,
            on_gui=self.gui_requested.emit if (spec or {}).get("gui") else None,
            on_parallel=self.toggle_parallel,
            on_remove=self.remove_task,
            config_options=config_options,
            current_config=task.get("config", ""),
            on_config_change=self._on_config_change,
            base_project=proj,
        )
        card.drag_started.connect(self._on_card_drag_started)
        card.drag_moved.connect(self._on_card_drag_moved)
        card.drag_finished.connect(self._on_card_drag_finished)
        return card

    def reload_tasks(self, settings=None, registry=None):
        settings = settings if settings is not None else self._settings_getter()
        registry = registry if registry is not None else self._registry_getter()
        projects = (registry or {}).get("projects", {}) or {}
        tasks = project_ops.load_tasks(settings, registry)
        self._tasks = list(tasks)
        self._parallel_blocks = listval.as_str_groups(
            ((settings or {}).get("chain") or {}).get("parallel_blocks"),
            what="chain.parallel_blocks",
        )
        self._parallel_pending = None
        self._populate_add_combos(registry=registry)

        flow = self.cards_host.flow()
        for card in list(self.cards.values()):
            flow.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.cards = {}

        cfg_cache = {}
        for task in tasks:
            proj = task["project"]
            spec = projects.get(proj) or {}
            if proj not in cfg_cache:
                cfg_cache[proj] = project_ops.discover_project_configs(proj, spec)
            card = self._make_card(task, spec, cfg_cache[proj])
            self.cards[task["id"]] = card
            flow.addWidget(card)

        self._prune_parallel_blocks(persist=False)
        self._sync_parallel_state()
        self._sync_cards_height()
        runs = self._runs_getter() or {}
        if runs:
            self.update_runs(runs)

    # -- 任务增删 / 配置切换 --------------------------------------------------
    def _on_add_task(self):
        proj = str(self.add_project_combo.currentData() or "")
        if not proj:
            return
        cfg = str(self.add_config_combo.currentData() or "")
        settings = self._settings_getter()
        registry = self._registry_getter()
        tid = project_ops.add_task(proj, cfg, settings=settings, registry=registry)
        self.reload_tasks(settings=self._settings_getter(), registry=registry)
        self._log(
            "core",
            "info",
            "已添加运行任务 {0}{1}".format(tid, "（配置：{0}）".format(cfg) if cfg else ""),
        )
        self.order_changed.emit()

    def _on_config_change(self, task_id, cfg):
        updated = False
        for task in self._tasks:
            if task["id"] == task_id:
                if task.get("config", "") == cfg:
                    return
                task["config"] = cfg
                updated = True
                break
        if not updated:
            return
        project_ops.save_tasks(self._tasks, self._registry_getter())
        self._log(
            "core",
            "info",
            "任务 {0} 配置已设为：{1}".format(task_id, cfg or "默认配置"),
        )
        self.order_changed.emit()

    def remove_task(self, task_id):
        card = self.cards.pop(task_id, None)
        if card is None:
            return
        self._tasks = [t for t in self._tasks if t["id"] != task_id]
        self._parallel_blocks = [b for b in self._parallel_blocks if task_id not in b]
        if self._parallel_pending == task_id:
            self._parallel_pending = None
        self.cards_host.flow().removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        project_ops.save_tasks(self._tasks, self._registry_getter())
        self._write_parallel_blocks()
        self._sync_parallel_state()
        self._sync_cards_height()

        run = (self._runs_getter() or {}).get(task_id)
        status = (run or {}).get("status") if isinstance(run, dict) else getattr(run, "status", None)
        if status in ("pending", "running", "hang"):
            self._log("core", "warn", "任务 {0} 仍在运行中，已从运行顺序列表移除".format(task_id))
        else:
            self._log("core", "info", "已从运行顺序移除任务 {0}".format(task_id))
        self.order_changed.emit()

    # -- 并行分组 -------------------------------------------------------------
    def visible_card_keys(self):
        return [w.key for w in self.cards_host.ordered_widgets() if hasattr(w, "key")]

    def _adjacent_pair(self, first, second):
        visible = self.visible_card_keys()
        if first in visible and second in visible:
            return abs(visible.index(first) - visible.index(second)) == 1
        return False

    def parallel_groups(self):
        visible = self.visible_card_keys()
        visible_set = set(visible)
        groups = []
        claimed = set()
        for block in self._parallel_blocks:
            members = [k for k in block if k in self.cards]
            if len(members) < 2 or not all(k in visible_set for k in members):
                continue
            indices = sorted(visible.index(k) for k in members)
            if indices != list(range(indices[0], indices[0] + len(indices))):
                continue
            if claimed & set(members):
                continue
            claimed |= set(members)
            groups.append(sorted(members, key=visible.index))
        return groups

    def _write_parallel_blocks(self, log_text=None):
        blocks = [list(b) for b in self._parallel_blocks if len(b) >= 2]
        settings = config.settings()
        chain = settings.setdefault("chain", {})
        if not isinstance(chain, dict):
            chain = {}
            settings["chain"] = chain
        chain["parallel_blocks"] = blocks
        config.save_yaml(config.SETTINGS_YAML, settings)
        if log_text:
            self._log("core", "info", log_text)
        self.order_changed.emit()

    def _prune_parallel_blocks(self, persist=True):
        blocks = self.parallel_groups()
        kept = {tuple(sorted(g)) for g in blocks}
        before = [list(b) for b in self._parallel_blocks]
        dropped = [b for b in before if tuple(sorted(b)) not in kept]
        if not dropped:
            return
        self._parallel_blocks = [list(g) for g in blocks]
        if persist:
            self._write_parallel_blocks()
            self._log(
                "core",
                "warn",
                "并行组已取消：{0} 里的任务不再相邻".format(
                    "、".join("+".join(b) for b in dropped)
                ),
            )

    def toggle_parallel(self, key):
        self._prune_parallel_blocks()
        pending = self._parallel_pending

        if pending is not None and pending != key:
            if not self._adjacent_pair(pending, key):
                self._log(
                    "core",
                    "warn",
                    "只能把相邻的两个任务设为并行：{0} 和 {1} 之间隔着别的任务".format(pending, key),
                )
                self._parallel_pending = None
                self._sync_parallel_state()
                return
            target = None
            for block in self._parallel_blocks:
                if pending in block or key in block:
                    target = block
                    break
            if target is None:
                self._parallel_blocks.append([pending, key])
            else:
                if pending not in target:
                    target.append(pending)
                if key not in target:
                    target.append(key)
                visible = self.visible_card_keys()
                target.sort(key=lambda k: visible.index(k) if k in visible else 999)
            self._parallel_pending = None
            self._write_parallel_blocks(
                log_text="并行：{0} 与 {1} 将同时运行".format(pending, key)
            )
            self._sync_parallel_state()
            return

        if pending is None:
            for block in list(self._parallel_blocks):
                if key in block:
                    block.remove(key)
                    if len(block) < 2:
                        self._parallel_blocks = [b for b in self._parallel_blocks if b is not block]
                    self._parallel_pending = None
                    self._write_parallel_blocks(log_text="取消并行：{0}".format(key))
                    self._prune_parallel_blocks()
                    self._sync_parallel_state()
                    return
            self._parallel_pending = key
            self._sync_parallel_state()
            return

        self._parallel_pending = None
        self._sync_parallel_state()

    def _sync_parallel_state(self):
        groups = self.parallel_groups()
        member_of = {}
        for block in groups:
            for key in block:
                member_of[key] = block
        for key, card in self.cards.items():
            card.set_parallel(key in member_of, pending=(self._parallel_pending == key))
        self.links.setGeometry(self.cards_host.rect())
        widgets = [[self.cards.get(k) for k in block] for block in groups]
        active = [self.cards[self._parallel_pending]] if self._parallel_pending in self.cards else []
        self.links.set_groups(widgets, active=active)
        self.links.raise_()

    # -- 拖动排序（弹簧动画 + 卡片中心点实时位置检测） ------------------------
    def _on_card_drag_started(self, key):
        self._drag_key = key
        card = self.cards.get(key)
        if card is None:
            return
        self._drag_grab = None
        add_shadow(card, color="#000000", alpha=55, blur=24, dy=8)
        self.cards_host.set_floating(card)
        card.raise_()

    def _grab_offset(self, card, pos):
        if self._drag_grab is not None:
            return self._drag_grab
        local = card.mapFromParent(pos)
        local.setX(max(0, min(local.x(), max(0, card.width() - 1))))
        local.setY(max(0, min(local.y(), max(0, card.height() - 1))))
        self._drag_grab = local
        return local

    def _on_card_drag_moved(self, key, global_pos):
        moving = self.cards.get(key)
        if moving is None:
            return
        pos = self.cards_host.mapFromGlobal(global_pos)
        grab = self._grab_offset(moving, pos)
        x = max(0, min(pos.x() - grab.x(), max(0, self.cards_host.width() - moving.width())))
        y = max(0, min(pos.y() - grab.y(), max(0, self.cards_host.height() - moving.height())))
        if moving.pos() != QPoint(x, y):
            moving.move(x, y)
        moving.raise_()

        order = [w for w in self.cards_host.ordered_widgets() if hasattr(w, "key")]
        slots = self.cards_host.slot_rects()
        if not order or len(slots) != len(order):
            return
        try:
            current_slot = order.index(moving)
        except ValueError:
            return
        center = moving.geometry().center()
        target_index = pick_drag_slot(
            center.x(), center.y(), slots, current_slot=current_slot, hysteresis=8.0
        )
        if current_slot == target_index:
            return
        order.remove(moving)
        order.insert(max(0, min(target_index, len(order))), moving)
        self.cards_host.animate_next_layout()
        self.cards_host.set_order(order)
        self._sync_parallel_state()
        moving.raise_()

    def _on_card_drag_finished(self, key):
        card = self.cards.get(key)
        if card is not None:
            card.setGraphicsEffect(None)
        self.cards_host.clear_floating(animate=True)
        self._drag_grab = None
        self._drag_key = None

        keys = self.visible_card_keys()
        by_id = {t["id"]: t for t in self._tasks}
        self._tasks = [by_id[k] for k in keys if k in by_id] + [
            t for t in self._tasks if t["id"] not in set(keys)
        ]
        project_ops.save_tasks(self._tasks, self._registry_getter())
        self._prune_parallel_blocks()
        self._sync_parallel_state()
        self._sync_cards_height()
        self._log("core", "info", "运行顺序已更新：{0}".format(" → ".join(keys)))
        self.order_changed.emit()

    # -- 运行态 / 换肤 / 高度同步 ---------------------------------------------
    def update_runs(self, runs):
        runs = runs or {}
        for tid, card in self.cards.items():
            card.update_state(runs.get(tid))

    def refresh_theme(self, color_getter=None, runs=None):
        if color_getter is not None:
            self._color_getter = color_getter
        self.links.set_color(links_color())
        self.add_task_btn.set_variant(self.add_task_btn.variant_name)
        runs = runs if runs is not None else (self._runs_getter() or {})
        for tid, card in self.cards.items():
            card.refresh_theme(self._color_getter(card.base_project), runs.get(tid))
        self._sync_parallel_state()

    def _sync_cards_height(self):
        vp = self.cards_scroll.viewport()
        vp_w = vp.width() if vp is not None else 0
        vp_h = vp.height() if vp is not None else 0
        width = vp_w if vp_w > 0 else self.cards_host.width()
        if width <= 0:
            width = max(360, self.width() - 36)
        min_h = max(0, vp_h - 4)
        self.cards_host.sync_height(width, min_height=min_h)
        self.links.setGeometry(self.cards_host.rect())
        desired = self.cards_host.rows_height_for(width)
        if desired > 0 and self.cards_scroll.content_height() != desired:
            self.cards_scroll.set_content_height(desired)

    def eventFilter(self, obj, ev):
        if obj is self.cards_scroll.viewport() and ev.type() == QEvent.Resize:
            self._sync_cards_height()
        return super().eventFilter(obj, ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._sync_cards_height()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._sync_cards_height()
