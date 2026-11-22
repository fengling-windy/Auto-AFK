import hashlib
import logging
import os
import shutil
import time
from pathlib import Path

from . import config
from .proc import run as proc_run

log = logging.getLogger("afk.configsync")


def _store_dir(key, spec):
    return config.config_dir_for(key, spec)


def _file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup(store, name, source, keep=10):
    if not source.exists():
        return
    backups = store / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    existing = sorted(backups.glob("*_" + name), reverse=True)
    try:
        digest = _file_digest(source)
    except OSError:
        digest = None
    if digest:
        for item in existing[:3]:
            try:
                if _file_digest(item) == digest:
                    return
            except OSError:
                continue
    stamp = time.strftime("%Y%m%d_%H%M%S") + "." + "{:03d}".format(int(time.time() * 1000) % 1000)
    shutil.copy2(source, backups / "{0}_{1}".format(stamp, name))
    for item in existing[max(0, keep - 1):]:
        try:
            item.unlink()
        except OSError as exc:
            # 旧备份删不掉就白占盘；备份目录还能无限长下去。
            log.debug("remove old backup %s failed: %s", item.name, exc)


def _is_link(path):
    try:
        if path.is_symlink():
            return True
    except OSError as exc:
        # 判不出是不是链接就按「不是」处理，但要知道是哪条路径读不动
        log.debug("is_symlink(%s) failed: %s", path, exc)
    checker = getattr(os.path, "isjunction", None)
    if checker is not None:
        try:
            return bool(checker(path))
        except OSError as exc:
            log.debug("isjunction(%s) failed: %s", path, exc)
            return False
    return False


def _merge_into(source, target):
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            if not dest.exists():
                shutil.copy2(item, dest)


def ensure_links(key, spec):
    store = _store_dir(key, spec)
    project = config.project_dir(spec)
    results = []
    for link in spec.get("links", []) or []:
        link_path = project / link["link"]
        target = store / link["store"]
        if link_path.exists():
            if _is_link(link_path):
                results.append("{0}: already linked".format(link["link"]))
                continue
            target.mkdir(parents=True, exist_ok=True)
            _merge_into(link_path, target)
            shutil.rmtree(link_path, ignore_errors=True)
            results.append("{0}: merged existing dir into store".format(link["link"]))
        target.mkdir(parents=True, exist_ok=True)
        link_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            proc = proc_run(
                ["cmd", "/c", "mklink", "/J", str(link_path), str(target)],
                capture_output=True,
                text=True,
            )
            ok = proc.returncode == 0
            detail = (proc.stdout + proc.stderr).strip()
        else:
            try:
                os.symlink(str(target), str(link_path), target_is_directory=True)
                ok, detail = True, "symlink created"
            except Exception as exc:
                ok, detail = False, str(exc)
        results.append("{0}: {1}".format(link["link"], "linked" if ok else "failed: " + detail))
    return results


def sync_in(key, spec):
    store = _store_dir(key, spec)
    project = config.project_dir(spec)
    results = []
    for entry in spec.get("sync_files", []) or []:
        name = entry["path"]
        store_file = store / entry.get("store", name)
        project_file = project / name
        if store_file.exists():
            if project_file.exists():
                _backup(store, name, project_file)
            project_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(store_file, project_file)
            results.append("{0}: store -> project".format(name))
        elif project_file.exists():
            store_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project_file, store_file)
            results.append("{0}: project -> store (seeded)".format(name))
        else:
            results.append("{0}: missing".format(name))
    return results


def sync_out(key, spec):
    store = _store_dir(key, spec)
    project = config.project_dir(spec)
    results = []
    for entry in spec.get("sync_files", []) or []:
        name = entry["path"]
        store_file = store / entry.get("store", name)
        project_file = project / name
        if not project_file.exists():
            continue
        store_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project_file, store_file)
        results.append("{0}: project -> store".format(name))
    return results


def restore(key, spec):
    store = _store_dir(key, spec)
    project = config.project_dir(spec)
    results = []
    for entry in spec.get("sync_files", []) or []:
        name = entry["path"]
        store_file = store / entry.get("store", name)
        project_file = project / name
        if not store_file.exists():
            continue
        if project_file.exists():
            _backup(store, name, project_file)
        shutil.copy2(store_file, project_file)
        results.append("{0}: restored".format(name))
    backups = store / "backups"
    if backups.exists():
        latest = {}
        for item in backups.iterdir():
            base = item.name.split("_", 2)[-1]
            if base not in latest or item.name > latest[base].name:
                latest[base] = item
        for base, item in latest.items():
            target = project / base
            shutil.copy2(item, target)
            results.append("{0}: restored from {1}".format(base, item.name))
    return results


def import_release_config(key, spec):
    source_root = Path(
        (config.settings().get("configsync", {}) or {}).get("source_root", "E:/GameAFK")
    )
    store = _store_dir(key, spec)
    store.mkdir(parents=True, exist_ok=True)
    results = []
    if key == "BAAH":
        src = source_root / "BAAH" / "BAAH_CONFIGS"
        dst = store
        if src.exists():
            for item in src.iterdir():
                if item.is_file():
                    shutil.copy2(item, dst / item.name)
                    results.append(item.name)
    elif key == "March7thAssistant":
        src = source_root / "March7thAssistant"
        for name in ("config.yaml",):
            if (src / name).exists():
                shutil.copy2(src / name, store / name)
                results.append(name)
        for name in ("config", "settings"):
            if (src / name).exists():
                shutil.copytree(src / name, store / name, dirs_exist_ok=True)
                results.append(name + "/")
    elif key == "MaaEnd":
        src = source_root / "MaaEnd"
        mapping = {"config": "mxu/config", "debug": "mxu/debug", "cache": "mxu/cache"}
        for name, rel in mapping.items():
            if (src / name).exists():
                shutil.copytree(src / name, store / rel, dirs_exist_ok=True)
                results.append(name + " -> " + rel)
    return results
