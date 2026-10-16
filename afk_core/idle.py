"""系统空闲检测：Windows GetLastInputInfo 封装 + 空闲触发调度。

IdleWatcher 与 Scheduler 同构（daemon 线程 + 轮询 tick），区别是触发条件为
「全局无鼠标键盘输入达到 idle.minutes」而不是到点。

触发语义（目标：同一次离开最多提醒一次，绝不重复弹窗）：
  * 空闲达到阈值 -> 触发一次，随即解除武装（armed=False）并记录冷却起点；
  * 任务链正在跑（is_busy 为真）也解除武装，本次离开不再提醒；
  * 今天已成功完成过一轮链（is_done_today 为真）则不提醒——日常已经做完了；
  * 只有用户回来（空闲回落到阈值以下）才重新武装并清零冷却。
"""

import ctypes
import threading
import time

DEFAULT_MINUTES = 30
DEFAULT_PROMPT_SEC = 30
DEFAULT_COOLDOWN_MIN = 30

try:  # 非 Windows 上 ctypes.windll 不存在，直接降级为空闲恒 0
    _IS_WINDOWS = hasattr(ctypes, "windll") and ctypes.windll is not None
except Exception:
    _IS_WINDOWS = False


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    """返回全局空闲秒数（无鼠标键盘输入）；不支持时返回 0.0。"""
    if not _IS_WINDOWS:
        return 0.0
    try:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(_LastInputInfo)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        # GetTickCount 是 32 位且会回绕，两个值都取低 32 位再相减
        ticks = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        elapsed = (ticks - (info.dwTime & 0xFFFFFFFF)) & 0xFFFFFFFF
        return elapsed / 1000.0
    except Exception:
        return 0.0


def format_idle(seconds):
    """把空闲秒数格式化为「1 小时 23 分」这种短文本。"""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{0} 小时 {1} 分".format(hours, minutes)
    if minutes:
        return "{0} 分 {1} 秒".format(minutes, secs)
    return "{0} 秒".format(secs)


class IdleWatcher:
    """轮询系统空闲时间，达到阈值时回调 on_trigger(idle_seconds)。"""

    def __init__(self, get_settings, on_trigger, bus=None, is_busy=None, interval=5.0, is_done_today=None):
        self.get_settings = get_settings
        self.on_trigger = on_trigger
        self.bus = bus
        self.is_busy = is_busy
        self.is_done_today = is_done_today
        self.interval = float(interval)
        self._stop = threading.Event()
        self._thread = None
        self._armed = True
        self._next_allowed_at = 0.0
        self._last_idle = idle_seconds()
        self.last_error = ""
        self.last_skip_reason = ""
    # -- 生命周期 --------------------------------------------------------------
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="afk-idle")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    # -- 状态 ------------------------------------------------------------------
    def _cfg(self):
        return (self.get_settings() or {}).get("idle") or {}

    def status(self):
        cfg = self._cfg()
        threshold = self.threshold_seconds()
        idle = idle_seconds()
        return {
            "supported": bool(_IS_WINDOWS),
            "enabled": bool(cfg.get("enabled", False)),
            "idle_sec": round(idle, 1),
            "idle_text": format_idle(idle),
            "threshold_sec": threshold,
            "armed": self._armed,
            "cooldown_left_sec": max(0, int(self._next_allowed_at - time.time())),
            "watching": self.running(),
            "last_error": self.last_error,
            "last_skip_reason": self.last_skip_reason,
        }

    def threshold_seconds(self):
        try:
            minutes = float(self._cfg().get("minutes", DEFAULT_MINUTES))
        except (TypeError, ValueError):
            minutes = float(DEFAULT_MINUTES)
        return max(1.0, minutes * 60.0)

    def prompt_seconds(self):
        try:
            value = int(self._cfg().get("prompt_sec", DEFAULT_PROMPT_SEC))
        except (TypeError, ValueError):
            value = DEFAULT_PROMPT_SEC
        return max(0, value)

    def cooldown_seconds(self):
        try:
            minutes = float(self._cfg().get("cooldown_min", DEFAULT_COOLDOWN_MIN))
        except (TypeError, ValueError):
            minutes = float(DEFAULT_COOLDOWN_MIN)
        return max(0.0, minutes * 60.0)

    def rearm(self):
        """允许下一次空闲再次触发，但**不清除冷却**。

        两道闸门必须同时放行才会触发：armed（本次离开是否还没提醒过）与
        cooldown（距离上次提醒是否够久）。这样即使某条代码路径重新武装了，
        也不会在同一次离开里立刻二次弹窗。

        「用户回来」（空闲回落到阈值以下）会同时重置这两者。
        """
        self._armed = True

    def _note_failure(self, exc):
        """记录一次触发失败，并把它写进事件流（便于排查）。

        过去这里对 on_trigger 的异常静默 pass，导致「提示框根本没弹出来」这种
        故障在日志里毫无痕迹——空闲挂机看起来像什么都没发生。
        """
        self.last_error = "{0}: {1}".format(type(exc).__name__, exc)
        if self.bus is not None:
            try:
                self.bus.emit(
                    {
                        "project": "core",
                        "kind": "idle",
                        "level": "error",
                        "message": "idle trigger callback failed: {0}".format(self.last_error),
                    }
                )
            except Exception:
                pass

    def _note_skip(self, reason):
        """记录「本次空闲被跳过」的原因，并写入事件流（同一原因只记一次，避免刷屏）。"""
        if self.last_skip_reason == reason:
            return
        self.last_skip_reason = reason
        if self.bus is None:
            return
        try:
            self.bus.emit(
                {
                    "project": "core",
                    "kind": "idle",
                    "level": "info",
                    "message": "idle auto-run skipped: {0}".format(reason),
                }
            )
        except Exception:
            pass

    # -- 主循环 ----------------------------------------------------------------
    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception:
                continue

    def _tick(self):
        cfg = self._cfg()
        idle = idle_seconds()
        self._last_idle = idle
        if not cfg.get("enabled", False):
            self._armed = True
            return
        threshold = self.threshold_seconds()
        if idle < threshold:
            # 用户回来了：重新武装，冷却也一并清掉（下一次离开可以正常触发）
            self._armed = True
            self._next_allowed_at = 0.0
            self.last_skip_reason = ""
            return
        if not self._armed:
            return
        if time.time() < self._next_allowed_at:
            return
        if self.is_busy is not None:
            try:
                if self.is_busy():
                    # 任务已在跑：解除武装，本次离开不再提醒（用户回来后才重置）
                    self._armed = False
                    self._next_allowed_at = time.time() + self.cooldown_seconds()
                    return
            except Exception:
                pass
        if self.is_done_today is not None:
            try:
                if self.is_done_today():
                    # 今天已经正常完成过一轮：本次离开不再提醒（日常已做完）
                    self._armed = False
                    self._next_allowed_at = time.time() + self.cooldown_seconds()
                    self._note_skip("already completed today")
                    return
            except Exception:
                pass
        self._armed = False
        self._next_allowed_at = time.time() + self.cooldown_seconds()
        if self.bus is not None:
            try:
                self.bus.emit(
                    {
                        "project": "core",
                        "kind": "idle",
                        "level": "info",
                        "message": "idle for {0}, threshold {1} - prompting".format(
                            format_idle(idle), format_idle(threshold)
                        ),
                    }
                )
            except Exception:
                pass
        try:
            self.on_trigger(idle)
        except Exception as exc:
            # 触发回调失败必须留下痕迹：过去这里是静默 pass，导致「提示框没弹出来」
            # 在日志里完全看不到（空闲挂机看起来像什么都没发生）。
            self._note_failure(exc)
