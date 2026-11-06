"""测试用的运行数据隔离工具。

背景（真实缺陷）：Supervisor 在链成功时会写 `logs/last_chain_success.json`
（记录「今天已完成」，供空闲自动挂机跳过重复执行）。如果某个冒烟测试跑了
一条会成功的链却没有隔离输出目录，就会把这个标记写进**真实的** logs 目录，
结果用户当天的空闲自动挂机被误跳过——测试污染了功能状态。

因此凡是会运行任务链的测试，都应当先调用 `isolate_runtime()`，把
LOGS_DIR / PROJECT_LOGS / EVENTS_FILE / STATE_FILE / ERROR_INDEX /
DURATIONS_FILE 全部指向临时目录，并在结束时 restore()。

用法：
    from tests import smoke_isolation as iso

    guard = iso.isolate_runtime()
    try:
        ...
    finally:
        guard.restore()

也提供 checkpoint() 以便在结束时把新产生的文件清掉（双保险）。
"""

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from afk_core import config

_TRACKED = (
    "LOGS_DIR",
    "PROJECT_LOGS",
    "REPORTS_DIR",
    "EVENTS_FILE",
    "STATE_FILE",
    "ERROR_INDEX",
    "DURATIONS_FILE",
)

# 配置文件同样要隔离：MainWindow 在关闭/拖动时会把自己的窗口尺寸和小组件位置
# 写回 settings.yaml（`_save_window_size` / `_flush_widget_pos`），而 save_yaml
# 会抹掉注释、重排缩进。实测 smoke_gui 跑一遍就把仓库里真实的
# configs/core/settings.yaml 重写了一遍（注释全丢、widget_pos 变成 [543, 96]），
# 用户下次打开程序看到的是测试留下的窗口位置。
_CONFIG_FILES = (
    ("SETTINGS_YAML", "settings.yaml"),
    ("PROJECTS_YAML", "afk_projects.yaml"),
    ("REPAIR_YAML", "repair_rules.yaml"),
)


class _Guard:
    def __init__(self, tmp, original, original_env=None, had_env=False):
        self.tmp = tmp
        self.original = original
        self.original_env = original_env
        self.had_env = had_env

    def restore(self):
        for name, value in self.original.items():
            setattr(config, name, value)
        # 还原 AFK_ROOT（原本没有就删除）
        if self.had_env:
            os.environ["AFK_ROOT"] = self.original_env
        else:
            os.environ.pop("AFK_ROOT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.restore()
        return False


def _isolate_configs(target_dir, original):
    """把还指向仓库真实文件的配置文件改成临时副本。

    只接管「仍然指向 configs/core 真文件」的那几个属性：很多冒烟自己在模块级
    就把 config.SETTINGS_YAML 换成了自己的临时副本（smoke_shell_ui、
    smoke_user_flow 等），那些已经隔离好了，这里不能抢过来覆盖。

    还有一种情况要救：同一个进程里反复调用 isolate_runtime() 却不 restore() 的
    （smoke_completion 就是这样，每段用 shutil.rmtree 清临时目录），上一轮的
    临时配置路径已经随目录一起消失。此时 current 指向一个不存在的文件，
    若照抄它就整段失败（实测 FileNotFoundError），所以这种情况按真文件处理。
    """
    target_dir = Path(target_dir)
    for attr, filename in _CONFIG_FILES:
        current = getattr(config, attr, None)
        if current is None:
            continue
        real = Path(config.CORE_CONFIG_DIR) / filename
        try:
            same = Path(current).resolve() == real.resolve()
        except OSError:
            same = False
        stale = False
        if not same:
            # 上一轮隔离留下的临时副本已经没了 -> 当作「还没隔离」重新做一次
            stale = not Path(current).exists()
            if not stale:
                continue
        if not real.exists():
            continue
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / filename
            shutil.copy(real, target)
        except OSError:
            continue
        # restore 时回到真文件（stale 情况回到的是一个已删除的路径，没意义）
        original[attr] = real if stale else current
        setattr(config, attr, target)


def isolate_runtime(prefix="afk_test_"):
    """把运行数据目录指向临时目录，返回一个 restore()/with 用的 guard。

    同时设置 AFK_ROOT 环境变量：被 Supervisor spawn 的子进程会继承环境
    （_build_env 用 os.environ.copy()），从而让子进程也把运行数据写到同一个
    临时根，而不是用户真实 logs。缺了这一步会出现跨进程路径不一致——
    例如 tests/smoke_gui_entry.py 写的 marker 落到真实 logs，而父测试在
    临时目录里等不到，表现为测试失败（实测 smoke_guiopen 即如此）。

    配置文件（settings.yaml / afk_projects.yaml / repair_rules.yaml）也会换成
    临时副本：MainWindow 关窗和拖动小组件时会把窗口尺寸、widget_pos 写回
    settings.yaml，而 save_yaml 会抹掉注释、重排缩进。实测 smoke_gui 跑一遍
    就把仓库里真实的 configs/core/settings.yaml 重写了一遍。
    """
    original = {name: getattr(config, name) for name in _TRACKED}
    original_env = os.environ.get("AFK_ROOT")
    had_env = "AFK_ROOT" in os.environ
    tmp = Path(tempfile.mkdtemp(prefix=prefix))

    config.LOGS_DIR = tmp
    config.PROJECT_LOGS = tmp / "projects"
    config.REPORTS_DIR = tmp / "reports"
    config.EVENTS_FILE = tmp / "events.jsonl"
    config.STATE_FILE = tmp / "state.json"
    config.ERROR_INDEX = tmp / "errors.jsonl"
    config.DURATIONS_FILE = tmp / "durations.jsonl"
    os.environ["AFK_ROOT"] = str(tmp)

    config.PROJECT_LOGS.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    _isolate_configs(tmp / "_configs", original)
    return _Guard(tmp, original, original_env, had_env)


def wait_supervise_threads(timeout=30.0, poll=0.1, prefix="afk-supervise-"):
    """等到所有「监督线程」结束。

    为什么需要它：Supervisor 为每个运行中的项目起一个名为 afk-supervise-<key>
    的守护线程。stop_all() 只停项目、**不等待**该线程退出；而它在收尾时还会
    调用 _persist() 写 state.json。测试若在这之前 restore() 隔离目录，这次写入
    就落到用户真实 logs——实测 smoke_idle 的 background 链留下存活线程，导致
    进程退出后约 220ms 才出现 logs/state.json。

    返回 True 表示都已结束。
    """
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        alive = [t for t in threading.enumerate()
                 if t.name.startswith(prefix) and t.is_alive()]
        if not alive:
            return True
        time.sleep(poll)
    return False


def real_state_paths():
    """真实 logs 下「会被测试误写」的文件路径，供测试做前后对比。"""
    root = Path(config.ROOT) / "logs"
    return {
        "completion_marker": root / "last_chain_success.json",
    }


def wait_chain_idle(supervisor, timeout=300.0, poll=0.2):
    """等到「链线程真正退出且无项目在跑」——比只看 chain_status 更严格。

    为什么需要它：chain_status 是链线程内部设置的共享状态，它变成 success 时
    线程可能仍在收尾（写完成标记、持久化、跑后置动作）。测试若只等 chain_status
    就跳出并 restore() 了隔离目录，链线程随后写的标记就会落到**真实** logs 目录
    （实测 5 次里有 2 次），把「今日已完成」错误地写进用户环境，导致用户当天的
    空闲自动挂机被跳过。

    返回 True 表示已静默；False 表示超时。
    """
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        thread = getattr(supervisor, "_chain_thread", None)
        alive_thread = bool(thread is not None and thread.is_alive())
        runs = getattr(supervisor, "runs", {}) or {}
        any_alive = any(getattr(r, "alive", False) for r in runs.values())
        status = getattr(supervisor, "chain_status", "idle")
        if not alive_thread and not any_alive and status in ("success", "failed", "stopped", "idle"):
            return True
        time.sleep(poll)
    return False
