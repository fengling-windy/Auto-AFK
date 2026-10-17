import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from afk_core import config

spec = importlib.util.spec_from_file_location("gui_main", ROOT / "gui" / "main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    # 隔离运行数据（GUI 离屏冒烟）：避免把 state.json / durations.jsonl /
    # errors.jsonl 写进用户真实 logs——用户可能正在挂机，那会污染其运行状态。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_gui_")
    try:
        return _run()
    finally:
        # 先等监督线程（afk-supervise-*）收尾，否则其 _persist()
        # 会写进尚未隔离的真实 logs（见 smoke_isolation）
        smoke_isolation.wait_supervise_threads(timeout=30)
        guard.restore()


def _run():
    app = QApplication([])
    app.setStyleSheet(module.APP_QSS)
    app.setQuitOnLastWindowClosed(False)
    window = module.MainWindow()
    window.show()
    window.event_buffer.append({"project": "BAAH", "level": "info", "message": "baah-line-1"})
    window.event_buffer.append({"project": "MaaEnd", "level": "info", "message": "maaend-line-1"})
    window.filter_combo.setCurrentIndex(window.filter_combo.findData("BAAH"))
    window._rerender_log()
    text = window.log_view.toPlainText()
    filter_ok = "baah-line-1" in text and "maaend-line-1" not in text
    window.filter_combo.setCurrentIndex(0)
    window._rerender_log()
    text_all = window.log_view.toPlainText()
    filter_ok = filter_ok and "maaend-line-1" in text_all
    html = window.log_view.document().toHtml()
    color_ok = "#81c784" in html and "#ffb74d" in html

    def save_preview():
        try:
            window.grab().save(str(config.LOGS_DIR / "gui_preview.png"))
        except Exception:
            pass

    QTimer.singleShot(1200, save_preview)
    QTimer.singleShot(2500, app.quit)
    code = app.exec()
    print("GUI smoke ok, exec code:", code)
    print("event server port:", window.bus.port)
    print("cards:", ", ".join(window.cards.keys()))
    print("filter works:", filter_ok)
    print("project colors present:", color_ok)
    return 0 if filter_ok and color_ok else 1


if __name__ == "__main__":
    sys.exit(main())
