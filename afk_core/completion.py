"""「今日是否已正常完成」的判定：供空闲自动挂机跳过重复执行。

判据：**今天（本地日期）已经成功跑完过一整条任务链**。

为什么不用 durations.jsonl 里「某个项目今天成功过」来判定：链是整体编排，
单独手点某个项目成功并不代表今天的日常已经做完；反过来，一轮链成功意味着
链内所有启用的项目都成功了，这才是用户关心的「今日正常执行完成」。

标记写在 logs/last_chain_success.json，内容形如
    {"date": "2026-10-16", "ts": 1792..., "projects": ["March7thAssistant", ...]}

只依赖“当天日期是否匹配”，因此跨天自动失效，无需定时清理。
"""

import json
import logging
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger("afk.completion")

_LOCK = threading.Lock()


def marker_path():
    return Path(config.LOGS_DIR) / "last_chain_success.json"


def _today(local=True):
    if local:
        return time.strftime("%Y-%m-%d", time.localtime())
    return time.strftime("%Y-%m-%d", time.gmtime())


def mark_chain_success(projects=None, when=None):
    """记录「今天成功完成了一轮链」。由 Supervisor 在链成功结束时调用。"""
    payload = {
        "date": _today(),
        "ts": float(when if when is not None else time.time()),
        "projects": sorted(str(p) for p in (projects or [])),
    }
    try:
        path = marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            # 用原子写：GUI 的「今日已完成」标签与空闲判定会在别的线程读这个文件，
            # 直接 write_text 先截断再写，对方能读到空文件 → 被当成「今天还没完成」，
            # 空闲自动挂机会在刚跑完时又来一次。
            config.write_text_atomic(
                path, json.dumps(payload, ensure_ascii=False, indent=2))
        # 立即失效缓存，避免同一进程内先前的「未完成」被继续返回
        _CACHE["key"] = None
        _CACHE["value"] = False
    except Exception as exc:
        # 写不进去意味着「今日已完成」这个状态只在内存里——一旦 GUI 重开或
        # 空闲判定换了个进程，用户就会被再挂一遍。以前这里是静默 return None，
        # 唯一的现象是「明明跑完了又自动开了一轮」，查不到任何线索。
        log.warning("write chain success marker failed: %s: %s", type(exc).__name__, exc)
        return None
    return payload


def load_marker():
    """读取标记文件；不存在或损坏返回 None。"""
    try:
        path = marker_path()
        if not path.exists():
            return None
        data = json.loads(config.read_text(path))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        # 文件损坏/被别的进程正写着：当成「今天还没完成」继续跑，但留一条痕，
        # 否则用户只看到「今天已完成」标签莫名消失。
        log.warning("read chain success marker failed: %s: %s", type(exc).__name__, exc)
        return None


def clear():
    """清除标记（GUI/CLI 提供「今天还没完成，恢复自动挂机」时用）。"""
    try:
        marker_path().unlink(missing_ok=True)
        _CACHE["key"] = None
        _CACHE["value"] = False
        return True
    except Exception as exc:
        log.warning("clear chain success marker failed: %s: %s", type(exc).__name__, exc)
        return False


def completed_today():
    """今天是否已经成功完成过一轮链。"""
    data = load_marker()
    if not data:
        return False
    return str(data.get("date") or "") == _today()


# 按 mtime 缓存的完成度查询：GUI 的 _poll 每 ~300ms 调一次，
# 标记文件只在链成功时写一次，没必要反复解析 JSON（实测每次 ~3.6ms）。
_CACHE = {"key": None, "value": False}


def completed_today_cached():
    """completed_today() 的缓存版本：仅当标记文件 mtime/大小变化时重新读取。"""
    today = _today()
    try:
        st = marker_path().stat()
        key = (st.st_mtime_ns, st.st_size, today)
    except OSError:
        # 文件不存在（或不可读）-> 未完成；用 None 作键避免反复 stat 失败仍重算
        key = None
    if _CACHE["key"] == key:
        return _CACHE["value"]
    value = completed_today() if key is not None else False
    _CACHE["key"] = key
    _CACHE["value"] = bool(value)
    return _CACHE["value"]



def completion_info():
    """给 GUI/CLI 显示用的状态快照。"""
    data = load_marker()
    today = _today()
    same_day = bool(data) and str(data.get("date") or "") == today
    return {
        "completed_today": same_day,
        "marker_date": (data or {}).get("date") or "",
        "marker_ts": (data or {}).get("ts"),
        "marker_projects": (data or {}).get("projects") or [],
        "today": today,
        "path": str(marker_path()),
    }
