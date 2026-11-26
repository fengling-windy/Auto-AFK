# 贡献指南 (Contributing)

感谢关注或参与 **Auto-AFK** 的开发！在提交 Issue 或 Pull Request 前请先阅读以下约定：

## 开发环境搭建

```powershell
git clone https://github.com/fengling-windy/Auto-AFK.git
cd Auto-AFK
powershell -ExecutionPolicy Bypass -File setup\05_core_venv.ps1
```

推荐使用仓库根目录 `.venv\Scripts\python.exe` 运行桌面界面与测试：

```powershell
# 启动图形界面
.venv\Scripts\python.exe gui\main.py

# 运行核心回归测试（安全子集，不触碰真实配置与子项目）
.venv\Scripts\python.exe tools\run_smokes.py
```

## 目录分工

- `afk_core/`：核心无头逻辑（配置读写、进程监督、项目生命周期、修复引擎、调度、通知、CLI）。
- `gui/`：PySide6 桌面端（主题令牌、动效组件、四页面架构及独立悬浮小组件）。
- `monitor_kit/`：注入子项目的日志/完成信号捕获与补丁工具。
- `packaging/`：PyInstaller 与 Inno Setup Windows 安装包构建配置。
- `setup/` / `tools/`：环境引导、快捷方式与辅助诊断脚本。
- `tests/`：独立注册表隔离的冒烟与回归测试。

## 提交规范与注意事项

1. **禁止提交个人配置与密钥**：切勿提交 `configs/core/settings.yaml` 中的个人运行状态、`configs/core/github_token.txt`、`configs/<子项目>/` 个人配置、任何 `.token`/`.lnk`/`logs/` 文件或子项目源码目录。
2. **换行符**：所有源码与文档统一使用 LF（仓库根目录已配置 `.gitattributes`）。
3. **测试隔离**：所有会触发任务链执行的测试必须使用 `tests/smoke_isolation.py` 的 `isolate_runtime()`，避免写入真实的 `logs/`。
4. **开源协议**：本项目采用 **GNU General Public License v3.0 (GPL-3.0)** 发布，贡献代码即视为同意在同协议下分发。
