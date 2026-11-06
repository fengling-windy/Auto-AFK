"""抹掉子项目输出里的 ANSI 转义序列。

真实缺陷（2026-11-06 发现）：子项目往 stdout 打颜色是常态，MaaEnd 的
`[ERR]` 行就长这样（从 logs/projects/MaaEnd_20261106_152713_141e2d.out.log
实测取出）：

    \\x1b[31m[2026-11-06 15:28:36.001][ERR][Px33084][ActionHelper.cpp]...\\x1b[0m

这些字节被尾随读进事件流之后：
  * GUI 实时日志区直接显示成 `[31m...` 这种看不懂的垃圾（QPlainTextEdit
    不解释转义序列）；
  * 错误报告的 stdout_tail.txt 同样带垃圾；
  * `[31m` 还会混进错误关键字，让 report.error_signature() 把「同一条错误
    带不同颜色」算成两种错误，去重失效。

统一在读取层抹掉，下游全干净。
"""

import re

# CSI（颜色/光标）、OSC（窗口标题，以 BEL 或 ST 结束）、以及其它单字符 ESC 序列
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[@-_]")


def strip_ansi(text):
    """去掉 ANSI 转义序列；没有 ESC 时原样返回（避免无谓的正则开销）。"""
    if not text or "\x1b" not in text:
        return text
    return _ANSI_RE.sub("", text)
