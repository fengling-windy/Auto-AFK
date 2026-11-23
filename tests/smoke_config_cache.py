r"""配置读盘缓存：命中要快，但不能把调用方的改动串给别人。

历史背景：``settings()`` / ``registry()`` 是所有模块的配置入口，一次开窗要调
几十次（空转时 GUI 每 300ms 还会调一次），每次都重读文件 + 重新解析 YAML。
走 CSafeLoader 后单次解析 settings 0.46ms、afk_projects 1.21ms，开窗累计仍是
几十毫秒的纯重复劳动，所以加了「最近一次」的读盘缓存。

缓存是全局共享的，最怕两件事：
  1. 命中时直接把缓存对象返回——调用方普遍拿到就改
     （``settings.setdefault("pre_run", {})``、``gui_cfg["window_size"] = [...]``），
     共享一份字典会让这些改动串到别的调用方，甚至被下一次 save_yaml 写回磁盘。
  2. 文件改了还读缓存——配置都是 ``_write_atomic`` 用 os.replace 落盘的，
     换了文件 mtime/size 一定变，必须能识别出来重新解析。

判据：
  1. 同一份文件连续调用，结果相等；
  2. 两次调用拿到的是不同对象（深拷贝隔离），改一份不影响另一份；
  3. 改返回值不会污染后续调用；
  4. 文件内容变了能读到新值（mtime/size 变化即失效）；
  5. 文件不存在时返回默认值且不抛；
  6. settings 与 registry 的缓存互不干扰；
  7. 缓存只留最近一次（不会无限攒旧版本）。
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

FAILS = []


def check(name, ok, detail=""):
    if ok:
        print("ok   {0}".format(name))
    else:
        print("FAIL {0}  <- {1}".format(name, detail))
        FAILS.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="afk_cfgcache_"))
    originals = (config.SETTINGS_YAML, config.PROJECTS_YAML)
    try:
        config.SETTINGS_YAML = tmp / "settings.yaml"
        config.PROJECTS_YAML = tmp / "afk_projects.yaml"
        _run(tmp)
    finally:
        config.SETTINGS_YAML, config.PROJECTS_YAML = originals
        with config._CACHE_LOCK:
            config._SETTINGS_CACHE.clear()
            config._REGISTRY_CACHE.clear()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print("FAILED {0}: {1}".format(len(FAILS), "; ".join(FAILS)))
        print("CONFIG CACHE FAIL")
        return 1
    print("CONFIG CACHE PASS")
    return 0


def _write(path, data):
    """写配置并确保 mtime 一定变化（Windows 上同一时钟刻度内连写两次时间戳相同）。"""
    text = config.yaml.dump(data, Dumper=config._YAML_DUMPER, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns + 1_000_000, st.st_mtime_ns + 1_000_000))


def _run(tmp):
    settings_path = config.SETTINGS_YAML
    projects_path = config.PROJECTS_YAML

    _write(settings_path, {"gui": {"theme": "dark"}, "chain": {"order": ["A"]}})
    _write(projects_path, {"projects": {"A": {"path": "A"}, "B": {"path": "B"}}})

    first = config.settings()
    second = config.settings()
    check("同一份文件连续读，内容相等", first == second, (first, second))
    check("两次调用不是同一个对象（深拷贝隔离）", first is not second, id(first))
    check("嵌套字典也不是同一个对象", first["gui"] is not second["gui"], id(first["gui"]))

    first["gui"]["theme"] = "light"
    check("改一份返回值不影响下一次调用", config.settings()["gui"]["theme"] == "dark",
          config.settings()["gui"]["theme"])

    first.setdefault("pre_run", {})["at_startup"] = False
    check("往返回值里塞新节不会落到缓存的下一份", "pre_run" not in config.settings(),
          config.settings())

    _write(settings_path, {"gui": {"theme": "light"}, "chain": {"order": ["A", "B"]}})
    check("文件改了能读到新值", config.settings()["gui"]["theme"] == "light",
          config.settings()["gui"]["theme"])
    check("新增的键也读得到", config.settings()["chain"]["order"] == ["A", "B"],
          config.settings()["chain"]["order"])

    # 同样大小、只改一个字符：靠 mtime 失效
    same_size = {"gui": {"theme": "Light"}, "chain": {"order": ["A", "B"]}}
    _write(settings_path, same_size)
    check("大小不变、只改内容也能识别", config.settings()["gui"]["theme"] == "Light",
          config.settings()["gui"]["theme"])

    with config._CACHE_LOCK:
        check("缓存只留最近一次（不攒旧版本）", len(config._SETTINGS_CACHE) <= 1,
              len(config._SETTINGS_CACHE))

    reg = config.registry()
    check("registry 读得到项目", sorted((reg.get("projects") or {}).keys()) == ["A", "B"],
          list((reg.get("projects") or {}).keys()))
    check("registry 与 settings 互不干扰", config.settings()["gui"]["theme"] == "Light",
          config.settings()["gui"]["theme"])
    _write(projects_path, {"projects": {"A": {"path": "A"}, "C": {"path": "C"}}})
    check("registry 文件改了也失效",
          sorted((config.registry().get("projects") or {}).keys()) == ["A", "C"],
          list((config.registry().get("projects") or {}).keys()))

    settings_path.unlink()
    projects_path.unlink()
    check("settings.yaml 不存在时返回字典且不抛", isinstance(config.settings(), dict),
          type(config.settings()).__name__)
    check("afk_projects.yaml 不存在时也返回字典", isinstance(config.registry(), dict),
          type(config.registry()).__name__)

    # 隔离兜底：缓存不能把「配置坏掉」的状态也缓存住
    _write(settings_path, {"gui": "abc", "chain": [1, 2]})
    broken = config.settings()
    check("写成标量的节被归一成映射", isinstance(broken.get("gui"), dict),
          type(broken.get("gui")).__name__)
    check("写成列表的节也被归一成映射", isinstance(broken.get("chain"), dict),
          type(broken.get("chain")).__name__)


if __name__ == "__main__":
    sys.exit(main())
