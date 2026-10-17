import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

spec = importlib.util.spec_from_file_location("gui_main", ROOT / "gui" / "main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    # 隔离运行数据（完成后退出）：避免把 state.json / durations.jsonl /
    # errors.jsonl 写进用户真实 logs——用户可能正在挂机，那会污染其运行状态。
    from tests import smoke_isolation

    guard = smoke_isolation.isolate_runtime(prefix="afk_closeexit_")
    try:
        return _run()
    finally:
        guard.restore()


def _run():
    app = QApplication([])
    app.setStyleSheet(module.APP_QSS)
    app.setQuitOnLastWindowClosed(False)
    window = module.MainWindow()
    window.show()
    flag = {"quit": False}
    app.aboutToQuit.connect(lambda: flag.__setitem__("quit", True))
    QTimer.singleShot(500, window.close)
    QTimer.singleShot(4000, app.quit)
    code = app.exec()
    visible = window.isVisible()
    ok = flag["quit"] and not visible
    print("aboutToQuit fired:", flag["quit"], "| window visible:", visible, "| exec code:", code)
    print("CLOSE-EXIT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
