import re
import time


class RunWatcher:
    def __init__(self, project, run_id, spec):
        self.project = project
        self.run_id = run_id
        self.spec = spec
        self.last_activity = time.time()
        self.error_messages = []
        self.warn_messages = []
        self.success_hit = False
        self.finish_hit = False
        self.fail_reason = ""
        self.last_line = ""
        self._patterns = {
            "error": [re.compile(p, re.IGNORECASE) for p in spec.get("error_patterns", [])],
            "warn": [re.compile(p, re.IGNORECASE) for p in spec.get("warn_patterns", [])],
            "success": [re.compile(p, re.IGNORECASE) for p in spec.get("success_patterns", [])],
            "finish": [re.compile(p, re.IGNORECASE) for p in spec.get("finish_patterns", [])],
        }

    def feed(self, event):
        if event.get("run_id") != self.run_id:
            return
        if event.get("kind") == "heartbeat":
            return
        self.last_activity = time.time()
        message = str(event.get("message", ""))
        level = str(event.get("level", "info"))
        if message.strip():
            self.last_line = message.strip()[:500]
        if event.get("kind") == "error" or level == "error":
            self._record_error(message)
        self._match_line(message)

    def feed_text(self, text, count_activity=False):
        if not text:
            return
        if count_activity:
            self.last_activity = time.time()
        for line in text.splitlines():
            if not line.strip():
                continue
            self.last_line = line.strip()[:500]
            for pattern in self._patterns["error"]:
                if pattern.search(line):
                    self._record_error(line)
                    break
            for pattern in self._patterns["warn"]:
                if pattern.search(line):
                    self._record_warn(line)
                    break
            self._match_line(line)

    def _match_line(self, line):
        for pattern in self._patterns["success"]:
            if pattern.search(line):
                self.success_hit = True
        for pattern in self._patterns["finish"]:
            if pattern.search(line):
                self.finish_hit = True

    def _record_error(self, message):
        snippet = message.strip()
        if not snippet:
            return
        self.error_messages.append(snippet[:4000])
        if len(self.error_messages) > 50:
            del self.error_messages[0:len(self.error_messages) - 50]

    def _record_warn(self, message):
        snippet = message.strip()
        if not snippet:
            return
        self.warn_messages.append(snippet[:1000])
        if len(self.warn_messages) > 100:
            del self.warn_messages[0:len(self.warn_messages) - 100]

    def hang_seconds(self):
        return time.time() - self.last_activity

    def classify(self, exit_code, stopped=False, hang=False, require_finish=False, min_runtime_sec=0, runtime=None):
        self.fail_reason = ""
        if stopped:
            return "stopped"
        if hang:
            self.fail_reason = "hang detected (no output for a long time; last output: {0})".format(self.last_line or "n/a")
            return "hang"
        if exit_code is None:
            self.fail_reason = "process did not report an exit code"
            return "failed"
        if self.error_messages:
            self.fail_reason = "error pattern matched: {0}".format(self.error_messages[-1][:300])
            return "failed"
        if exit_code != 0:
            self.fail_reason = "non-zero exit code: {0}".format(exit_code)
            return "failed"
        if min_runtime_sec and runtime is not None and runtime < min_runtime_sec and not self.finish_hit:
            self.fail_reason = "exited after {0:.1f}s (< {1}s) without finish marker".format(runtime, min_runtime_sec)
            return "failed"
        if require_finish and not self.finish_hit:
            self.fail_reason = "finish marker not found in output"
            return "failed"
        return "success"

    def error_text(self, limit=8000):
        parts = list(self.error_messages[-10:])
        if not parts and self.fail_reason:
            parts = [self.fail_reason]
        text = "\n".join(parts)
        return text[:limit]

    def warn_count(self):
        return len(self.warn_messages)
