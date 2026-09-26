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
  - **现代化深色界面**：圆角卡片 + 项目色条、状态圆点与状态胶囊、主/次按钮层级、标签页下划线指示、
    深色等宽日志区；实时日志按**对象着色**（每个项目一种颜色，error 红、warn 橙保持不变），
    颜色可在 `configs\core\settings.yaml` 的 `gui.project_colors` 中自定义。
- **一键自动挂机快捷方式（推荐）**：`Auto-AFK 自动挂机.lnk`（根目录与桌面各一份，另有"(管理员)"版本），
  点击后无任何 cmd 窗口，直接启动 GUI 并**自动开始一键挂机**（`--autorun`）；管理员版会弹一次 UAC。
  - 重新生成：`.venv\Scripts\python.exe setup\create_shortcuts.py`（会同时生成 `gui\afk.ico` 图标）
  - 可选参数：`--tray` 启动即最小化到托盘；`--autorun` 自动开跑
    （`tools\launch_gui.vbs` 会原样转发附加参数；自动挂机快捷方式已带 `--autorun`，`Auto-AFK.bat` 不带参数打开纯界面）
  - 演练模式：`AFK_DRY_RUN=1` 时"一键挂机"只记录日志不真正启动项目
- **管理员模式（推荐，M7A/BAAH 控制游戏需要）**：使用桌面的 `Auto-AFK 自动挂机(管理员).lnk`，或双击 `Auto-AFK-Admin.bat`。
  未提权时监管器会直接拒绝启动 `require_admin` 的项目并给出提示，避免 M7A 自我提权导致监管失联。
- **无 cmd 窗口**：启动器走 `tools\launch_gui.vbs`（wscript 静默）；监管器、更新器、MuMu 管理、doctor、
  修复动作等所有子进程都加了隐藏窗口标志（`afk_core/proc.py`），运行过程中不会闪出控制台。
  只有 `Auto-AFK-CLI.bat` 保留控制台（命令行交互需要）。
- 命令行：
  - `Auto-AFK-CLI.bat run`：一键挂机（先并行 M7A+BAAH，完成后自动跑 MaaEnd）
  - `Auto-AFK-CLI.bat start March7thAssistant`
  - `Auto-AFK-CLI.bat stop BAAH`
  - `Auto-AFK-CLI.bat status --json`
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
组内任一项目失败默认终止整个链条；在组上设置 `continue_on_failure: true` 可跳过失败组继续执行后续组。
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

每次「一键挂机」或定时启动前，会自动执行（可用 GUI 的「运行前更新」开关关闭）：

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
- **出错即止**：`stop_on_error: true` 的项目（MaaEnd）在错误模式命中后，
  宽限 `stop_on_error_delay_sec` 秒（默认 10）即杀掉进程树并判定失败，不再空等卡死超时。
  MaaEnd 的 `分辨率不符合要求`（非 16:9 会被任务强制停止）已登记为错误模式，
  实测可在数秒内给出真实原因并结束，而不是 15 分钟后误报"卡死"。
- **卡死检测与自动恢复**：
  - 心跳事件不刷新"活跃时间"，只有真实日志/输出才算活跃；
  - `hang_timeout: 900`（15 分钟无任何输出）判定卡死 → 杀掉进程树并清理游戏/模拟器 →
    `hang-detected` 修复规则自动重试一次 → 仍卡死则打包报告（`error tail` 会给出**最后一条日志**，即卡点）并通知；
  - GUI/任务链启动时自动清理上次残留的"运行中"孤儿进程（校验进程工作目录，避免误杀）。
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

每次启动 GUI 时会自动执行一次清理（可在 `configs\core\settings.yaml` 的 `housekeeping` 中调整）：

- 删除 `log_days`（默认 14）天前的单次运行日志、卡死截图与 spool 导入文件；
- 每个项目的滚动日志或 `events.jsonl` 过大时自动截断保留尾部；
- 删除 `report_days`（默认 30）天前的错误报告。

## 测试

`tests\` 下是可直接用中心 venv 运行的冒烟/回归测试（会使用 `tests\` 内的独立注册表，不触碰正式配置）：

```powershell
& .venv\Scripts\python.exe tests\smoke_classify.py    # 完成/失败判定
& .venv\Scripts\python.exe tests\smoke_hang_flow.py   # 卡死 -> 修复 -> 重试 -> 报告
& .venv\Scripts\python.exe tests\smoke_stop_error.py  # 出错即止（stop_on_error）
& .venv\Scripts\python.exe tests\smoke_cli_remote.py  # CLI 远程 stop/status
& .venv\Scripts\python.exe tests\smoke_gui.py         # GUI 离屏冒烟
```

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
4. 如需精细监测，在 `monitor_kit\patcher.py` 的 `PATCHES` 中登记补丁块。

## 注意事项

- MaaEnd 构建建议在 `configs\core\github_token.txt` 提供有效 GitHub Token（下载 CI artifact 必需）；
  系统中无效的 `GH_TOKEN` 会被构建脚本忽略。
- MaaEnd 首次运行需在 MXU 图形界面配置控制器（Win32-Front）与实例名，并把实例名填到注册表 `instance` 字段。
- March7thAssistant 控制游戏需要管理员权限，建议以管理员身份启动 GUI/CLI。
- 三个项目都要控制游戏画面，本编排默认"云游戏+ADB 并行、前台独占串行"，避免输入冲突。
