import json
import logging
import os
import queue
import re
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config


class EventBus:
    def __init__(self, maxlen=30000):
        self._events = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._subs = []
        self._counter = 0
        self._server = None
        self._port = 0
        self._token = ""
        self._jsonl_fh = None
        self._project_fhs = {}
        self.status_provider = None
        self.command_handler = None
        config.PROJECT_LOGS.mkdir(parents=True, exist_ok=True)
        self._jsonl = config.EVENTS_FILE
        self._counter = self._restore_counter()

    def _restore_counter(self):
        try:
            if not self._jsonl.exists():
                return 0
            size = self._jsonl.stat().st_size
            with open(self._jsonl, "rb") as f:
                if size > 65536:
                    f.seek(-65536, os.SEEK_END)
                tail = f.read().decode("utf-8", errors="ignore")
            match = None
            for match in re.finditer(r'"id":\s*(\d+)', tail):
                pass
            return int(match.group(1)) if match else 0
        except Exception:
            return 0

    def emit(self, event):
        with self._lock:
            self._counter += 1
            event = dict(event)
            event.setdefault("id", self._counter)
            event.setdefault("ts", time.time())
            self._events.append(event)
            subs = list(self._subs)
            self._persist_event(event)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass
        return event

    def _persist_event(self, event):
        # persistent line-buffered handles: one write per event instead of an
        # open/close pair, with the same flush-per-line durability
        try:
            if self._jsonl_fh is None:
                self._jsonl_fh = open(self._jsonl, "a", encoding="utf-8", buffering=1)
            self._jsonl_fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            self._close_jsonl()
        project = event.get("project")
        if not project or event.get("kind") == "heartbeat":
            return
        try:
            fh = self._project_fhs.get(project)
            if fh is None:
                fh = open(config.PROJECT_LOGS / "{0}.log".format(project), "a", encoding="utf-8", buffering=1)
                self._project_fhs[project] = fh
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event.get("ts", time.time())))
            fh.write("[{0}] [{1}] [{2}] {3}\n".format(
                ts,
                str(event.get("level", "info")).upper(),
                event.get("kind", "log"),
                event.get("message", ""),
            ))
        except Exception:
            self._close_project_log(project)

    def _close_jsonl(self):
        try:
            if self._jsonl_fh is not None:
                self._jsonl_fh.close()
        except Exception:
            pass
        self._jsonl_fh = None

    def _close_project_log(self, project):
        fh = self._project_fhs.pop(project, None)
        try:
            if fh is not None:
                fh.close()
        except Exception:
            pass

    def close_handles(self):
        with self._lock:
            self._close_jsonl()
            for project in list(self._project_fhs):
                self._close_project_log(project)

    def subscribe(self):
        q = queue.Queue(maxsize=5000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def history(self, since=0, project=None):
        with self._lock:
            items = list(self._events)
        if since:
            items = [e for e in items if e.get("id", 0) > since]
        if project:
            items = [e for e in items if e.get("project") == project]
        return items

    def project_events(self, project, run_id=None):
        with self._lock:
            items = list(self._events)
        result = [e for e in items if e.get("project") == project]
        if run_id:
            result = [e for e in result if e.get("run_id") == run_id]
        return result

    def drain_spool(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            os.replace(path, str(path) + ".imported")
        except FileNotFoundError:
            return 0
        count = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                self.emit(json.loads(line))
                count += 1
            except Exception:
                continue
        return count

    @property
    def port(self):
        return self._port

    def start_server(self, host=None):
        # host 未显式给出时读 settings 的 event.host，让配置项真正生效；
        # 默认仍绑回环地址，避免无意中把带 token 的事件接口暴露到局域网。
        if host is None:
            try:
                host = ((config.settings().get("event") or {}).get("host") or "").strip() or "127.0.0.1"
            except Exception:
                host = "127.0.0.1"
        bus = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/health"):
                    self._send(200, {"ok": True, "port": bus.port})
                elif self.path.startswith("/status"):
                    provider = bus.status_provider
                    if provider is None:
                        self._send(503, {"error": "status unavailable"})
                    else:
                        try:
                            self._send(200, provider())
                        except Exception as exc:
                            self._send(500, {"error": str(exc)})
                elif self.path.startswith("/events"):
                    since = 0
                    if "since=" in self.path:
                        try:
                            since = int(self.path.split("since=")[1].split("&")[0])
                        except Exception:
                            since = 0
                    self._send(200, {"events": bus.history(since)})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except Exception:
                    length = 0
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    event = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._send(400, {"error": "bad json"})
                    return
                if self.path.startswith("/command"):
                    if self.headers.get("X-AFK-Token", "") != bus._token:
                        self._send(403, {"error": "forbidden"})
                        return
                    handler = bus.command_handler
                    if handler is None:
                        self._send(503, {"error": "commands unavailable"})
                        return
                    try:
                        result = handler(event)
                    except Exception as exc:
                        self._send(500, {"error": str(exc)})
                        return
                    self._send(200, result if isinstance(result, dict) else {"ok": bool(result)})
                elif self.path.startswith("/event"):
                    bus.emit(event)
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"error": "not found"})

        server = ThreadingHTTPServer((host, 0), Handler)
        self._port = server.server_address[1]
        self._server = server
        self._token = secrets.token_hex(12)
        threading.Thread(target=server.serve_forever, daemon=True, name="afk-event-server").start()
        self._write_server_info(host)
        return self._port

    def _server_info_path(self):
        return config.LOGS_DIR / "event_server.json"

    def _write_server_info(self, host):
        try:
            info = {"port": self._port, "pid": os.getpid(), "token": self._token, "host": host, "ts": time.time()}
            # CLI 的 stop/status 随时可能在读这份文件；直接 write_text 先截断再写，
            # 对方会读到空内容（json 解析失败 → CLI 报「没找到实例」）。
            config.write_text_atomic(self._server_info_path(), json.dumps(info))
        except Exception as exc:
            # 写入失败会导致 GUI/CLI 找不到事件服务端口，必须留痕而不是静默
            logging.getLogger("afk.events").warning("failed to write event server info: %s", exc)

    def stop_server(self):
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
        self.close_handles()
        try:
            self._server_info_path().unlink(missing_ok=True)
        except Exception:
            pass
