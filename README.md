# Auto-AFK — 多游戏自动化一键挂机编排器

[![Release](https://img.shields.io/github/v/release/fengling-windy/Auto-AFK?display_name=tag&color=3d7dfa)](https://github.com/fengling-windy/Auto-AFK/releases)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](pyproject.toml)
[![GUI: PySide6](https://img.shields.io/badge/GUI-PySide6-41CD52.svg)](requirements.txt)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg)](#安装方式)

**Auto-AFK** 是一个面向多游戏自动化脚本的统一桌面编排器与无人值守运行守护工具：将 **March7thAssistant**、**BAAH**、**MaaEnd**、**绝区零一条龙**、**BetterGI** 等自动化项目整合在同一套调度、监控、自动修复、错误打包与现代极简 PySide6 桌面端中。

---

## 核心特性

### 1. 现代极简桌面端（深色 / 浅色双主题）
- **总览舞台（上一个 ← 当前运行 → 下一个）**：高亮聚焦当前正在执行的任务，两侧清晰呈现上一完成结果与队列下一待办项；并行运行多任务时同框实时计时并支持一键停止、日志跳转与原生配置界面入口。
- **多列可缩放实时日志**：每个正在运行的项目自动独占一列（支持关闭自动弹出或手动通过下拉框打开任意列），支持横向拖动分隔条自由缩放、关键字防抖检索与滚动跟随控制；系统/更新/模拟器事件归集至「系统日志」列。
- **运行顺序（整卡弹簧拖拽 + 同项目多配置）**：
  - 按住卡片任意非按钮区域即可直接拖起，配合**二阶阻尼弹簧物理动画（Spring Animation）**与实时卡片中心检测平滑让位重排。
  - 支持为**同一项目添加多条运行任务**并分别指定不同的配置文件/实例（如多账号 `BAAH` + `BAAH#2`），并在相邻卡片间开启带流动光效的**并行运行连线**。
- **项目配置与生命周期管理**：顶部项目标签栏 + 右侧「受支持项目」一键安装控制（已安装自动置灰提示）；提供**环境检测**、**打开此项目界面**、**更新项目**、**重装项目**（卸载源码前自动摘除 junction，安全保留 `configs/<项目>` 用户配置）与**删除项目**，并可直接在面板内编辑核心运行参数。
- **分组设置页**：通过顶部子导航整合为六大清晰分组——**常规与外观**、**自动化与任务链**、**更新与模拟器**、**修复与通知**、**日志与维护**、**高级设置**。

### 2. 统一编排、监测与自愈体系
- **任务模式与兼容分组**：支持 `chain.tasks`（精确到单任务配置/实例），同时向后兼容原有 `groups` + `chain.order` 调度。
- **全链路监测**：通过 `monitor_kit` 注入与外部日志文件追踪，结合 `require_finish`、`min_runtime_sec`、`window_finish`（游戏窗口消失防误判）和 `stop_on_error` 实现精确成功/失败/卡死判定。
- **自动修复与错误报告**：按 `configs/core/repair_rules.yaml` 自动执行环境修复与重跑；无法自愈时自动打包 `report.md`、事件切片、配置快照与桌面截图至 `logs/reports/`。
- **全自动运维**：启动自动清理过期日志、空闲检测弹窗倒计时自动挂机（今日已整链完成可跳过）、定时任务、MuMu 模拟器托管、Windows 系统 Toast 与 `onepush` 多通道推送。

---

## 支持的自动化项目

| 项目 | 对应游戏 | 运行形态 | 默认环境 |
|---|---|---|---|
| **March7thAssistant** | 崩坏：星穹铁道 | 云游戏 / 后台 CLI | Python 3.12 |
| **BAAH** | 蔚蓝档案 | MuMu 模拟器 / ADB | Python 3.11 |
| **MaaEnd** | 明日方舟：终末地 | MXU 独占前台 | Python 3.12 (uv) |
| **ZenlessZoneZero-OneDragon** | 绝区零 | PC 客户端 CLI 独占前台 | Python 3.11 |
| **BetterGI** | 原神 | `startOneDragon` CLI 独占前台 | .NET 8 |

---

## 安装方式

### 方式一：下载 Windows `.exe` 安装包 / 绿色便携版（推荐）
1. 前往 [GitHub Releases](https://github.com/fengling-windy/Auto-AFK/releases) 下载最新版本的：
   - **`Auto-AFK-Setup-vX.Y.Z.exe`**：标准 Windows 安装向导，支持用户目录安装、桌面/开始菜单快捷方式及完整卸载；升级安装时自动保留 `configs\core\` 已有配置。
   - **`Auto-AFK-vX.Y.Z-win64-portable.zip`**：解压即可运行的便携版。
2. 打开 Auto-AFK 后，切到左侧「**项目配置**」页，在顶部右侧下拉框选择需要使用的项目点击「**安装项目**」完成对应脚本的拉取与构建。

### 方式二：从源码运行（开发者 / 进阶用户）

```powershell
git clone https://github.com/fengling-windy/Auto-AFK.git
cd Auto-AFK

# 创建中心虚拟环境并安装核心依赖
powershell -ExecutionPolicy Bypass -File setup\05_core_venv.ps1

# 启动桌面端
.venv\Scripts\python.exe gui\main.py
```

如需一键部署全套引导脚本，可按顺序执行 `setup\` 下编号脚本：

```powershell
cd setup
powershell -ExecutionPolicy Bypass -File 10_install_tools.ps1
powershell -ExecutionPolicy Bypass -File 20_clone.ps1
powershell -ExecutionPolicy Bypass -File 05_core_venv.ps1
powershell -ExecutionPolicy Bypass -File 30_build_m7a.ps1
powershell -ExecutionPolicy Bypass -File 31_build_baah.ps1
powershell -ExecutionPolicy Bypass -File 32_build_maaend.ps1
powershell -ExecutionPolicy Bypass -File 33_build_bettergi.ps1
powershell -ExecutionPolicy Bypass -File 40_apply_patches.ps1
powershell -ExecutionPolicy Bypass -File 50_verify.ps1
```

---

## 目录结构

```text
Auto-AFK/
├─ afk_core/              核心调度、监控、修复引擎、项目生命周期与 CLI
├─ gui/                   PySide6 现代桌面界面与悬浮小组件
├─ monitor_kit/           子项目进程监测注入与补丁工具
├─ configs/
│  └─ core/               全局核心配置与项目注册表 (settings / afk_projects / repair_rules)
├─ packaging/             PyInstaller spec、Inno Setup (.iss) 与本地打包脚本
├─ setup/                 环境初始化、子项目构建与快捷方式生成脚本
├─ tools/                 静默启动器 (VBS)、视觉预览与测试调度工具
├─ tests/                 配置隔离的冒烟与回归测试套件
├─ LICENSE                GNU General Public License v3.0 (GPL-3.0)
├─ THIRD_PARTY_NOTICES.md 第三方依赖与集成项目开源许可声明
├─ pyproject.toml         标准 Python 项目元数据
└─ requirements.txt       运行时依赖清单
```

---

## 常用启动与 CLI 命令

- **桌面端入口**：
  - `Auto-AFK.exe`（安装包/便携版）或 `Auto-AFK.bat`：静默启动图形界面。
  - `一键挂机.bat` / `--autorun`：启动界面并自动开始一轮任务链。
  - `Auto-AFK-Admin.bat` / `--tray`：管理员权限启动或启动后驻留系统托盘。
- **命令行工具（源码环境）**：
  ```powershell
  Auto-AFK-CLI.bat run            # 按顺序与并行组运行一键挂机
  Auto-AFK-CLI.bat start BAAH     # 单独启动指定项目
  Auto-AFK-CLI.bat stop-chain     # 当前步骤完成后软停止任务链
  Auto-AFK-CLI.bat status --json  # 查看运行状态快照
  Auto-AFK-CLI.bat stats          # 查看各项目历史运行耗时统计
  Auto-AFK-CLI.bat doctor         # 全项目与宿主环境自检
  Auto-AFK-CLI.bat gui BAAH       # 打开指定项目原生界面
  ```

---

## 本地构建 `.exe` 安装包

确保本机已安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php) 与 PyInstaller（`pip install pyinstaller`）：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1 -Version 2.0.0
```

产物输出在 `packaging\output\`：
- `Auto-AFK-Setup-v2.0.0.exe`
- `Auto-AFK-v2.0.0-win64-portable.zip`

此外，仓库已配置 [GitHub Actions Release 工作流](.github/workflows/release.yml)：每次推送 `v*` 标签或手动触发 workflow 时会自动在云端 `windows-latest` 构建安装包与便携包并发布到 GitHub Releases。

---

## 回归测试

```powershell
.venv\Scripts\python.exe tools\run_smokes.py            # 运行安全回归子集
.venv\Scripts\python.exe tools\run_smokes.py --group ui # 仅运行 GUI 离屏测试
```

---

## 开源协议

本项目采用 **[GNU General Public License v3.0 (GPL-3.0)](LICENSE)** 协议开源分发。  
运行时依赖库及所支持的上游自动化项目的完整开源许可与版权归属请参阅 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；欢迎阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 参与贡献。
