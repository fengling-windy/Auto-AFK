# 第三方开源组件与上游项目声明 (Third-Party Notices)

Auto-AFK 基于 **GNU General Public License v3.0 (GPL-3.0)** 开源。本项目桌面发行包（`.exe` 安装程序与便携包）包含或集成以下第三方开源软件与生态支持：

## 运行时依赖库（打包内置）

| 组件 | 开源协议 | 用途说明 | 项目主页 |
|---|---|---|---|
| **PySide6 (Qt for Python)** | LGPL-3.0 / GPL-3.0 | 跨平台 GUI 框架与高分屏图形渲染 | https://wiki.qt.io/Qt_for_Python |
| **psutil** | BSD-3-Clause | 进程树枚举、孤儿进程清理与系统状态采集 | https://github.com/giampaolo/psutil |
| **PyYAML** | MIT | YAML 配置文件与项目注册表读写解析 | https://github.com/yaml/pyyaml |
| **mss** | MIT | 异常与卡死现场快速桌面截图取证 | https://github.com/BoboTiG/python-mss |
| **onepush** | MIT | 多通道（ServerChan / Bark / Telegram / 企业微信等）通知推送 | https://github.com/y1ndan/onepush |
| **winotify** | MIT | Windows 原生系统 Toast 弹窗通知 | https://github.com/versa-syahjur/winotify |

## 集成与编排支持的上游项目（独立源码下载安装，不包含在本仓库发行包内）

Auto-AFK 的「项目配置」页面支持按需克隆并构建以下优秀社区开源项目，各项目版权与许可均归其原作者所有：

| 项目 | 开源协议 | 仓库地址 |
|---|---|---|
| **March7thAssistant（三月七小助手）** | GPL-3.0 | https://github.com/moesnow/March7thAssistant |
| **BAAH（BlueArchiveArisHelper）** | MIT | https://github.com/BlueArchiveArisHelper/BAAH |
| **MaaEnd** | AGPL-3.0 | https://github.com/MaaEnd/MaaEnd |
| **ZenlessZoneZero-OneDragon（绝区零一条龙）** | GPL-3.0 | https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon |
| **BetterGI（更好的原神）** | GPL-3.0 | https://github.com/babalae/better-genshin-impact |

另外，窗口消失完成判定（`window_finish`）与历史运行耗时统计（`durations.jsonl`）思路借鉴了 [YukinoChan/Anigame-script-manager](https://github.com/yukikino001-ship-it/Anigame-script-manager)（GPL-3.0）。

## LGPL / GPL 分发说明

- 根据 **GPL-3.0** 与 **LGPL-3.0** 条款，本发行包完整附带 `LICENSE` 与本声明文件；源代码可从官方仓库 https://github.com/fengling-windy/Auto-AFK 获取，用户可自行替换或重新构建 PySide6 动态链接库。
- 本项目与上述任何游戏或自动化工具开发团队无从属关系；请严格遵守各上游项目的使用条款与游戏服务协议。
