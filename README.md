# Auto-AFK 一键挂机

在 `E:\GameAFK\Auto-AFK` 下整合五个自动化项目（独立虚拟环境；前三个源码构建在 Auto-AFK 内，
绝区零一条龙复用 `E:\GameAFK\ZenlessZoneZero-OneDragon` 的现成源码安装），
提供统一编排、完成/报错监测、规则化自动修复、错误日志打包与 PySide6 图形界面。

| 项目 | 游戏 | 运行方式 | 环境 |
|---|---|---|---|
| March7thAssistant | 崩坏：星穹铁道（云游戏） | CLI 后台 | Python 3.12 |
| BAAH | 蔚蓝档案（MuMu 模拟器 / ADB） | CLI 后台 | Python 3.11（onnxruntime 1.16.2 限制） |
| MaaEnd | 明日方舟：终末地 | MXU 独占前台 | Python 3.12（uv 工具链） |
| ZenlessZoneZero-OneDragon | 绝区零（PC 客户端） | CLI 一条龙，独占前台 | Python 3.11 |
| BetterGI | 原神（PC 客户端） | CLI `startOneDragon`，独占前台 | .NET 8（dotnet publish，日志监测） |

## 目录结构

```
Auto-AFK\
├─ setup\                 引导脚本（按编号执行，可重入）
├─ tests\                 冒烟/回归测试（smoke_*.py，直接运行）
├─ March7thAssistant\     源码 + .venv
├─ BAAH\                  源码 + .venv
├─ MaaEnd\                源码 + .venv + install\（mxu.exe / maafw / agent）
│                          （绝区零一条龙在 E:\GameAFK\ZenlessZoneZero-OneDragon，注册表直接引用）
├─ BetterGI\              源码 + publish\（BetterGI.exe，.NET 8 构建产物）
├─ monitor_kit\           sitecustomize / afk_hook / patcher 及补丁文件
├─ afk_core\              调度、监控、修复、报告、doctor、CLI
├─ gui\                   PySide6 界面
├─ configs\               配置中心（与源码分离）
│   ├─ core\              afk_projects.yaml / settings.yaml / repair_rules.yaml
│   ├─ March7thAssistant\ config.yaml（同步）+ config、settings（junction）
│   ├─ BAAH\              BAAH_CONFIGS（junction）
│   └─ MaaEnd\            mxu 的 config、debug、cache（junction）
└─ logs\                  事件流、项目日志、错误报告
```

## 首次安装

```powershell
cd E:\GameAFK\Auto-AFK\setup
powershell -ExecutionPolicy Bypass -File 10_install_tools.ps1   # Go / pnpm / Python 3.11+3.12
powershell -ExecutionPolicy Bypass -File 20_clone.ps1           # 克隆三个源码仓库
powershell -ExecutionPolicy Bypass -File 05_core_venv.ps1       # 中心 venv（PySide6 等）
powershell -ExecutionPolicy Bypass -File 30_build_m7a.ps1
powershell -ExecutionPolicy Bypass -File 31_build_baah.ps1
powershell -ExecutionPolicy Bypass -File 32_build_maaend.ps1    # 需 Go/pnpm，耗时最长
powershell -ExecutionPolicy Bypass -File 33_build_bettergi.ps1  # 原神 BetterGI（自动装 .NET 8 运行时）
powershell -ExecutionPolicy Bypass -File 40_apply_patches.ps1   # 注入监测代码
powershell -ExecutionPolicy Bypass -File 50_verify.ps1          # 导入配置 + 自检
```

## 启动

- 图形界面：双击 `Auto-AFK.bat`（打开界面，不自动开跑）或 `一键挂机.bat`（打开界面并自动一键挂机）。
  **关闭窗口即直接退出**（同时停止正在运行的项目）；
  如需恢复"最小化到托盘"，把 `configs\core\settings.yaml` 的 `gui.minimize_to_tray` 改为 `true`。
  - **项目卡片「界面」按钮**：每个挂机项目卡片上点「界面」可打开该项目自己的图形界面
    （三月七小助手=app.py 启动器、BAAH=配置编辑器、MaaEnd=MXU、绝区零=一条龙配置界面、BetterGI=主界面），
    仅打开窗口、**不运行任务也不受监管**，进程独立于 Auto-AFK（关掉 Auto-AFK 后仍保留）；
    也可用 `Auto-AFK-CLI.bat gui <项目名>` 打开。GUI 入口在各项目的 `gui:` 配置块中定义，新增项目照抄即可。
  - **现代化深色界面**：圆角卡片 + 项目色条、状态圆点与状态胶囊、主/次按钮层级、标签页下划线指示、
    深色等宽日志区；实时日志按**对象着色**（每个项目一种颜色，error 红、warn 橙保持不变），
    颜色可在 `configs\core\settings.yaml` 的 `gui.project_colors` 中自定义。
- **快捷方式（均为管理员模式，推荐）**：
  - `Auto-AFK(管理员).lnk`（根目录与桌面各一份）——管理员身份打开界面，**不自动运行任务**；
  - `Auto-AFK 自动挂机(管理员).lnk`（根目录与桌面各一份）——管理员身份打开界面并**自动开始一键挂机**（`--autorun`）；
  - 两者都会弹一次 UAC（M7A/BAAH 控制游戏需要管理员权限）。
  - 重新生成：`.venv\Scripts\python.exe setup\create_shortcuts.py`（会同时生成 `gui\afk.ico` 图标）
  - 可选参数：`--tray` 启动即最小化到托盘；`--autorun` 自动开跑
    （`tools\launch_gui.vbs` 会原样转发附加参数；`Auto-AFK.bat` 不带参数打开纯界面，
    `一键挂机.bat` / `Auto-AFK-CLI.bat run` 为非提权自动开跑入口）
  - 演练模式：`AFK_DRY_RUN=1` 时"一键挂机"只记录日志不真正启动项目
- **管理员模式（推荐，M7A/BAAH 控制游戏需要）**：使用桌面的 `Auto-AFK 自动挂机(管理员).lnk`，或双击 `Auto-AFK-Admin.bat`。
  未提权时监管器会直接拒绝启动 `require_admin` 的项目并给出提示，避免 M7A 自我提权导致监管失联。
- **无 cmd 窗口**：启动器走 `tools\launch_gui.vbs`（wscript 静默）；监管器、更新器、MuMu 管理、doctor、
  修复动作等所有子进程都加了隐藏窗口标志（`afk_core/proc.py`），运行过程中不会闪出控制台。
  管理员快捷方式经 `tools\hidden_run.vbs` 隐藏中转（提权后子进程控制台一律以 SW_HIDE 创建，不弹黑窗），
  GUI 启动时会显式 `ShowWindow` 强制显示主窗口（规避 `nCmdShow=SW_HIDE` 沿 wscript/venv 启动器链传播把窗口藏起）。
  启动脚本带错误捕获并把每个环节写入 `logs\launch_trace.log`（runas 提权为异步请求，
  wscript 必须存活到请求完成，否则提权会被静默丢弃）。只有 `Auto-AFK-CLI.bat` 保留控制台（命令行交互需要）。
- 命令行：
  - `Auto-AFK-CLI.bat run`：一键挂机（先并行 M7A+BAAH，完成后自动跑 MaaEnd）
  - `Auto-AFK-CLI.bat start March7thAssistant`
  - `Auto-AFK-CLI.bat stop BAAH`
  - `Auto-AFK-CLI.bat stop-chain`：**软停止**——正在运行的项目跑完后停止任务链，不再启动后续组（不杀进程，适合"这轮跑完就停"）
  - `Auto-AFK-CLI.bat status --json`
  - `Auto-AFK-CLI.bat stats`：各项目历史运行耗时统计（次数/平均/最长/最近，来自 `logs\durations.jsonl`，用于调 `hang_timeout`）
  - `Auto-AFK-CLI.bat doctor`
  - `Auto-AFK-CLI.bat reports`
  - `Auto-AFK-CLI.bat patch apply|verify|revert`
  - 说明：GUI/CLI 正在运行时会写 `logs\event_server.json`（含端口与 token）；
    `stop` / `status` 会优先通过本地事件服务器远程控制（可停止 GUI 里启动的项目、查看其实时状态），
    token 不匹配的 `/command` 请求会被拒绝；没有运行中的实例时自动回退到本地行为。

运行顺序与分组在 `configs\core\afk_projects.yaml` 的 `groups` 中定义：
`cloud_and_emulator`（M7A 云游戏 + BAAH 走 ADB，互不抢输入，并行）→ `exclusive_foreground`（MaaEnd 独占前台）
→ `zzz_one_dragon`（绝区零一条龙，MaaEnd 之后独占前台）。
组内 `parallel: false` 时成员按顺序串行执行（前一个结束后才启动下一个），`true` 时并行启动并等待全部完成。
组内任一项目失败/卡死**默认不中断整条链**：该项目被跳过，后续组照常执行；等所有组跑完一轮后，
在末尾自动重跑本轮失败的项目（`chain.retry_failed_at_end`，默认 `true`）；
同一个项目在一次一键挂机中最多被启动 `chain.max_attempts_per_project` 次（默认 3，已含进程内重试），
达到上限仍失败则链条结果为 `failed` 并保留报告。要恢复"失败即终止"的老行为，
在 `configs\core\settings.yaml` 的 `chain` 段设 `continue_on_failure: false`（或在某个组上单独设 `continue_on_failure: false`）。

### 空闲自动挂机 / 桌面小组件 / 开机启动

- **空闲自动挂机**（`configs\core\settings.yaml` 的 `idle` 段，默认**关闭**）：
  电脑连续无鼠标/键盘输入达到 `idle.minutes`（默认 30）分钟后，弹出**三选一提示框**：
  - **停止** —— 本次不挂机（用户回来后再离开会重新提醒）；
  - **立刻开始** —— 跑全部组；
  - **仅运行不占用鼠标任务** —— 只跑注册表里标记 `background: true` 的组（默认是云游戏 + 模拟器组），
    不抢前台输入，适合你在电脑前做别的事。

  `idle.prompt_sec`（默认 30）秒内未选择则**自动运行全部任务**；`Esc` 或关闭窗口等同「停止」，
  快捷键 `S` 停止 / `A` 立刻开始 / `B` 仅后台任务。
  **同一次离开只提醒一次**：触发后解除监听，只有你回来（输入恢复）再离开才会重新提醒；
  `idle.cooldown_min`（默认 30）为额外冷却；任务链正在跑时不会弹窗。

  **今天已正常完成就不再自动执行**（`idle.skip_if_completed_today`，默认开）：
  当天成功跑完过**一整条链**后，空闲到点也不再弹窗/自动挂机（当天成功时写
  `logs\last_chain_success.json`，只比对当天日期，**跨天自动失效**，无需清理）。
  只有「整链成功」才算完成——手动单独跑某个项目成功不算。
  GUI 设置页「空闲自动挂机」卡片会显示「今日状态：已完成/未完成」，并可点
  「今天还没完成（恢复自动挂机）」清除标记；CLI 同样可查/清除：

  ```powershell
  Auto-AFK-CLI.bat idle                    # 查看空闲状态 + completed today + 上次跳过原因
  Auto-AFK-CLI.bat idle --on               # 打开
  Auto-AFK-CLI.bat idle --off              # 关闭
  Auto-AFK-CLI.bat idle --clear-completion # 清除「今日已完成」标记，今天仍会再自动挂机
  Auto-AFK-CLI.bat run --background        # 手动只跑 background 组（等价于「仅运行不占用鼠标任务」）
  ```

- **桌面小组件**（`gui.widget_enabled`，默认**关闭**）：屏幕上的常驻小浮窗，显示任务链状态胶囊、
  正在运行的项目、以及「空闲多少 / 再多久自动开始」的倒计时；带「一键挂机 / 全部停止 / 显示主窗口」
  三个按钮，可拖动（位置记在 `gui.widget_pos`）、可折叠。托盘菜单「桌面小组件」或设置页可开关。
  主窗口加 `--no-widget` 参数启动则不创建小组件。

- **开机启动**：写当前用户注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的
  `Auto-AFK` 值（**不需要管理员权限**，关闭开关即删除该项，注册表里一眼可见）。两种模式：

  ```powershell
  Auto-AFK-CLI.bat autostart status          # 查看当前是否已注册、用的是哪种模式
  Auto-AFK-CLI.bat autostart on              # 默认 tray 模式：静默拉起 GUI（--tray），无黑窗、不弹 UAC
  Auto-AFK-CLI.bat autostart on --mode admin # admin 模式：经 launch_admin.vbs 提权，开机弹一次 UAC
                                             #（M7A/BAAH 控制游戏需要管理员权限时选这个）
  Auto-AFK-CLI.bat autostart off             # 取消开机启动
  ```

  典型用法：开机自启选 **admin** 模式 + 打开**空闲自动挂机**，即可实现
  「开机后挂机程序常驻 → 你离开电脑 30 分钟 → 弹出提示框 → 不选择就自动跑完所有日常」。
  自启 tray 模式时 GUI 以 `--tray` 启动（只驻留托盘，不弹主窗口）。

原神 BetterGI **默认不参与一键挂机**（已从 groups 移除，避免误跑），可在 GUI 卡片上点「启动」手动单独运行；
如需恢复原神的自动挂机（作为最后一组串行执行），把下面的分组加回 `groups` 列表末尾即可：
```yaml
  - id: bettergi
    label: 原神 BetterGI
    parallel: false
    members:
      - BetterGI
```

### 原神 BetterGI（better-genshin-impact）
- **前置条件（重要）**：
  1. 本机需**已安装原神客户端**并至少**登录过一次**（`doctor` 的 `genshin:install` 会检查）；
  2. BetterGI 需要管理员权限运行（游戏通常以管理员启动）；
  3. 首次使用请运行 `publish\BetterGI.exe`（管理员），在 GUI 内确认/设置游戏路径，
     需要刷秘境/讨伐/地脉花时配置好队伍与秘境；这些战斗任务需在一条龙页手动添加。
  4. 默认已生成 `User\OneDragon\默认配置.json`：启用领取邮件 / 领取每日奖励 / 领取尘歌壶奖励 / 合成树脂，
     并设置"完成后操作 = 关闭软件"（`doctor` 的 `BetterGI:tasks` 会显示启用任务数）。
- 社区最主流的原神自动化项目（14k+ stars）：自动拾取/剧情/秘境/地脉花/一条龙等；
- 源码构建：`dotnet publish BetterGenshinImpact\BetterGenshinImpact.csproj -c Release -r win-x64 --self-contained false -o publish`
  （NuGet 会自动带上模型与地图资源；需要 .NET 8 桌面运行时，脚本会自动安装）；
- 无人值守入口：`publish\BetterGI.exe startOneDragon [配置名]`，启动后自动执行"一条龙"；
- **首次使用需先运行一次 GUI** 完成账号/队伍/秘境/脚本等配置（`publish\BetterGI.exe`），
  并在一条龙页面把「完成后操作」设为 **关闭软件**（这样结束时自动退出，最干净）；
  未设置时 Auto-AFK 仍会通过日志 `一条龙和配置组任务结束` 判定完成并自动关闭进程（`stop_on_finish`，默认宽限 5 秒）；
- 监测：跟踪 `publish\log\better-genshin-impact*.log`（Serilog 按天滚动）；
  `[FTL]`/`UnHandle Exception [FATAL]`/`一条龙任务将被取消` 记为失败，`[ERR]` 记为警告并打包提醒报告；
- 配置分离：`publish\User`、`publish\log` 通过 junction 指向 `configs\BetterGI\`；
- 需要管理员权限（游戏通常以管理员启动），未提权时监管器会拒绝启动。

### 绝区零一条龙（ZenlessZoneZero-OneDragon）
- 直接复用现有源码安装：`E:\GameAFK\ZenlessZoneZero-OneDragon`（`.venv` Python 3.11 + `config\` 配置保留在原目录，不做搬迁）；
- 无人值守入口：`.venv\Scripts\python.exe src\zzz_od\application\zzz_application_launcher.py --close-game`，
  `extra_pythonpath: [src]` 由监管器注入，`--close-game` 让一条龙结束后自行关游戏；
- 监测补丁注入 `src\zzz_od\application\zzz_application_launcher.py`：
  完成时上报 `one dragon finished success=True/False`，失败时进程退出码置 1，
  配合 `require_finish` 与错误模式（运行失败/初始化超时/未注册）判定结果；
- 运行前更新：`git pull` + 子模块 + 补丁自动重打；`pyproject.toml`/`uv.lock` 变化时执行 `uv sync`。

## 运行前自动更新与模拟器管理

**刚启动就自动更新**：Auto-AFK 界面一启动就会在后台自动更新全部项目
（`pre_run.at_startup`，默认开；受"运行前更新"总开关和 `update_projects` 控制），
日志区会显示"启动更新：正在后台更新全部项目..."。
任务链启动时**不再重复更新项目**，只等待启动更新完成（若其仍在进行）；
关闭 `at_startup` 后恢复旧行为（任务链启动前才更新）。

**多个项目同时并行更新**（不再逐个等待）：各项目的更新彼此独立（各自的 git 工作树、
各自的 `.afk_monitor_manifest.json`），因此一次性并行放出去，总耗时从"所有项目之和"
降到"最慢的那个项目"（MaaEnd 的 `build-and-install` 通常是最慢的，过去串行时它会让
其它项目一直干等）。并发度由 `pre_run.update_concurrency` 控制：

```yaml
pre_run:
  update_concurrency: 0    # 0（默认）= 全部项目同时更新；填 N>0 则最多同时 N 个
```

日志会显示 `update: updating 5 project(s) in parallel (concurrency=unlimited)`；
返回结果仍按注册表顺序排列，某个项目更新失败（如 git pull 失败）也不影响其它项目。

每次「一键挂机」或定时启动前，仍会自动执行（可用 GUI 的「运行前更新」开关关闭）：

1. **MuMu 模拟器**：
   - 记录当前版本（`MuMuManager version`），并执行 `MuMuNxUpdater.exe --silence-check` 静默检查（无窗口）；
   - **仅在检测到有新版本时**才打开更新器界面（避免"当前版本无需更新"弹窗）：
     - 判定方式：读取 `nx_updater_config.json` 的 `force`/`reminder`（MuMu 自身就是靠它决定是否提示）并与当前版本比较；
     - `updater_mode: gui`（默认）：有更新才 `--manual-check` 打开界面并等待关闭（超时 600s）；
       `always_open: true` 可恢复"每次都开界面"；
     - `auto`：有更新时 `--do-reminder-update` 无人值守更新；
     - `silence`：只静默检查；`none`：跳过。
   - 更新器关闭后，如果 MuMu 主界面是本次被拉起的，会自动关闭（`close_main_after_update`）。
   - 也可在 GUI 点「更新MuMu」或 `Auto-AFK-CLI.bat update --emulator-gui` 手动打开更新器。
   - 说明：`mumu-cli.exe` 与 `MuMuManager.exe` 是同一个工具（官网文档确认），只支持实例/应用/安卓版本管理，
     **没有客户端更新命令**；客户端升级只能通过 `MuMuNxUpdater.exe`。
2. **模拟器生命周期由 Auto-AFK 统一管理**（BAAH 配置里的 `TARGET_EMULATOR_PATH` 已置空，不再由 BAAH 启动）：
   - BAAH 启动前：先确保 `MuMuNxMain.exe`（主程序）在运行，再用 `MuMuManager control -v 0 launch` 启动实例
     （实测只有主程序在运行时窗口才会显示；失败则回退 `MuMuNxDevice.exe -v 0`）；
   - 等待 Android 就绪并执行 `adb connect 127.0.0.1:16384`；
   - **先显示再最小化**：先 `show_window` + 恢复/置前，停留 `show_before_minimize_sec`（默认 3 秒）让窗口可见，
     再用 `PostMessage(WM_SYSCOMMAND, SC_MINIMIZE)` 缩小到任务栏（Qt 窗口忽略跨进程 `ShowWindow`），
     并启动 90 秒看门狗持续保持最小化（`minimize_window` / `minimize_watchdog_sec` 可配置）；
   - BAAH 结束后：若模拟器是本次启动的，自动 `MuMuManager control -v 0 shutdown`；
     若主程序也是本次拉起的，会一并 `MuMuManager main close` 关闭。
3. **三个项目**：先回滚监测补丁 → `git pull --ff-only` + 子模块更新 → 自动重打补丁；
4. **依赖/构建钩子**（仅当相关文件有变化时触发）：
   - M7A：`uv sync --python 3.12`
   - BAAH：重建 `requirements-core.txt` 并重装 CORE 依赖；自动补齐 `tools/adb`、`tools/aria2`
   - MaaEnd：`uv run setup-workspace`、`pnpm install`、`uv run build-and-install`
5. 手动触发：GUI 工具栏「立即更新」，或 `Auto-AFK-CLI.bat update`（`--no-emulator` / `--no-projects` / `--emulator-gui`）。

> 注意：手动单独运行 BAAH 时，需要先自行启动 MuMu（因为已改为由 Auto-AFK 管理启动）。

## 监测与修复

- **监测**：
  - `sitecustomize.py` 由 `PYTHONPATH` 自动注入，捕获未处理异常、线程异常、logging 输出、心跳；
  - 入口文件的标记补丁（`# >>> AFK-MONITOR:...`）上报任务阶段；
  - **stdout/stderr 实时追踪**：M7A/BAAH 使用自带日志器（不经过标准 logging），监管器直接跟踪其输出并按项目规则匹配；
  - **外部日志追踪**：MaaEnd 通过 `log_files` 跟踪 `install\debug\maafw.log`，识别 `Tasker.Task.Failed` 等信号；
  - 事件写入 `logs\events.jsonl`、`logs\projects\<项目>.log`，GUI 实时展示（支持按项目过滤，切换即重绘）。
- **完成判定**（防止"秒完成"误判）：
  - `require_finish: true`：必须出现 `finish_patterns` 才判定成功；
  - `min_runtime_sec`：运行时间过短且无完成标记 → 失败；
  - 错误模式命中、非零退出码、心跳静默超时 → 失败；
  - 失败原因写入事件与报告（`fail_reason`），错误模式命中时优先显示真实错误文本（而非退出码）。
- **窗口完成判定**（借鉴 [YukinoChan/Anigame-script-manager](https://github.com/yukikino001-ship-it/Anigame-script-manager)）：
  - 项目可配置 `window_keywords`（窗口标题关键词，不区分大小写），监管器每秒枚举可见窗口跟踪游戏窗口；
  - `window_finish: true` 时：窗口出现过、随后**持续消失 `window_absent_sec` 秒**（默认 15，去抖防止启动器重启/自动更新/模拟器闪烁）→ 判定完成；
  - **启动器秒退保护**：进程提前退出但游戏窗口仍在运行时，不立即判失败，继续等待窗口关闭（上限 `window_exit_wait_sec`，默认等于 `hang_timeout`）；
  - `finish_patterns` 仍是主判定，窗口判定是兜底；未配置 `window_keywords` 的项目行为不变。
- **出错即止**：`stop_on_error: true` 的项目（MaaEnd）在错误模式命中后，
  宽限 `stop_on_error_delay_sec` 秒（默认 10）即杀掉进程树并判定失败，不再空等卡死超时。
  MaaEnd 的 `分辨率不符合要求`（非 16:9 会被任务强制停止）已登记为错误模式，
  实测可在数秒内给出真实原因并结束，而不是 15 分钟后误报"卡死"。
- **卡死检测与自动恢复**：
  - 心跳事件不刷新"活跃时间"，只有真实日志/输出才算活跃；
  - `hang_timeout: 900`（15 分钟无任何输出）判定卡死 → 杀掉进程树并清理游戏/模拟器 →
    `hang-detected` 修复规则自动重试一次 → 仍卡死则打包报告（`error tail` 会给出**最后一条日志**，即卡点）并通知；
  - **卡死预警**：无输出达到 `hang_timeout` 的 5/6 时先发预警事件并截图一次（提前留证，再等最后 1/6）；
  - GUI/任务链启动时自动清理上次残留的"运行中"孤儿进程（校验进程工作目录，避免误杀）。
- **软停止**：`stop-chain`（或向事件服务器发 `soft_stop` 命令）让当前组内项目自然跑完，
  不再启动后续组，区别于"全部停止"的直接杀进程；任务链结果记为 `stopped`，不触发关机等后置动作。
- **进程清理**：每次运行结束（成功/失败/停止）自动清理本次运行派生的子进程（模拟器、mxu、go-service、cpp-algo、云游戏客户端等），
  另可在项目配置中用 `cleanup_processes` 按进程名补充清理。
- **修复**：`configs\core\repair_rules.yaml` 白名单规则（正则 → 动作），默认自动执行安全动作：
  补装缺失模块、更新子模块、重启 adb、还原配置、网络重试等；不安全动作（重建 MaaEnd 等）需手动确认。
- **报错/无法修复**：自动在 `logs\reports\<时间>_<项目>\` 打包 `report.md`、事件切片、stdout/stderr 尾部、
  配置快照（脱敏）、桌面截图、修复尝试记录，并写入 `logs\errors.jsonl` 索引；
  `report_on_warn: true` 的项目（MaaEnd）完成任务但存在任务级警告时也会打包报告；
  **同签名去重**：同一错误签名在 `report_dedupe_hours`（默认 20 小时）内重复出现时复用已有报告目录，
  避免每天都产生内容相同的报告（报告 `report.md` 的 last events 已过滤心跳空行）。

## 配置分离

- 目录型配置通过 junction 链接到 `configs\<项目>\`，升级源码不受影响；
- `config.yaml` 等单文件在启动时从配置中心拷入、结束时拷回，并在 `backups\` 留档；
- 首次运行 `50_verify.ps1` 会从现有发行版目录（`E:\GameAFK\<项目>`）导入已有配置。

## 日志维护

启动 GUI **或**执行会真正产生新日志的 CLI 命令（`run` / `start` / `update`）时会自动执行一次清理
（可在 `configs\core\settings.yaml` 的 `housekeeping` 中调整）：

- 删除 `log_days`（默认 14）天前的单次运行日志、卡死截图与 spool 导入文件；
- 每个项目的滚动日志或 `events.jsonl` 过大时自动截断保留尾部
  （`events_max_mb` 默认 50，超阈值截半；截断总是落在整行边界，不会拦腰截断 JSON）；
- 删除 `report_days`（默认 30）天前的错误报告。

> 过去只有「启动 GUI」才清理，长期只用 CLI 时 `events.jsonl` 会持续增长、阈值永不生效；
> 现在 CLI 也会清理。纯查询命令（`status` / `stats` / `reports` / `idle`）**不触发**清理，保持轻量。

## 测试

`tests\` 下是可直接用中心 venv 运行的冒烟/回归测试（会使用 `tests\` 内的独立注册表，不触碰正式配置）：

```powershell
& .venv\Scripts\python.exe tests\smoke_classify.py    # 完成/失败判定
& .venv\Scripts\python.exe tests\smoke_hang_flow.py   # 卡死 -> 修复 -> 重试 -> 报告
& .venv\Scripts\python.exe tests\smoke_stop_error.py  # 出错即止（stop_on_error）
& .venv\Scripts\python.exe tests\smoke_window.py      # 窗口完成判定 + 启动器秒退保护
& .venv\Scripts\python.exe tests\smoke_softstop.py    # 软停止（跑完当前组即停）
& .venv\Scripts\python.exe tests\smoke_cli_remote.py  # CLI 远程 stop/status
& .venv\Scripts\python.exe tests\smoke_guiopen.py     # 打开项目 GUI（模拟入口，无窗口）
& .venv\Scripts\python.exe tests\smoke_guiopen_real.py  # 实测打开 BAAH 编辑器（会闪现窗口）
& .venv\Scripts\python.exe tests\smoke_gui.py         # GUI 离屏冒烟
& .venv\Scripts\python.exe tests\smoke_retry_round.py # 链级容错：失败跳过 -> 末尾重跑 -> 单项目最多 3 次
& .venv\Scripts\python.exe tests\smoke_update_parallel.py  # 项目并行更新（并发度、顺序、失败隔离、停止）
& .venv\Scripts\python.exe tests\smoke_completion.py  # 今日已完成则跳过空闲自动执行（含跨天失效）
```

空闲检测 / 桌面小组件 / 开机自启 / 线程安全 / 新外壳的测试需离屏运行：

```powershell
$env:QT_QPA_PLATFORM="offscreen"; $env:QT_QPA_FONTDIR="C:\Windows\Fonts"
& .venv\Scripts\python.exe tests\smoke_idle.py       # 空闲阈值/武装冷却、三选一提示框、自启、background 组、小组件
& .venv\Scripts\python.exe tests\smoke_idle_flow.py  # 空闲到点 -> 提示框 -> 启动任务（含 --tray 开机自启模式）
& .venv\Scripts\python.exe tests\smoke_thread_qt.py  # 后台线程经 Signal 投递到 GUI 线程，不直接碰控件
& .venv\Scripts\python.exe tests\smoke_shell_ui.py   # 新外壳（导航栏/设置页/嵌入页）
& .venv\Scripts\python.exe tests\smoke_modern_ui.py  # 主题与动效组件（按钮/滚动/Tab/状态点/胶囊）
```

其余可运行的冒烟测试（用中心 venv 直接跑）：

```powershell
& .venv\Scripts\python.exe tests\smoke_supervisor.py      # Supervisor 基础编排
& .venv\Scripts\python.exe tests\smoke_chain.py           # 并行组 + 独占组的分组编排
& .venv\Scripts\python.exe tests\smoke_sequential.py      # 组内串行执行顺序
& .venv\Scripts\python.exe tests\smoke_startup_update.py  # 启动时更新项目 + 链跳过重复更新
& .venv\Scripts\python.exe tests\smoke_cleanup.py         # 进程清理
& .venv\Scripts\python.exe tests\smoke_bus.py             # 事件总线与监测注入（含夹具 smoke_monitor.py）
& .venv\Scripts\python.exe tests\smoke_emulator.py        # 模拟器管理
& .venv\Scripts\python.exe tests\smoke_gui_log.py         # GUI 日志区
& .venv\Scripts\python.exe tests\smoke_hang.py            # 卡死判定
& .venv\Scripts\python.exe tests\smoke_close_exit.py      # 完成后退出
```

> `tests\smoke_baah.py` 需在 BAAH 自己的 venv 里跑（依赖 cv2）；
> `tests\smoke_guiopen_real.py` / `smoke_embed_real.py` 会真实打开窗口，仅在需要时手动跑。
> `tests\smoke_isolation.py` 是隔离工具（被其它测试 import），不是独立测试。

> ⚠️ 会运行任务链的测试必须调用 `tests/smoke_isolation.py` 的 `isolate_runtime()`：
> 链成功会写 `logs\last_chain_success.json`（今日已完成），写进真实目录会让用户当天
> 的空闲自动挂机被误跳过。另外**等待链结束时必须用 `smoke_isolation.wait_chain_idle()`**
> 而不是只看 `chain_status`：状态变终态的那一刻链线程仍在收尾写标记，若此时就
> `restore()` 隔离目录，标记会落到真实 logs（实测 5 次里出现 2 次）。
>
> 相应地，`Supervisor` 已保证**「公布 chain_status=success」时完成标记必定已写好**
> （写标记在前、公布状态在后），`smoke_completion.py` 用 1ms 高频轮询锁住这个不变量。


## 更新

```powershell
cd E:\GameAFK\Auto-AFK\setup
powershell -ExecutionPolicy Bypass -File 90_update.ps1
# MaaEnd 源码有更新时再执行：
powershell -ExecutionPolicy Bypass -File 32_build_maaend.ps1
powershell -ExecutionPolicy Bypass -File 40_apply_patches.ps1
```

## 新增挂机项目（预留位）

1. 克隆或放置新项目到 `Auto-AFK\` 下；
2. GUI「项目注册表」页签或直接编辑 `configs\core\afk_projects.yaml`：
   ```yaml
   projects:
     新项目名:
       display_name: 显示名称
       launch_type: python        # 或 exe
       path: 新项目目录
       python: .venv\Scripts\python.exe
       args: [main.py]
       retry: 1
       success_patterns: ["完成"]
       error_patterns: ["error", "Traceback"]
   ```
3. 把项目名加入 `groups` 的成员列表（并行组或独占组）；
   组上可加 `background: true` —— 表示该组**不占用鼠标/键盘**，会被空闲自动挂机的
   「仅运行不占用鼠标任务」选中（默认只有云游戏 + 模拟器组是 `background: true`）；
4. 如需精细监测，在 `monitor_kit\patcher.py` 的 `PATCHES` 中登记补丁块；
5. 可选窗口监测字段（借鉴 YukinoChan）：`window_keywords`（游戏窗口标题关键词）、
   `window_finish`（窗口持续消失即完成）、`window_absent_sec`（去抖时长，默认 15）、
   `window_exit_wait_sec`（启动器秒退后等待窗口关闭的上限）。

## 注意事项

- MaaEnd 构建建议在 `configs\core\github_token.txt` 提供有效 GitHub Token（下载 CI artifact 必需）；
  系统中无效的 `GH_TOKEN` 会被构建脚本忽略。
- MaaEnd 首次运行需在 MXU 图形界面配置控制器（Win32-Front）与实例名，并把实例名填到注册表 `instance` 字段。
- March7thAssistant 控制游戏需要管理员权限，建议以管理员身份启动 GUI/CLI。
- 三个项目都要控制游戏画面，本编排默认"云游戏+ADB 并行、前台独占串行"，避免输入冲突。
