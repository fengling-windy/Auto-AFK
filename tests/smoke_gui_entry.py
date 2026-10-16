import os
from pathlib import Path

# 模拟某项目的"配置界面"入口：被 open_project_gui 派生后写标记文件并退出
marker = Path(__file__).resolve().parents[1] / "logs" / "gui_open_marker.txt"
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text("opened pid={0}\n".format(os.getpid()), encoding="utf-8")
