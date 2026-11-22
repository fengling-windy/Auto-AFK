import logging
import shutil
import time

from . import config

log = logging.getLogger("afk.housekeeping")

PROJECT_LOG_MAX_BYTES = 5 * 1024 * 1024
PROJECT_LOG_KEEP_BYTES = 2 * 1024 * 1024


def _emit(bus, message, level="info"):
    if bus is None:
        return
    try:
        bus.emit({"project": "core", "kind": "housekeeping", "level": level, "message": message, "data": {}})
    except Exception as exc:
        log.warning("housekeeping emit failed: %s", exc)


def _older_than(path, cutoff):
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _is_rolling_project_log(path):
    # per-project rolling logs are "<project>.log" (no underscore in the stem);
    # per-run files look like "<project>_<runid>.out.log" / hang png / spool files.
    return path.suffix == ".log" and "_" not in path.stem


def _keep_tail(path, keep_bytes):
    """把文件内容换成「尾部 keep_bytes 字节，从一个整行开始」，返回是否真的截了。

    为什么不用「写临时文件 + os.replace」：events.jsonl / durations.jsonl /
    每个项目的滚动日志在本进程里都长期开着追加句柄（EventBus 为每个项目
    保留一个 buffering=1 的 fh，GUI 进程整个生命周期不关）。Windows 上
    os.replace 需要对目标文件的 DELETE 权限，只要还有别的句柄打开着就报
    WinError 5「拒绝访问」——实测 42 MB 的 events.jsonl 一次都没截断成功，
    每次还在 logs/ 里多留一个 .tmp 垃圾，只能等进程退出才有机会生效。
    原地重写不需要替换文件：把尾部搬到文件开头再 truncate，打开着的追加
    句柄（O_APPEND）会继续在**新的**文件末尾写，双方互不影响。
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= keep_bytes or keep_bytes <= 0:
        return False
    try:
        tail = path.read_bytes()[-keep_bytes:]
    except OSError:
        return False
    newline = tail.find(b"\n")
    if newline >= 0:
        tail = tail[newline + 1:]
    if not tail:
        return False
    with open(path, "r+b") as f:
        f.seek(0)
        f.write(tail)
        f.truncate(len(tail))
    return True


def cleanup_logs(bus=None, settings=None):
    cfg = ((settings or config.settings()).get("housekeeping") or {})
    if not cfg.get("enabled", True):
        return {"enabled": False}
    now = time.time()
    log_cutoff = now - float(cfg.get("log_days", 14)) * 86400
    report_cutoff = now - float(cfg.get("report_days", 30)) * 86400
    removed = 0

    if config.PROJECT_LOGS.exists():
        for path in config.PROJECT_LOGS.iterdir():
            if not path.is_file() or _is_rolling_project_log(path):
                continue
            if _older_than(path, log_cutoff):
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    # 文件被别的进程占着（正在写的日志）删不掉是常态，
                    # 但只删得掉一半会让「保留天数」越来越不准。
                    log.debug("remove old project log %s failed: %s", path.name, exc)

        for path in config.PROJECT_LOGS.glob("*.log"):
            if not _is_rolling_project_log(path):
                continue
            try:
                if path.stat().st_size > PROJECT_LOG_MAX_BYTES:
                    if _keep_tail(path, PROJECT_LOG_KEEP_BYTES):
                        _emit(bus, "project log truncated: {0}".format(path.name))
            except OSError as exc:
                # 截断失败会让这份日志无限长下去，至少留下一条线索
                log.debug("project log truncation failed: %s: %s", path.name, exc)

    max_bytes = int(float(cfg.get("events_max_mb", 50)) * 1024 * 1024)
    try:
        events = config.EVENTS_FILE
        if events.exists() and events.stat().st_size > max_bytes:
            if _keep_tail(events, max_bytes // 2):
                _emit(bus, "events.jsonl truncated to last {0} MB".format(max_bytes // 2 // 1024 // 1024))
    except OSError as exc:
        log.warning("events.jsonl truncation failed: %s", exc)

    durations_max = int(float(cfg.get("durations_max_mb", 1)) * 1024 * 1024)
    try:
        durations = config.DURATIONS_FILE
        if durations.exists() and durations.stat().st_size > durations_max:
            if _keep_tail(durations, durations_max // 2):
                _emit(bus, "durations.jsonl truncated")
    except OSError as exc:
        log.warning("durations.jsonl truncation failed: %s", exc)

    # 旧版本用 os.replace 截断 events.jsonl，失败时会把 .tmp 留在 logs/ 里。
    # 现在改成原地重写，这些残留没有用处了，顺手清掉（只清自己的命名，别乱删）。
    for name in ("events.jsonl.tmp", "durations.jsonl.tmp"):
        stale = config.LOGS_DIR / name
        try:
            if stale.exists():
                stale.unlink()
        except OSError as exc:
            # 残留清不掉不致命，但会是 logs 目录长期带着垃圾的信号
            log.debug("remove stale %s failed: %s", name, exc)

    if config.REPORTS_DIR.exists():
        for path in config.REPORTS_DIR.iterdir():
            if path.is_dir() and _older_than(path, report_cutoff):
                shutil.rmtree(path, ignore_errors=True)
                removed += 1

    if removed:
        _emit(bus, "housekeeping removed {0} old item(s)".format(removed))
    return {"removed": removed}
