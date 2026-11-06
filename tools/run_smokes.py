r"""统一的冒烟测试运行器。

为什么需要它：README 里列了二十多条单独的 python 命令，每次回归要手工逐条敲、
逐条看退出码，容易漏项，也容易把「需要真实环境」的测试误跑进来（比如
smoke_emulator 会真的拉起 MuMu，smoke_guiopen_real 会弹出真实窗口）。

用法：

    & .venv\Scripts\python.exe tools\run_smokes.py            # 跑安全子集（默认）
    & .venv\Scripts\python.exe tools\run_smokes.py --list     # 只列出分组，不执行
    & .venv\Scripts\python.exe tools\run_smokes.py --group ui # 只跑 GUI 相关
    & .venv\Scripts\python.exe tools\run_smokes.py --include-real  # 连真实环境测试也跑
    & .venv\Scripts\python.exe tools\run_smokes.py --timeout 600

分组：
    core    编排/链/重试/事件总线等纯逻辑测试
    ui      PySide6 离屏 GUI 测试（自动设置 QT_QPA_PLATFORM=offscreen）
    cleanup 日志轮转与进程清理
    real    需要真实环境（MuMu / 真实窗口 / 子项目 venv），默认不跑

退出码：全部通过为 0，有任何失败为 1。
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _use_utf8_stdout():
    """让中文说明在 Windows 重定向/管道下也能正常显示。

    Python 的 stdout 在非 tty（管道、重定向）下默认用系统 ANSI 代码页（简体中文
    环境是 gbk），直接打印中文会变成乱码。控制台本身支持 UTF-8，重配即可。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

# (测试名, 分组, 说明)
SUITE = [
    ("smoke_bus", "core", "事件总线与监测注入"),
    ("smoke_supervisor", "core", "Supervisor 基础编排"),
    ("smoke_chain", "core", "并行组 + 独占组编排"),
    ("smoke_sequential", "core", "组内串行顺序"),
    ("smoke_classify", "core", "完成/失败判定"),
    ("smoke_retry_round", "core", "链级容错与重试上限"),
    ("smoke_stop_error", "core", "出错即止"),
    ("smoke_softstop", "core", "软停止"),
    ("smoke_hang_flow", "core", "卡死 -> 修复 -> 重试 -> 报告"),
    ("smoke_hang", "core", "卡死判定"),
    ("smoke_window", "core", "窗口完成判定 + 启动器秒退保护"),
    ("smoke_completion", "core", "今日已完成则跳过空闲自动执行"),
    ("smoke_cli_remote", "core", "CLI 远程 stop/status"),
    ("smoke_startup_update", "core", "启动时更新项目 + 链跳过重复更新"),
    ("smoke_update_parallel", "core", "项目并行更新"),
    ("smoke_configsync", "core", "配置同步失败变成失败运行"),
    ("smoke_patcher", "core", "监测补丁引擎（原子写 + 语法守卫 + 幂等）"),
    ("smoke_repair_patch", "core", "补丁把源文件写坏后由修复引擎自愈"),
    ("smoke_close_exit", "core", "完成后退出"),
    ("smoke_cleanup", "cleanup", "进程清理"),
    ("smoke_housekeeping", "cleanup", "日志截断（句柄开着时也要生效）"),
    ("smoke_idle_flow", "cleanup", "空闲到点 -> 提示 -> 启动任务"),
    ("smoke_gui", "ui", "GUI 离屏冒烟"),
    ("smoke_gui_log", "ui", "GUI 日志区"),
    ("smoke_gui_log_events", "ui", "GUI 动作进事件流 + 渲染不回声"),
    ("smoke_guiopen", "ui", "打开项目 GUI（模拟入口）"),
    ("smoke_idle", "ui", "空闲阈值/武装冷却/小组件"),
    ("smoke_thread_qt", "ui", "后台线程经 Signal 投递到 GUI 线程"),
    ("smoke_shell_ui", "ui", "新外壳（导航栏/设置页/嵌入页）"),
    ("smoke_modern_ui", "ui", "主题与动效组件"),
    ("smoke_user_flow", "ui", "端到端用户流程"),
    # 需要真实环境，默认不跑
    ("smoke_emulator", "real", "会真的启动并关闭本机 MuMu 模拟器"),
    ("smoke_guiopen_real", "real", "会真实打开 BAAH 编辑器窗口"),
    ("smoke_embed_real", "real", "真实嵌入窗口"),
    ("smoke_real_launch", "real", "真实 fork GUI 进程"),
    ("smoke_baah", "real", "需在 BAAH 自己的 venv 里跑，依赖 cv2"),
]

UI_GROUPS = {"ui"}


def run_one(name, timeout):
    script = TESTS / "{0}.py".format(name)
    if not script.exists():
        return ("MISSING", 0.0, "")
    env = dict(os.environ)
    # 子进程输出是被管道捕获的，默认同样会退回 gbk；统一成 UTF-8，失败尾行才可读
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ("TIMEOUT", time.time() - started, "")
    elapsed = time.time() - started
    ok = proc.returncode == 0
    tail = ""
    if not ok:
        text = (proc.stdout or b"").decode("utf-8", "replace")
        err = (proc.stderr or b"").decode("utf-8", "replace")
        lines = [line for line in (text + err).strip().splitlines() if line.strip()]
        # 只留最后 3 行会把「到底哪条断言挂了」这件事丢掉：进程末尾往往是
        # Qt 离屏插件的噪声（propagateSizeHints / raise 之类），真正的 FAIL
        # 行在中间。这里优先报 FAIL 行，没有 FAIL 行时再退回尾部。
        failed = [line.strip() for line in lines if line.lstrip().startswith("FAIL")]
        tail = " | ".join(failed[:5] if failed else lines[-3:])
    return ("PASS" if ok else "FAIL", elapsed, tail)


def main(argv=None):
    _use_utf8_stdout()
    parser = argparse.ArgumentParser(description="统一冒烟测试运行器")
    parser.add_argument("--list", action="store_true", help="只列分组不执行")
    parser.add_argument("--group", action="append", default=None,
                        help="只跑指定分组，可重复：core / ui / cleanup / real")
    parser.add_argument("--include-real", action="store_true",
                        help="连需要真实环境的测试也跑（会动 MuMu、弹真实窗口）")
    parser.add_argument("--timeout", type=int, default=300, help="单个测试超时秒数")
    parser.add_argument("--only", default=None, help="只跑名字包含该子串的测试")
    args = parser.parse_args(argv)

    selected = []
    for name, group, desc in SUITE:
        if args.group and group not in args.group:
            continue
        if group == "real" and not args.include_real and not args.group:
            continue
        if args.only and args.only not in name:
            continue
        selected.append((name, group, desc))

    if args.list:
        for name, group, desc in selected:
            print("{0:<24} {1:<8} {2}".format(name, group, desc))
        print()
        print("共 {0} 个".format(len(selected)))
        return 0

    if not selected:
        print("没有匹配的测试")
        return 1

    if any(group in UI_GROUPS for _, group, _ in selected):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        if not os.environ.get("QT_QPA_FONTDIR") and Path("C:/Windows/Fonts").is_dir():
            os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

    results = []
    for name, group, desc in selected:
        print("[{0}] {1} ... ".format(group, name), end="", flush=True)
        status, elapsed, tail = run_one(name, args.timeout)
        print("{0} ({1:.1f}s)".format(status, elapsed))
        if tail:
            print("        {0}".format(tail))
        results.append((name, status, elapsed))

    passed = [r for r in results if r[1] == "PASS"]
    failed = [r for r in results if r[1] == "FAIL"]
    missing = [r for r in results if r[1] == "MISSING"]
    timeout = [r for r in results if r[1] == "TIMEOUT"]

    print()
    print("=" * 60)
    print("通过 {0} / 共 {1}，耗时 {2:.1f}s".format(
        len(passed), len(results), sum(r[2] for r in results)))
    for label, rows in (("失败", failed), ("超时", timeout), ("缺失", missing)):
        if rows:
            print("{0}：{1}".format(label, ", ".join(r[0] for r in rows)))
    return 0 if not (failed or timeout or missing) else 1


if __name__ == "__main__":
    sys.exit(main())
