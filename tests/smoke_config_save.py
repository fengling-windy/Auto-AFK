r"""保存配置文件必须是「要么旧内容，要么新内容」，且不能被并发/占用打挂。

历史缺陷（2026-11-06 发现）：`afk_core/config.py:save_yaml()` 用固定的
`<文件名>.tmp` 做中转再 `os.replace`。GUI 里有两处会并发保存同一份
`configs/core/settings.yaml`：

  * 设置页的 400ms 合并落盘（`settings_page._queue_save`）；
  * 拖动窗口结束时写 `gui.window_size`（`MainWindow._on_window_resized`）。

实测 8 线程 × 30 次保存有 7 路抛
`PermissionError: [WinError 32] ... settings.yaml.tmp -> settings.yaml`——两路
抢同一个 tmp。另外只要还有别的句柄开着目标文件（设置页的 yaml 编辑器、
杀软扫描、用户用编辑器打开着），Windows 上 `os.replace` 需要 DELETE 权限，
必然 `WinError 5 拒绝访问`，保存直接失败且用户看不到原因。

判据：
  1. 多线程并发保存不抛异常，最终文件是合法 YAML；
  2. 保存过程中不残留 .tmp 垃圾；
  3. 目标文件被别的句柄占着时仍然保存成功；
  4. 写入的内容就是传进去的内容（不丢键、不乱序）；
  5. 原子性：写到一半中断不会留下半截文件（用「旧内容或新内容」判断）。
"""

import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from afk_core import config

FAILS = []


def check(name, ok, detail=""):
    if ok:
        print("ok   {0}".format(name))
    else:
        print("FAIL {0}  <- {1}".format(name, detail))
        FAILS.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="afk_cfgsave_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print("FAILED {0}: {1}".format(len(FAILS), "; ".join(FAILS)))
        print("CONFIG SAVE FAIL")
        return 1
    print("CONFIG SAVE PASS")
    return 0


def _tmp_leftovers(tmp):
    return sorted(p.name for p in tmp.iterdir() if p.suffix == ".tmp")


def _run(tmp):
    target = tmp / "settings.yaml"

    # 1. 并发保存：抢同一个 tmp 会抛 WinError 32
    errors = []

    def worker(n):
        for i in range(30):
            try:
                config.save_yaml(target, {"worker": n, "round": i, "payload": "x" * 200})
            except Exception as exc:
                errors.append("{0}: {1}".format(type(exc).__name__, exc))
                return

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("并发保存不抛异常", not errors, errors[:3])
    check("并发保存后没有残留 .tmp", not _tmp_leftovers(tmp), _tmp_leftovers(tmp))
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        parsed = isinstance(data, dict)
    except Exception as exc:
        parsed = False
        data = "{0}: {1}".format(type(exc).__name__, exc)
    check("并发保存后文件仍是合法 YAML", parsed, data)

    # 2. 目标文件被别的句柄占着（编辑器/杀软/设置页编辑器）也要能保存
    target.write_text("old: 1\n", encoding="utf-8")
    with open(target, "r+b") as holder:
        holder.read()
        try:
            config.save_yaml(target, {"new": 2})
            saved = True
            err = ""
        except Exception as exc:
            saved = False
            err = "{0}: {1}".format(type(exc).__name__, exc)
    check("目标文件被别的句柄占着时仍能保存", saved, err)
    if saved:
        check("被占用时写入的内容正确",
              yaml.safe_load(target.read_text(encoding="utf-8")) == {"new": 2},
              target.read_text(encoding="utf-8"))
    check("被占用时也不残留 .tmp", not _tmp_leftovers(tmp), _tmp_leftovers(tmp))

    # 3. 内容保真：嵌套 + 中文 + 列表顺序
    payload = {
        "chain": {"order": ["BAAH", "MaaEnd", "March7thAssistant"]},
        "idle": {"enabled": True, "minutes": 30, "hint": "空闲提示"},
        "housekeeping": {"events_max_mb": 50},
    }
    config.save_yaml(target, payload)
    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    check("嵌套结构与列表顺序保真", loaded == payload, loaded)
    check("中文不转义", "空闲提示" in target.read_text(encoding="utf-8"),
          target.read_text(encoding="utf-8")[:80])

    # 4. 原子性：交替写入两种内容，中途不能被读到别的形态
    #    （读取端用 load_yaml，等价于 GUI 每次保存前的读盘）
    target.write_text(yaml.safe_dump({"variant": "a"}, allow_unicode=True), encoding="utf-8")
    bad = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                data = config.load_yaml(target, None)
            except Exception as exc:
                bad.append("{0}: {1}".format(type(exc).__name__, exc))
                return
            if data not in ({"variant": "a"}, {"variant": "b"}):
                bad.append("读到意外内容: {0}".format(data))
                return

    watcher = threading.Thread(target=reader)
    watcher.start()
    try:
        for i in range(50):
            config.save_yaml(target, {"variant": "b" if i % 2 else "a"})
    finally:
        stop.set()
        watcher.join()
    check("并发读到的永远是完整内容，不会是半截文件", not bad, bad[:3])

    # 5. 目录不存在时自动创建
    deep = tmp / "a" / "b" / "c" / "settings.yaml"
    try:
        config.save_yaml(deep, {"deep": True})
        ok = deep.exists() and yaml.safe_load(deep.read_text(encoding="utf-8")) == {"deep": True}
    except Exception as exc:
        ok = False
        deep = "{0}: {1}".format(type(exc).__name__, exc)
    check("父目录不存在时自动创建", ok, deep)


if __name__ == "__main__":
    sys.exit(main())
