"""`tests/smoke_isolation.isolate_runtime()` 本身的回归。

真实事故（2026-11-06 发现）：isolate_runtime 只隔离了运行数据（LOGS_DIR /
STATE_FILE / EVENTS_FILE …），**没有隔离配置文件**。而 MainWindow 在关窗和拖动
桌面小组件时会把窗口尺寸、`widget_pos` 写回 settings.yaml，`config.save_yaml`
又是「dump 一遍整棵字典」——注释、空行、原有缩进全部丢失。

实测：只跑一次 `tests/smoke_gui.py`，仓库里真实的 `configs/core/settings.yaml`
就被重写了一遍（5 段说明注释消失、`widget_pos: []` 变成 `[543, 96]`），用户下次
打开程序看到的是测试留下的窗口位置，`git status` 也永远是脏的。以前每个需要
跑 GUI 的冒烟都得自己手动改 `config.SETTINGS_YAML`，漏一个就污染仓库。

现在 isolate_runtime 会把「仍指向 configs/core 真文件」的 settings / projects /
repair 三份配置复制成临时副本并接管。判据：

1. 进入隔离后，三个 config 路径都不再指向仓库真文件；
2. 隔离期间 `save_yaml` 写这些路径，仓库真文件一个字节都不变；
3. 隔离期间读到的仍是真配置的内容（副本是从真文件复制的）；
4. restore() 之后路径回到真文件，且内容未被污染；
5. 已经被别的冒烟自己换成临时副本的配置**不被抢占**（那些冒烟自己做隔离）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config
from tests import smoke_isolation

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  <- " + str(detail)))
    if not ok:
        FAILED.append(name)


def _real(name):
    return Path(config.CORE_CONFIG_DIR) / name


def main():
    real_settings = _real("settings.yaml")
    real_projects = _real("afk_projects.yaml")
    real_repair = _real("repair_rules.yaml")
    before = {p: p.read_bytes() for p in (real_settings, real_projects, real_repair)}
    originals = [config.SETTINGS_YAML, config.PROJECTS_YAML, config.REPAIR_YAML]

    guard = smoke_isolation.isolate_runtime(prefix="afk_iso_guard_")
    try:
        check(
            "隔离后 settings.yaml 不再指向仓库真文件",
            Path(config.SETTINGS_YAML).resolve() != real_settings.resolve(),
            config.SETTINGS_YAML,
        )
        check(
            "隔离后 afk_projects.yaml 不再指向仓库真文件",
            Path(config.PROJECTS_YAML).resolve() != real_projects.resolve(),
            config.PROJECTS_YAML,
        )
        check(
            "隔离后 repair_rules.yaml 不再指向仓库真文件",
            Path(config.REPAIR_YAML).resolve() != real_repair.resolve(),
            config.REPAIR_YAML,
        )

        settings = config.settings()
        check("隔离期间仍读得到真配置内容", bool(settings.get("pre_run")), list(settings)[:6])

        # 模拟 MainWindow 关窗：把窗口尺寸写回 settings.yaml
        config.save_yaml(config.SETTINGS_YAML, {"gui": {"window_size": [1280, 800], "widget_pos": [11, 22]}})
        check(
            "隔离期间的保存写进了临时副本",
            "window_size" in Path(config.SETTINGS_YAML).read_text(encoding="utf-8"),
        )
        after = {p: p.read_bytes() for p in (real_settings, real_projects, real_repair)}
        check("隔离期间保存不动仓库里的 settings.yaml", after[real_settings] == before[real_settings])
        check("隔离期间保存不动仓库里的 afk_projects.yaml", after[real_projects] == before[real_projects])
        check("隔离期间保存不动仓库里的 repair_rules.yaml", after[real_repair] == before[real_repair])
        check(
            "隔离期间真文件注释没被抹掉",
            "导入旧副本配置时的源根目录" in real_settings.read_text(encoding="utf-8"),
        )
    finally:
        guard.restore()

    check("restore() 后 settings.yaml 回到真文件", Path(config.SETTINGS_YAML) == Path(originals[0]), config.SETTINGS_YAML)
    check("restore() 后 afk_projects.yaml 回到真文件", Path(config.PROJECTS_YAML) == Path(originals[1]))
    check("restore() 后 repair_rules.yaml 回到真文件", Path(config.REPAIR_YAML) == Path(originals[2]))
    check("restore() 后真配置内容未变", real_settings.read_bytes() == before[real_settings])
    check("restore() 后 logs 目录回到真目录", Path(config.LOGS_DIR) == Path(ROOT / "logs"), config.LOGS_DIR)

    # 已经自己隔离过配置的冒烟（smoke_shell_ui 等）不能被抢占
    import tempfile

    own = Path(tempfile.mkdtemp(prefix="afk_iso_own_")) / "settings.yaml"
    own.write_text("gui:\n  window_size: []\n", encoding="utf-8")
    previous = config.SETTINGS_YAML
    config.SETTINGS_YAML = own
    guard = None
    try:
        guard = smoke_isolation.isolate_runtime(prefix="afk_iso_own_")
        check("已经自己隔离过的配置不会被抢占", Path(config.SETTINGS_YAML) == own, config.SETTINGS_YAML)
    finally:
        if guard is not None:
            guard.restore()
        config.SETTINGS_YAML = previous
        try:
            own.unlink()
        except OSError:
            pass

    # 上一轮隔离的临时配置文件已被删掉（smoke_completion 的用法：反复
    # isolate_runtime 但只 rmtree、不 restore），此时必须按「还没隔离」重来，
    # 不能抱着一个不存在的路径。实测这条会让 smoke_completion 整段抛
    # FileNotFoundError。
    stale_dir = Path(tempfile.mkdtemp(prefix="afk_iso_stale_"))
    stale = stale_dir / "settings.yaml"
    stale.write_text("gui: {}\n", encoding="utf-8")
    previous = config.SETTINGS_YAML
    config.SETTINGS_YAML = stale
    guard = None
    try:
        stale.unlink()  # 目录还在，文件没了 = 上一轮隔离目录被清掉的形态
        stale_dir.rmdir()
        guard = smoke_isolation.isolate_runtime(prefix="afk_iso_stale2_")
        current = Path(config.SETTINGS_YAML)
        check("上一轮的临时配置已消失时自动换成新副本", current != stale and current.exists(), current)
        check("自动换来的副本读得到真配置", bool(config.settings().get("pre_run")))
    finally:
        if guard is not None:
            guard.restore()
        config.SETTINGS_YAML = previous

    check("收尾后 settings.yaml 仍是真文件", Path(config.SETTINGS_YAML).resolve() == real_settings.resolve(), config.SETTINGS_YAML)

    print("ISOLATION-GUARD", "PASS" if not FAILED else "FAIL")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
