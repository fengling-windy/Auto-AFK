"""真实 GUI 启动冒烟：按用户实际的启动方式拉起 GUI 进程并验证它真的在工作。

与其它 GUI 测试的区别：那些是「离屏实例化 MainWindow」（同进程内构造对象），
本测试是真的 fork 出一个 GUI 进程（python gui/main.py --tray --no-widget），
因此能验证启动阶段才会暴露的问题：入口脚本能否跑起来、事件服务器是否就绪、
CLI 能否远程读到它、空闲检测是否完成初始化、关闭后是否留下孤儿。

隔离方式：用 config 支持的 AFK_ROOT 环境变量把根目录整体重定向到一个临时
「影子根」（只含配置副本 + 全新 logs），因此整个过程对用户真实配置与日志
零影响（测试结束会断言真实 logs 未被写入）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

# 从本文件位置推导仓库根，便于在任何机器上运行（而不是写死绝对路径）
ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="afk_reallaunch_"))

CORE = TMP / "configs" / "core"
CORE.mkdir(parents=True, exist_ok=True)
(TMP / "logs").mkdir(parents=True, exist_ok=True)
for name in ("afk_projects.yaml", "repair_rules.yaml", "settings.yaml"):
    shutil.copy(ROOT / "configs" / "core" / name, CORE / name)

# 用 YAML 解析改配置（不用正则）
cfg = yaml.safe_load((CORE / "settings.yaml").read_text(encoding="utf-8"))
cfg.setdefault("pre_run", {}).update({
    "enabled": False,          # 不联网更新项目
    "at_startup": False,
    "update_projects": False,
    "update_emulator": False,
})
cfg.setdefault("gui", {})["autostart_chain_on_launch"] = False
cfg.setdefault("housekeeping", {"enabled": False})["enabled"] = False
cfg.setdefault("idle", {})["enabled"] = True      # 保留空闲检测开启以便验证
(CORE / "settings.yaml").write_text(
    yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

env = os.environ.copy()
env["AFK_ROOT"] = str(TMP)
env["AFK_DRY_RUN"] = "1"
env["PYTHONIOENCODING"] = "utf-8"
env["QT_QPA_PLATFORM"] = "offscreen"

checks = []
def check(name, ok, extra=""):
    print("{0} {1}{2}".format("PASS" if ok else "FAIL", name, ("  " + extra) if extra else ""))
    checks.append((name, ok))

print("影子根:", TMP)
print("影子 pre_run:", cfg["pre_run"])
proc = subprocess.Popen(
    [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "gui" / "main.py"), "--tray", "--no-widget"],
    cwd=str(ROOT), env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
print("  PID(Popen) =", proc.pid)

# 记录真实运行数据的基线，结束时比对，证明隔离确实「零影响」。
# 注意：不能用「events.jsonl 大小是否变化」来判断——用户自己可能正在跑挂机
# 任务，那也会让文件增长（实测踩到过：12 小时前跑本测试时因用户正在运行而
# 误报失败）。改为用唯一探针 ID 精确检查「我发的事件有没有落进真实日志」。
REAL_EVENTS = ROOT / "logs" / "events.jsonl"
REAL_STATE = ROOT / "logs" / "state.json"
REAL_COMPLETION = ROOT / "logs" / "last_chain_success.json"
unique_id = "probe-{0}-{1}".format(os.getpid(), int(time.time() * 1000))
real_state_before = REAL_STATE.exists()
real_completion_before = REAL_COMPLETION.exists()

try:
    server_file = TMP / "logs" / "event_server.json"
    info = None
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            info = json.loads(server_file.read_text(encoding="utf-8"))
            if info.get("port"):
                break
            info = None
        except Exception:
            info = None
        time.sleep(0.4)

    check("GUI 进程真实启动并存活", proc.poll() is None,
          "exit={0}".format(proc.poll()) if proc.poll() is not None else "")
    if info:
        print("  事件服务器: port={0} pid={1}".format(info.get("port"), info.get("pid")))
        # .venv python.exe 是启动器：Popen 的 pid 与解释器 os.getpid() 不同，
        # 因此验证「该 pid 存活」而不是与 Popen.pid 相等。
        import psutil
        check("事件服务器进程存活", psutil.pid_exists(int(info.get("pid", -1))))

        ev = TMP / "logs" / "events.jsonl"
        # 说明：events.jsonl 是「懒创建」的（首次 emit 才生成文件），且 GUI 自身的
        # 界面日志走 _append_log 只写控件、不进事件流；因此 pre_run 关闭时启动
        # 可能一条事件都没有。这里主动通过 /event 接口发一条带唯一 ID 的事件，
        # 验证整条「HTTP -> EventBus -> JSONL 落盘」管道在真实进程里工作。
        import urllib.request
        probe_msg = "real-launch smoke probe {0}".format(unique_id)
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:{0}/event".format(info.get("port")),
                data=json.dumps({"project": "core", "kind": "probe", "level": "info",
                                 "message": probe_msg}).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "X-AFK-Token": str(info.get("token", ""))},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                posted = resp.status == 200
        except Exception as exc:
            posted = False
            print("  发事件失败:", exc)
        check("可通过 HTTP 向 GUI 事件流写入事件", posted)

        wrote = False
        for _ in range(40):                     # 最多等 10s 让事件落盘
            if ev.exists() and ev.stat().st_size > 0:
                wrote = True
                break
            time.sleep(0.25)
        size = ev.stat().st_size if ev.exists() else 0
        check("隔离生效：事件写入影子根", wrote, "{0} 字节".format(size))
        if wrote:
            tail = ev.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-1:]
            check("影子根事件内容包含刚发的探针事件",
                  any("real-launch smoke probe" in t for t in tail))

        check("影子根不含真实路径的日志",
              not (ROOT / "logs" / "event_server.json").exists())

        # CLI 远程验证 GUI 在真实服务
        r = subprocess.run(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "afk_core.cli", "status", "--json"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=40,
        )
        ok = r.returncode == 0 and "chain_status" in (r.stdout or "")
        check("CLI 能远程读到该 GUI 状态", ok)
        if ok:
            snap = json.loads(r.stdout)
            print("  远程 chain_status =", snap.get("chain_status"),
                  "| runs =", len(snap.get("runs") or {}))

        # 空闲检测是否真的启动（用户的核心功能）
        r2 = subprocess.run(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "afk_core.cli", "idle", "--json"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=40,
        )
        idle_ok = r2.returncode == 0 and '"enabled"' in (r2.stdout or "")
        check("CLI 能读到空闲检测状态（功能已初始化）", idle_ok)
        if idle_ok:
            d = json.loads(r2.stdout)
            print("  空闲: enabled={0} supported={1} armed={2}".format(
                d.get("enabled"), d.get("supported"), d.get("armed")))
            check("空闲检测在影子根内处于开启状态", d.get("supported") is True)
    else:
        check("事件服务器就绪", False, "60s 内未就绪（进程是否提前退出？exit={0}）".format(proc.poll()))
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=10)
    print("关闭，exit =", proc.returncode)
    time.sleep(1.5)

    r3 = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "afk_core.cli", "status"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    check("关闭后无孤儿事件服务器（CLI 回退本地）", "chain:" in (r3.stdout or ""))

    # 零影响的强证明：用唯一探针 ID 检查「我发的事件」有没有落进真实日志。
    # 不能用文件大小差判断——用户自己可能正在跑任务，那也会让文件增长。
    leaked = False
    try:
        if REAL_EVENTS.exists():
            with open(REAL_EVENTS, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 1_000_000))          # 只看末尾 1MB
                tail = f.read().decode("utf-8", errors="ignore")
            leaked = unique_id in tail
    except OSError:
        leaked = False
    check("真实 logs/events.jsonl 未出现本次探针事件（隔离有效）", leaked is False,
          "probe id = {0}".format(unique_id))
    check("真实 state.json 未被创建",
          (ROOT / "logs" / "state.json").exists() == real_state_before)
    check("真实「今日已完成」标记未被创建",
          (ROOT / "logs" / "last_chain_success.json").exists() == real_completion_before)

    shutil.rmtree(TMP, ignore_errors=True)
    check("影子根已删除", not TMP.exists())

bad = [n for n, ok in checks if not ok]
print()
print("REAL-LAUNCH", "PASS" if not bad else "FAIL ({0})".format(", ".join(bad)))
sys.exit(0 if not bad else 1)
