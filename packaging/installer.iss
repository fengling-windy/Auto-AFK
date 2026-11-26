#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif

#define AppName "Auto-AFK"
#define AppPublisher "fengling-windy"
#define AppURL "https://github.com/fengling-windy/Auto-AFK"
#define AppExeName "Auto-AFK.exe"

[Setup]
AppId={{8E2D7D3F-5A18-4D9E-B1F6-4F92C19E7B20}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE
OutputDir=output
OutputBaseFilename=Auto-AFK-Setup-v{#AppVersion}
SetupIconFile=..\gui\afk.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut (Auto-AFK)"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "desktopadmin"; Description: "Create an administrator desktop shortcut (Auto-AFK Admin)"; GroupDescription: "Additional icons:"; Flags: unchecked

[Dirs]
Name: "{app}\logs"
Name: "{app}\configs\core"

[Files]
; PyInstaller 主程序目录
Source: "..\dist\Auto-AFK\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 开源协议与文档
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

; 默认核心配置：升级安装时不覆盖用户已有配置
Source: "..\configs\core\afk_projects.yaml"; DestDir: "{app}\configs\core"; Flags: onlyifdoesntexist
Source: "..\configs\core\repair_rules.yaml"; DestDir: "{app}\configs\core"; Flags: onlyifdoesntexist
Source: "..\configs\core\settings.yaml"; DestDir: "{app}\configs\core"; Flags: onlyifdoesntexist

; 监控注入套件、脚本与批处理入口
Source: "..\monitor_kit\*"; DestDir: "{app}\monitor_kit"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc"
Source: "..\setup\*"; DestDir: "{app}\setup"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc"
Source: "..\tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc"
Source: "..\Auto-AFK.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Auto-AFK-Admin.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Auto-AFK-CLI.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\一键挂机.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Auto-AFK"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Auto-AFK (Admin)"; Filename: "wscript.exe"; Parameters: """{app}\tools\launch_admin.vbs"""; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\Auto-AFK (Auto-Run Admin)"; Filename: "wscript.exe"; Parameters: """{app}\tools\launch_admin_autorun.vbs"""; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall Auto-AFK"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Auto-AFK"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{autodesktop}\Auto-AFK (Admin)"; Filename: "wscript.exe"; Parameters: """{app}\tools\launch_admin.vbs"""; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopadmin

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch Auto-AFK"; Flags: nowait postinstall skipifsilent
