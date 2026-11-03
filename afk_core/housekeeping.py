import logging
import os
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
                except OSError:
                    pass

        for path in config.PROJECT_LOGS.glob("*.log"):
            if not _is_rolling_project_log(path):
                continue
            try:
                if path.stat().st_size > PROJECT_LOG_MAX_BYTES:
                    tail = path.read_bytes()[-PROJECT_LOG_KEEP_BYTES:]
                    newline = tail.find(b"\n")
                    if newline >= 0:
                        tail = tail[newline + 1:]
                    path.write_bytes(tail)
                    _emit(bus, "project log truncated: {0}".format(path.name))
            except OSError:
                pass

    max_bytes = int(float(cfg.get("events_max_mb", 50)) * 1024 * 1024)
    try:
        events = config.EVENTS_FILE
        if events.exists() and events.stat().st_size > max_bytes:
            tail = events.read_bytes()[-max_bytes // 2:]
            newline = tail.find(b"\n")
            if newline >= 0:
                tail = tail[newline + 1:]
            # 原子替换：先写临时文件再 os.replace，避免写到一半被截断成损坏状态
            tmp = events.with_name(events.name + ".tmp")
            tmp.write_bytes(tail)
            os.replace(tmp, events)
            _emit(bus, "events.jsonl truncated to last {0} MB".format(max_bytes // 2 // 1024 // 1024))
    except OSError as exc:
        log.warning("events.jsonl truncation failed: %s", exc)

    durations_max = int(float(cfg.get("durations_max_mb", 1)) * 1024 * 1024)
    try:
        durations = config.DURATIONS_FILE
        if durations.exists() and durations.stat().st_size > durations_max:
            tail = durations.read_bytes()[-durations_max // 2:]
            newline = tail.find(b"\n")
            if newline >= 0:
                tail = tail[newline + 1:]
            durations.write_bytes(tail)
            _emit(bus, "durations.jsonl truncated")
    except OSError:
        pass

    if config.REPORTS_DIR.exists():
        for path in config.REPORTS_DIR.iterdir():
            if path.is_dir() and _older_than(path, report_cutoff):
                shutil.rmtree(path, ignore_errors=True)
                removed += 1

    if removed:
        _emit(bus, "housekeeping removed {0} old item(s)".format(removed))
    return {"removed": removed}
