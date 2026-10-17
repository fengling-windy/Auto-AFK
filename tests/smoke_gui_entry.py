"""模拟某项目的“配置界面”入口：被 open_project_gui 派生的子进程执行。

它写一个标记文件后退出，父测试据此判断 GUI 真的被拉起来了。

标记固定写在「仓库根的 logs/」下：
  Supervisor.spawn_project_gui 用的是 _build_gui_env，而该函数会**有意剥离
  所有 AFK_ 环境变量**（避免向项目自己的 GUI 泄漏监控配置），因此子进程读不到
  AFK_ROOT，也就无法跟随父测试的临时目录。父测试 tests/smoke_guiopen.py 因此
  也用仓库根定位 marker —— 两侧保持一致。
"""
import os
from pathlib import Path

marker = Path(__file__).resolve().parents[1] / "logs" / "gui_open_marker.txt"
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text("opened pid={0}\n".format(os.getpid()), encoding="utf-8")
