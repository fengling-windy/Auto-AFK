import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config
from .proc import popen as proc_popen, run as proc_run


class MuMuController:
    def __init__(self, bus, settings=None):
        self.bus = bus
        self.settings = settings or config.settings()

    def _cfg(self):
        return ((self.settings.get("pre_run") or {}).get("emulator") or {})

    def _emit(self, message, level="info", data=None):
        if self.bus is None:
            return
        self.bus.emit(
            {
                "project": "core",
                "kind": "emulator",
                "level": level,
                "message": message,
                "data": data or {},
            }
        )

    def _run(self, cmd, timeout=60, cwd=None):
        try:
            proc = proc_run(
                [str(item) for item in cmd],
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout,
            )
            return proc.returncode, ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
        except Exception as exc:
            return -1, str(exc)

    def _vmindex(self):
        return str(self._cfg().get("vmindex", 0))

    def info(self):
        manager = self._cfg().get("manager_exe")
        if not manager or not Path(str(manager)).exists():
            return {}
        code, out = self._run([manager, "info", "-v", self._vmindex()], timeout=60)
        if code != 0:
            return {}
        try:
            return json.loads(out[out.find("{"):out.rfind("}") + 1])
        except Exception:
            return {}

    def version(self):
        manager = self._cfg().get("manager_exe")
        if not manager or not Path(str(manager)).exists():
            return ""
        code, out = self._run([manager, "version"], timeout=60)
        if code != 0:
            return ""
        return out.strip().replace("\n", " ")[:120]

    def current_version(self):
        raw = self.version()
        try:
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            return str(data.get("version", "")).strip()
        except Exception:
            return ""

    @staticmethod
    def _version_tuple(value):
        parts = []
        for chunk in str(value).split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts)

    def update_available(self):
        cfg = self._cfg()
        path = Path(str(cfg.get("updater_config", "")))
        versions = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                versions = data.get("nxupdater", {}).get("version", {}) or {}
            except Exception:
                versions = {}
        skip = {item.strip() for item in str(versions.get("skip", "") or "").split(";") if item.strip()}
        current = self.current_version()
        current_tuple = self._version_tuple(current) if current else (0,)
        for key, bypass_skip in (("force", True), ("reminder", False)):
            value = str(versions.get(key, "") or "").strip()
            if not value:
                continue
            if not bypass_skip and value in skip:
                continue
            if self._version_tuple(value) > current_tuple:
                return True, value
        return False, ""

    def is_running(self):
        info = self.info()
        return bool(info.get("is_android_started") or info.get("is_process_started"))

    def adb_port(self):
        info = self.info()
        port = info.get("adb_port")
        if port:
            return int(port)
        return int(self._cfg().get("adb_port", 16384))

    def adb_exe(self):
        candidates = [
            config.ROOT / "BAAH" / "tools" / "adb" / "adb.exe",
            Path(r"E:\Environment_variables\platform-tools\adb.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return shutil.which("adb")

    def adb_connect(self, port=None):
        adb = self.adb_exe()
        if not adb:
            self._emit("adb executable not found, skip adb connect", level="warn")
            return False
        port = port or self.adb_port()
        code, out = self._run([adb, "connect", "127.0.0.1:{0}".format(port)], timeout=60)
        ok = code == 0 and "cannot" not in out.lower() and "failed" not in out.lower()
        self._emit("adb connect 127.0.0.1:{0}: {1}".format(port, "ok" if ok else out.strip()[:200]), level="info" if ok else "warn")
        return ok

    def _ensure_main_app(self):
        if self._main_app_running():
            return True
        cfg = self._cfg()
        main_exe = cfg.get("main_exe")
        if not main_exe or not Path(str(main_exe)).exists():
            self._emit("MuMu main app not found: {0}".format(main_exe), level="warn")
            return False
        exe = Path(str(main_exe))
        self._emit("starting MuMu main app: {0}".format(exe.name))
        try:
            proc_popen(
                [str(exe)],
                cwd=str(exe.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            self._emit("failed to start MuMu main app: {0}".format(exc), level="warn")
            return False
        self._main_started_by_us = True
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(3)
            if self._main_app_running():
                return True
        self._emit("MuMu main app did not start in time", level="warn")
        return False

    def start(self):
        cfg = self._cfg()
        if self.is_running():
            return False
        self._ensure_main_app()
        manager = cfg.get("manager_exe")
        launched = False
        if manager and Path(str(manager)).exists():
            for _ in range(3):
                code, out = self._run([manager, "control", "-v", self._vmindex(), "launch"], timeout=120)
                if code == 0 and '"errcode": 0' in out:
                    launched = True
                    self._emit("MuMu player launch requested via MuMuManager")
                    break
                time.sleep(5)
        if not launched:
            device_exe = cfg.get("device_exe")
            if not device_exe or not Path(str(device_exe)).exists():
                self._emit("MuMu device launcher not found: {0}".format(device_exe), level="warn")
                return False
            exe = Path(str(device_exe))
            self._emit("starting MuMu player: {0} -v {1}".format(exe.name, self._vmindex()))
            try:
                proc_popen(
                    [str(exe), "-v", self._vmindex()],
                    cwd=str(exe.parent),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
            except Exception as exc:
                self._emit("failed to start MuMu: {0}".format(exc), level="error")
                return False
        timeout = int(cfg.get("ready_timeout_sec", 240))
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            if self.is_running():
                self._emit("MuMu android started, waiting for adb")
                time.sleep(5)
                self.adb_connect()
                return True
        self._emit("MuMu did not become ready within {0}s".format(timeout), level="error")
        return False

    def stop(self, close_main=None):
        cfg = self._cfg()
        manager = cfg.get("manager_exe")
        stopped = False
        if manager and Path(str(manager)).exists():
            code, out = self._run([manager, "control", "-v", self._vmindex(), "shutdown"], timeout=180)
            if code == 0:
                for _ in range(24):
                    time.sleep(5)
                    if not self.is_running():
                        self._emit("MuMu shut down")
                        stopped = True
                        break
            else:
                self._emit("MuMu manager shutdown failed: {0}".format(out.strip()[:200]), level="warn")
        if not stopped:
            import psutil

            killed = []
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if name in ("mumunxdevice.exe", "mumuvmmheadless.exe"):
                        proc.kill()
                        killed.append(proc.info.get("pid"))
                except Exception:
                    continue
            if killed:
                self._emit("MuMu processes killed: {0}".format(sorted(killed)), level="warn")
                stopped = True
        if close_main is None:
            close_main = getattr(self, "_main_started_by_us", False)
        if close_main and manager and Path(str(manager)).exists():
            self._run([manager, "main", "close"], timeout=60)
            self._emit("MuMu main app closed")
            self._main_started_by_us = False
        return stopped

    def _mumu_pids(self):
        pids = set()
        try:
            import psutil

            for proc in psutil.process_iter(["pid", "name"]):
                name = (proc.info.get("name") or "").lower()
                if name in ("mumunxdevice.exe", "mumunxmain.exe"):
                    pids.add(int(proc.info["pid"]))
        except Exception:
            pass
        return pids

    def window_state(self):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        pids = self._mumu_pids()
        if not pids:
            return {"found": False, "minimized": False, "windows": 0}
        found = {"count": 0, "minimized": 0}
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _callback(hwnd, lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                found["count"] += 1
                if user32.IsIconic(hwnd):
                    found["minimized"] += 1
            return True

        user32.EnumWindows(enum_proc(_callback), 0)
        return {
            "found": found["count"] > 0,
            "minimized": found["count"] > 0 and found["minimized"] == found["count"],
            "windows": found["count"],
        }

    def _visible_mumu_windows(self):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        pids = self._mumu_pids()
        if not pids:
            return []
        hwnds = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _callback(hwnd, lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                hwnds.append(hwnd)
            return True

        user32.EnumWindows(enum_proc(_callback), 0)
        return hwnds

    def show_window(self):
        cfg = self._cfg()
        manager = cfg.get("manager_exe")
        if manager and Path(str(manager)).exists():
            self._run([manager, "control", "-v", self._vmindex(), "show_window"], timeout=60)
        import ctypes

        user32 = ctypes.windll.user32
        WM_SYSCOMMAND = 0x0112
        SC_RESTORE = 0xF120
        SW_RESTORE = 9
        shown = 0
        for _ in range(5):
            for hwnd in self._visible_mumu_windows():
                if user32.IsIconic(hwnd):
                    user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_RESTORE, 0)
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    shown += 1
                try:
                    user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
            if shown:
                break
            time.sleep(1)
        if shown:
            self._emit("MuMu window shown")
        else:
            self._emit("MuMu window show requested (no restore needed)")
        return shown > 0

    def _minimize_once(self):
        import ctypes

        user32 = ctypes.windll.user32
        WM_SYSCOMMAND = 0x0112
        SC_MINIMIZE = 0xF020
        hwnds = self._visible_mumu_windows()
        if not hwnds:
            return False, 0
        for hwnd in hwnds:
            if not user32.IsIconic(hwnd):
                posted = user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
                if not posted:
                    user32.ShowWindow(hwnd, 6)
        time.sleep(0.5)
        still_open = [hwnd for hwnd in self._visible_mumu_windows() if not user32.IsIconic(hwnd)]
        return not still_open, len(hwnds)

    def minimize_window(self):
        cfg = self._cfg()
        if not cfg.get("minimize_window", True):
            return False
        attempts = max(1, int(cfg.get("minimize_retries", 10)))
        interval = float(cfg.get("minimize_interval_sec", 1.0))
        for _ in range(attempts):
            ok, count = self._minimize_once()
            if ok and count:
                self._emit("MuMu window minimized to taskbar")
                self._start_minimize_watchdog()
                return True
            time.sleep(interval)
        self._emit("could not minimize MuMu window", level="warn")
        return False

    def _start_minimize_watchdog(self):
        cfg = self._cfg()
        duration = float(cfg.get("minimize_watchdog_sec", 90))
        if duration <= 0:
            return
        if getattr(self, "_watchdog", None) is not None and self._watchdog.is_alive():
            return

        def _loop():
            deadline = time.time() + duration
            while time.time() < deadline:
                time.sleep(3)
                try:
                    self._minimize_once()
                except Exception:
                    break

        self._watchdog = threading.Thread(target=_loop, daemon=True, name="mumu-minimize-watchdog")
        self._watchdog.start()

    def ensure_running(self):
        if self.is_running():
            version = self.version()
            self._emit("MuMu already running ({0}), adb port {1}".format(version or "unknown", self.adb_port()))
            self.adb_connect()
            self._show_then_minimize()
            return {"started": False, "was_running": True, "ready": True, "main_started": False}
        started = self.start()
        if started:
            self._show_then_minimize()
        return {
            "started": started,
            "was_running": False,
            "ready": started,
            "main_started": bool(getattr(self, "_main_started_by_us", False)),
        }

    def _show_then_minimize(self):
        cfg = self._cfg()
        if cfg.get("show_before_minimize", True):
            try:
                self.show_window()
            except Exception as exc:
                self._emit("show window failed: {0}".format(exc), level="warn")
            delay = float(cfg.get("show_before_minimize_sec", 3))
            if delay > 0:
                time.sleep(delay)
        self.minimize_window()

    def suppress_update_prompt(self):
        cfg = self._cfg()
        if not cfg.get("suppress_prompt", True):
            return False
        path = Path(str(cfg.get("updater_config", "")))
        if not path.exists():
            self._emit("MuMu updater config not found: {0}".format(path), level="warn")
            return False
        backup_dir = config.CORE_CONFIG_DIR / "backups" / "mumu"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, backup_dir / "{0}_{1}".format(stamp, path.name))
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            self._emit("failed to read MuMu updater config: {0}".format(exc), level="warn")
            return False
        version = data.setdefault("nxupdater", {}).setdefault("version", {})
        reminder = str(version.get("reminder", "") or "")
        force = str(version.get("force", "") or "")
        skip = [item for item in str(version.get("skip", "") or "").split(";") if item]
        changed = False
        for item in (reminder, force):
            if item and item not in skip:
                skip.append(item)
                changed = True
        if version.get("reminder"):
            version["reminder"] = ""
            changed = True
        if changed:
            version["skip"] = ";".join(skip)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self._emit("MuMu update reminder suppressed (backup: {0})".format(backup_dir))
            return True
        self._emit("MuMu update reminder already suppressed")
        return False

    def _main_app_running(self):
        try:
            import psutil

            for proc in psutil.process_iter(["name"]):
                if (proc.info.get("name") or "").lower() == "mumunxmain.exe":
                    return True
        except Exception:
            pass
        return False

    def update_check(self):
        cfg = self._cfg()
        if not cfg.get("enabled", True):
            return {"checked": False, "reason": "disabled"}
        mode = str(cfg.get("updater_mode", "gui")).lower()
        version = self.version()
        self._emit("MuMu version: {0}".format(version or "unknown"))
        exe = Path(str(cfg.get("updater_exe", "")))
        manager = str(cfg.get("manager_exe", ""))
        silent_ok = None
        silent_code = None
        # MuMu 的 Qt GUI 前端（MuMuNxUpdater / MuMuManager）在部分机器上会因
        # "no Qt platform plugin could be initialized" 而以 0xC0000409
        # （FAST_FAIL_FATAL_APP_EXIT）瞬间崩溃并弹出错误对话框。
        # 注意：不只是 --manual-check，连号称静默的 --silence-check 也会构造
        # QApplication，所以 updater_mode=none 时必须两个都不碰。
        if mode == "none":
            self._emit("MuMu updater GUI skipped (updater_mode=none)")
        elif exe.exists():
            code, out = self._run([str(exe), "--silence-check"], timeout=300, cwd=exe.parent)
            silent_code = code
            silent_ok = code == 0
            self._emit("MuMu silent update check: {0}".format("ok" if silent_ok else "exit {0}".format(code)))
        else:
            self._emit("MuMu updater not found: {0}".format(exe), level="warn")
        # 一旦 silent check 已经以 0xC0000409 崩溃，就不再拉起 --manual-check
        # 窗口，避免每次挂机前都弹一个注定崩溃的框。
        gui_crashed = silent_code is not None and (int(silent_code) & 0xFFFFFFFF) == 0xC0000409
        if gui_crashed:
            self._emit(
                "MuMu Qt GUI frontend crashed (0xC0000409), updater UI suppressed; "
                "the emulator itself is fine - set pre_run.emulator.updater_mode=none "
                "and update MuMu manually",
                level="warn",
            )
        if cfg.get("suppress_prompt", False):
            self.suppress_update_prompt()
        if gui_crashed:
            mode = "none"
        timeout = int(cfg.get("timeout_sec", 600))
        current = self.current_version()
        available, target = self.update_available()
        if available:
            self._emit("MuMu update available: {0} -> {1}".format(current or "?", target), level="warn")
        else:
            self._emit("MuMu is up to date ({0}), updater UI skipped".format(current or "unknown"))
        if mode == "gui" and exe.exists():
            if not available and not cfg.get("always_open", False):
                return {"checked": True, "version": version, "silent_ok": silent_ok, "mode": mode, "update_available": False}
            main_was_running = self._main_app_running()
            self._emit("launching MuMu updater UI (--manual-check), waiting up to {0}s".format(timeout))
            try:
                proc = proc_popen([str(exe), "--manual-check"], cwd=str(exe.parent))
                try:
                    code = proc.wait(timeout=timeout)
                    self._emit("MuMu updater closed (code {0})".format(code))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    self._emit("MuMu updater still open after {0}s, closed it and continued".format(timeout), level="warn")
            except Exception as exc:
                self._emit("failed to launch MuMu updater: {0}".format(exc), level="warn")
            if cfg.get("close_main_after_update", True) and not main_was_running and manager:
                time.sleep(2)
                self._run([manager, "main", "close"], timeout=60)
        elif mode == "auto" and exe.exists():
            if not available:
                return {"checked": True, "version": version, "silent_ok": silent_ok, "mode": mode, "update_available": False}
            self._emit("running MuMu unattended update (--do-reminder-update), timeout {0}s".format(timeout))
            code, out = self._run([str(exe), "--do-reminder-update"], timeout=timeout, cwd=exe.parent)
            self._emit("MuMu unattended update finished (code {0})".format(code), level="info" if code == 0 else "warn")
        elif mode == "none":
            self._emit("MuMu updater UI skipped (updater_mode=none)")
        return {"checked": True, "version": version, "silent_ok": silent_ok, "mode": mode, "update_available": available}
