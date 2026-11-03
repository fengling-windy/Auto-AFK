import hashlib
import json
import logging
import re
import time
from pathlib import Path

from . import config

log = logging.getLogger("afk.report")

MASK = re.compile(r"(pass|pwd|secret|token|cookie|key|auth)", re.IGNORECASE)


def _mask(value, key=""):
    if MASK.search(str(key)):
        return "***"
    if isinstance(value, dict):
        return {k: _mask(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, key) for v in value]
    return value


def error_signature(text):
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    normalized = re.sub(r"\d+", "N", normalized)
    normalized = re.sub(r"0x[0-9a-f]+", "H", normalized)
    return hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]


def find_recent(signature, hours=20):
    if not signature or hours <= 0:
        return None
    try:
        if not config.ERROR_INDEX.exists():
            return None
        cutoff = time.time() - float(hours) * 3600
        with open(config.ERROR_INDEX, "r", encoding="utf-8") as f:
            lines = f.readlines()[-500:]
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("signature") == signature and float(entry.get("ts", 0) or 0) >= cutoff:
                return entry.get("dir")
    except Exception:
        return None
    return None


def take_screenshot(target: Path):
    try:
        import mss

        target.parent.mkdir(parents=True, exist_ok=True)
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            shot = sct.grab(monitor)
            mss.tools.to_png(shot.rgb, shot.size, output=str(target))
        return True
    except Exception:
        return False


def _tail(path: Path, lines=200):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.readlines()
        return "".join(data[-lines:])
    except Exception:
        return ""


def create_report(project, reason, run, watcher, bus, repair_log=None, extra=None):
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_dir = config.REPORTS_DIR / "{0}_{1}".format(ts, project)
    report_dir.mkdir(parents=True, exist_ok=True)

    events = []
    if run is not None:
        events = bus.project_events(project, run.run_id)
    events_text = "\n".join(json.dumps(e, ensure_ascii=False) for e in events[-400:])
    (report_dir / "events.jsonl").write_text(events_text, encoding="utf-8")

    display_events = [
        e for e in events
        if e.get("kind") != "heartbeat" and str(e.get("message", "")).strip()
    ]

    error_text = watcher.error_text() if watcher else ""
    (report_dir / "error.txt").write_text(error_text, encoding="utf-8")

    if run is not None:
        stdout_tail = _tail(Path(run.out_log))
        stderr_tail = _tail(Path(run.err_log))
        (report_dir / "stdout_tail.txt").write_text(stdout_tail, encoding="utf-8")
        (report_dir / "stderr_tail.txt").write_text(stderr_tail, encoding="utf-8")

    spec = (config.registry().get("projects", {}) or {}).get(project, {})
    snapshot = {
        "project": project,
        "reason": reason,
        "spec": spec,
        "settings": config.settings(),
        "time": ts,
    }
    if extra:
        snapshot["extra"] = extra
    (report_dir / "snapshot.json").write_text(
        json.dumps(_mask(snapshot), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if repair_log:
        (report_dir / "repair_attempts.json").write_text(
            json.dumps(repair_log, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    take_screenshot(report_dir / "screenshot.png")

    signature = error_signature(error_text or reason)
    summary_lines = [
        "# Auto-AFK error report",
        "",
        "- project: {0}".format(project),
        "- reason: {0}".format(reason),
        "- signature: {0}".format(signature),
        "- time: {0}".format(time.strftime("%Y-%m-%d %H:%M:%S")),
        "- run_id: {0}".format(getattr(run, "run_id", "")),
        "- exit_code: {0}".format(getattr(run, "exit_code", None)),
        "",
        "## last events",
        "",
        "```",
        "\n".join(e.get("message", "")[:500] for e in display_events[-30:]),
        "```",
        "",
        "## error tail",
        "",
        "```",
        error_text[-4000:],
        "```",
    ]
    (report_dir / "report.md").write_text("\n".join(summary_lines), encoding="utf-8")

    index_entry = {
        "ts": time.time(),
        "project": project,
        "reason": reason,
        "signature": signature,
        "dir": str(report_dir),
        "run_id": getattr(run, "run_id", ""),
        "exit_code": getattr(run, "exit_code", None),
        "repair_attempts": len(repair_log or []),
    }
    try:
        with open(config.ERROR_INDEX, "a", encoding="utf-8") as f:
            f.write(json.dumps(index_entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("failed to write error index: %s", exc)
    return report_dir


def list_reports(limit=100):
    if not config.REPORTS_DIR.exists():
        return []
    items = []
    for path in sorted(config.REPORTS_DIR.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        entry = {"dir": str(path), "name": path.name}
        index = path / "snapshot.json"
        if index.exists():
            try:
                snap = json.loads(index.read_text(encoding="utf-8"))
                entry["project"] = snap.get("project")
                entry["reason"] = snap.get("reason")
            except Exception:
                pass
        items.append(entry)
        if len(items) >= limit:
            break
    return items


def duration_stats(limit=500):
    """Per-project run duration stats from logs/durations.jsonl.

    Borrowed from YukinoChan: recorded durations help tune hang_timeout values
    instead of guessing them.
    """
    entries = []
    try:
        if config.DURATIONS_FILE.exists():
            with open(config.DURATIONS_FILE, "r", encoding="utf-8") as f:
                for line in f.readlines()[-max(1, int(limit)):]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        continue
    except OSError:
        return {}
    by_project = {}
    for entry in entries:
        by_project.setdefault(str(entry.get("project")), []).append(entry)
    result = {}
    for project, items in by_project.items():
        durs = [float(i.get("runtime_sec") or 0) for i in items]
        result[project] = {
            "runs": len(durs),
            "avg_sec": round(sum(durs) / len(durs), 1),
            "max_sec": round(max(durs), 1),
            "last_sec": durs[-1],
            "last_status": items[-1].get("status"),
            "last_ts": items[-1].get("ts"),
        }
    return result
