"""子项目输出里的 ANSI 转义必须被抹掉。

真实缺陷（2026-11-06 从 logs/projects/MaaEnd_20261106_152713_141e2d.out.log
实测）：MaaEnd 的 `[ERR]` 行带颜色码——

    \\x1b[31m[2026-11-06 15:28:36.001][ERR][Px33084][ActionHelper.cpp]...\\x1b[0m

这些字节被 FileTailer 读进事件流之后：
  1. GUI 实时日志区直接把转义显示成 `[31m` 这种垃圾（QPlainTextEdit 不解释它）；
  2. 错误报告里的 stdout_tail.txt 同样带垃圾；
  3. `[31m` 混进错误关键字，report.error_signature() 会把「同一条错误带不同颜色」
     算成两种，去重失效；
  4. `_level_for_line()` 判断级别时也会先撞上 `[31m`。

判据：
  * strip_ansi 能去掉 CSI 颜色、OSC 标题、单字符 ESC 序列，且不动普通文本；
  * 没有 ESC 的文本原样返回（同一个对象，不做无谓拷贝）；
  * FileTailer 读出来的行已经干净（这是唯一的读取出口）；
  * report._tail 写进报告的尾部也干净；
  * 一条带颜色的 [ERR] 行仍能被 error_patterns 命中（抹颜色不能影响判定）。
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import report
from afk_core.ansi import strip_ansi
from afk_core.monitor import RunWatcher
from afk_core.supervisor import FileTailer

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  <- " + repr(detail)))
    if not ok:
        FAILED.append(name)


def main():
    red = "\x1b[31m[ERR] ActionHelper failed to get target rect\x1b[0m"
    check("CSI 颜色被抹掉", strip_ansi(red) == "[ERR] ActionHelper failed to get target rect", strip_ansi(red))

    bold = "\x1b[1;32m[INFO]\x1b[0m starting"
    check("带参数的多段 CSI 被抹掉", strip_ansi(bold) == "[INFO] starting", strip_ansi(bold))

    osc = "\x1b]0;window title\x07[INFO] body"
    check("OSC 标题被抹掉", strip_ansi(osc) == "[INFO] body", strip_ansi(osc))

    cursor = "\x1b[2K\x1b[1G[INFO] redrawn"
    check("光标控制序列被抹掉", strip_ansi(cursor) == "[INFO] redrawn", strip_ansi(cursor))

    single = "\x1bM[INFO] reverse index"
    check("单字符 ESC 序列被抹掉", strip_ansi(single) == "[INFO] reverse index", strip_ansi(single))

    plain = "[ERR] ActionHelper failed to get target rect"
    check("没有 ESC 的文本原样返回", strip_ansi(plain) == plain and strip_ansi(plain) is plain)
    check("空串安全", strip_ansi("") == "")
    check("None 安全", strip_ansi(None) is None)
    check("只有 ESC 的行被清成空", strip_ansi("\x1b[31m") == "", strip_ansi("\x1b[31m"))

    mixed = "\x1b[32m[2026-11-06 15:28:36.001][ERR][Px33084] failed\x1b[0m\n"
    check("真实 MaaEnd 行被清干净", "\x1b" not in strip_ansi(mixed) and "failed" in strip_ansi(mixed))

    # FileTailer：唯一的读取出口必须是干净的
    tmp = Path(tempfile.mkdtemp(prefix="afk_ansi_"))
    try:
        log = tmp / "child.out.log"
        log.write_bytes(
            b"\x1b[32m[INFO] starting\x1b[0m\r\n"
            b"\x1b[31m[ERR] ActionHelper failed to get target rect\x1b[0m\r\n"
        )
        tailer = FileTailer(log, start_at_end=False)
        lines = tailer.read_new()
        check("FileTailer 读出两行", len(lines) == 2, lines)
        check("FileTailer 读出的行不带 ESC", all("\x1b" not in line for line in lines), lines)
        check("FileTailer 读出的行不带字面 [31m", all("[31m" not in line for line in lines), lines)
        check("FileTailer 保留了正文", lines and lines[-1] == "[ERR] ActionHelper failed to get target rect", lines)

        tailer2 = FileTailer(log, start_at_end=False)
        again = tailer2.read_new()
        check("重复读取结果一致（可重复）", again == lines, again)

        # 报告尾部同样要干净
        tail = report._tail(log)
        check("报告尾部不带 ESC", "\x1b" not in tail, tail)
        check("报告尾部不带字面 [31m", "[31m" not in tail, tail)
        check("报告尾部有内容", "[ERR]" in tail, tail)

        # 抹掉颜色不能影响错误判定
        watcher = RunWatcher(
            "MaaEnd",
            "r1",
            {"error_patterns": [r"\[ERR\]"], "warn_patterns": [], "success_patterns": [], "finish_patterns": []},
        )
        for line in lines:
            watcher.feed_text(line)
        check("带颜色的 [ERR] 行仍被判为错误", bool(watcher.error_messages), watcher.error_messages)
        check("记录下来的错误文本也干净", all("\x1b" not in m for m in watcher.error_messages), watcher.error_messages)
        check("last_line 也干净", "\x1b" not in watcher.last_line, watcher.last_line)

        # 不同颜色的同一条错误算同一个签名（去重依赖这个）
        sig_red = report.error_signature("\x1b[31m[ERR] same thing\x1b[0m")
        sig_plain = report.error_signature("[ERR] same thing")
        check("颜色不同不影响错误签名", sig_red == sig_plain and sig_plain != "", (sig_red, sig_plain))
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    print("ANSI", "PASS" if not FAILED else "FAIL")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
