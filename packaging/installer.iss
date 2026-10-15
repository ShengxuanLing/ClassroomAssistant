; TASK-110 — 课堂助手 (Classroom Assistant) Windows 安装脚本
;
; 编译:  ISCC.exe packaging\installer.iss
; 或由  python packaging\build.py  传入版本号: ISCC.exe /DAppVersion=x.y.z ...
;
; 设计要点
; --------------------------------------------------------------------
; - PrivilegesRequired=lowest + {localappdata}\Programs: 装到用户目录,
;   不需要管理员、不弹 UAC;
; - 用户数据在 %LOCALAPPDATA%\ClassroomAssistant\classroom-data (由程序
;   冻结态自己解析, 见 src/application/config.py::installed_data_root),
;   与安装目录分离 —— 卸载**不删数据**, 这是有意设计;
; - AppId 是固定的 GUID: 升级安装时 Inno 据此识别"同一个程序", 改了
;   就等于变成另一个产品, 未来升级会变成并存两份。

#ifndef AppVersion
  ; 回退值必须与 src/application/workspace.py 的 APPLICATION_VERSION 一致
  ; (tests/test_packaging.py 钉住, 漂移会红)。
  #define AppVersion "1.1.1"
#endif

#define AppName "课堂助手"
#define AppFullName "课堂助手 (Classroom Assistant)"
#define AppExeName "ClassroomAssistant.exe"

[Setup]
AppId={{A0642C37-D798-40EF-A730-8F3D6A1AD48B}
AppName={#AppFullName}
AppVersion={#AppVersion}
AppVerName={#AppFullName} {#AppVersion}
AppPublisher=Classroom Assistant
DefaultDirName={localappdata}\Programs\ClassroomAssistant
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=ClassroomAssistant-{#AppVersion}-Setup
SetupIconFile=..\src\application\assets\app.ico
UninstallDisplayName={#AppFullName}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; onedir 产物整体铺进安装目录 (exe + _internal)。
Source: "..\dist\ClassroomAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppFullName}}"; Flags: nowait postinstall skipifsilent
