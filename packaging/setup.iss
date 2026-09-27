; ============================================================
;   setup.iss - 星尘闪连 (Stardust Flash Link) - Dr.COM 校园网自动登录 Inno Setup 6 脚本
;   版本: v2.0.6.0
;   编码: UTF-8 + BOM（ISCC 推荐 UTF-8 BOM）
;   目标: 生成 StardustFlashLink-Setup-v2.0.6.0.exe
; ============================================================

#define MyAppName "星尘闪连 (Stardust Flash Link)"
; 允许 CI 用 ISCC /DMyAppVersion=x.y 覆盖；本地直接编译时用下面的默认值
#ifndef MyAppVersion
  #define MyAppVersion "2.0.6.0"
#endif
; 版本线代号（MAJOR.MINOR 级别，规则见 docs/VERSIONING.md）
#ifndef MyAppCodename
  #define MyAppCodename "Sirius"
#endif
#define MyAppPublisher "星尘闪连"
#define MyAppExeName "联网_service.py"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion} "{#MyAppCodename}"
AppPublisher={#MyAppPublisher}
LicenseFile=branding\EULA.rtf
DefaultDirName={autopf}\DrcomAutoLogin
DisableProgramGroupPage=yes
PrivilegesRequired=admin
AppMutex=DrcomAutoLogin-mutex-v2
AppId={{A8F2E3D1-7C4B-4F89-9D5E-1A2B3C4D5E6F}
OutputBaseFilename=StardustFlashLink-Setup-v{#MyAppVersion}
OutputDir=output
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\branding\app.ico
SetupIconFile=branding\app.ico
; —— 向导外观（与 _ui_redesign\flashlink-mock.iss 上验证过的参数一致）——
; 多尺寸 PNG：Inno 6 会按当前 DPI 自动挑最合适的一张，2K/4K 屏不再糊；
; 此前只写了一张 wizard.bmp（v2.0.4.3 及以前），在高 DPI 下是放大插值的。
WizardStyle=modern dark includetitlebar hidebevels
WizardSizePercent=110
WizardImageFile=branding\wizard-left-202x386.png,branding\wizard-left-269x515.png,branding\wizard-left-336x643.png,branding\wizard-left-403x772.png,branding\wizard-left-430x824.png
WizardSmallImageFile=branding\wizard-small-58x58.png,branding\wizard-small-77x77.png,branding\wizard-small-97x97.png,branding\wizard-small-116x116.png,branding\wizard-small-124x124.png
; 带欢迎页（左侧大品牌图那一页）—— Inno 6 默认 DisableWelcomePage=yes，是没有这一页的
DisableWelcomePage=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

; 文案与 mock 保持一致（WelcomeLabel2 只在 DisableWelcomePage=no 时显示）
[Messages]
WelcomeLabel1=欢迎安装 [name]
WelcomeLabel2=即将在你的电脑上安装 [name/ver]。%n%n本程序会在后台守护校园网连接，掉线自动重新认证。%n%n继续前请先阅读使用许可。
FinishedLabel=[name] 已安装完成。%n%n服务会在后台自动运行，双击桌面上的「Dr.COM 校园网自动登录」即可打开控制台。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "startservice"; Description: "安装完成后立即启动服务"; GroupDescription: "附加任务:"
; v2.0.6.0 新增：登录会话里的托盘小程序（断线通知 + 状态图标）
; 注意：服务在 session 0（LocalSystem）里弹不出任何通知，必须由用户会话里的进程来做。
Name: "trayicon"; Description: "开机自动启动托盘（断线通知 / 状态图标）"; GroupDescription: "附加任务:"

[Files]
Source: "..\联网_service.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\version.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\protocol.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\eula.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\web_api.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\auto_update.py"; DestDir: "{app}"; Flags: ignoreversion
; 托盘小程序（v2.0.6.0）：随登录启动，轮询本机 /api/status 弹断线通知
Source: "..\tray.py"; DestDir: "{app}"; Flags: ignoreversion
; Web UI 品牌图片：服务端 /branding/* 静态路由从这里读取（顶栏 logo + favicon）
Source: "branding\web-logo-*.png"; DestDir: "{app}\branding"; Flags: ignoreversion
; 品牌图标：桌面/开始菜单快捷方式的图标从这里取
; （v2.0.4.0 之前 [Icons] 写的是 {sys}\shell32.dll,13 的 Windows 通用图标，桌面上是个灰扑扑的默认图标）
Source: "branding\app.ico"; DestDir: "{app}\branding"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; 更新日志：Web UI「关于 → 查看更新日志」直接读 {app}\CHANGELOG.md。
; v2.0.4.0 补打包 —— 之前没随包拷贝，弹窗必然报 "No such file or directory"。
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.json.template"; DestDir: "{app}"; DestName: "config.json"; Flags: ignoreversion onlyifdoesntexist
Source: "password.txt.template"; DestDir: "{app}"; DestName: "password.txt"; Flags: ignoreversion onlyifdoesntexist
Source: "..\tools\nssm.exe"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\logs"

[Icons]
Name: "{group}\Dr.COM 校园网自动登录"; Filename: "{app}\启动UI.bat"; IconFilename: "{app}\branding\app.ico"; IconIndex: 0
Name: "{group}\查看日志"; Filename: "{app}\logs"
Name: "{group}\卸载 Dr.COM 校园网自动登录"; Filename: "{uninstallexe}"
Name: "{commondesktop}\Dr.COM 校园网自动登录"; Filename: "{app}\启动UI.bat"; Tasks: desktopicon; IconFilename: "{app}\branding\app.ico"; IconIndex: 0

[Registry]
; v2.0.6.0：托盘随登录启动（HKCU，只对装它的那个用户生效）。
; uninsdeletevalue → 卸载时自动清掉；Tasks: trayicon → 和安装向导里的勾选联动。
; 用 pythonw.exe（无控制台窗口），不弹黑框。
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "DrcomAutoLoginTray"; \
  ValueData: """{app}\python\pythonw.exe"" ""{app}\tray.py"""; \
  Flags: uninsdeletevalue; Tasks: trayicon

[Run]
; 装完（非静默）可以顺手把托盘起起来；skipifsilent → 自动升级的静默安装不会在这里起进程
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\tray.py"""; \
  Description: "立即启动托盘（断线通知 / 状态图标）"; \
  Flags: nowait postinstall skipifsilent; Tasks: trayicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\branding"

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c ""{app}\tools\nssm.exe"" stop DrcomAutoLogin"; Flags: runhidden; RunOnceId: "StopDrcomAutoLogin"
Filename: "{cmd}"; Parameters: "/c ""{app}\tools\nssm.exe"" remove DrcomAutoLogin confirm"; Flags: runhidden; RunOnceId: "RemoveDrcomAutoLogin"

[Code]

const
  // WaitServiceStopped 轮询间隔（毫秒）；也用作超时计算的除数
  PollIntervalMs = 500;

// ============================================================
//   GetPythonPath - 探测可用的 Python 解释器
//   优先返回安装包内嵌的 Python 运行时（{app}\python\python.exe）；
//   内嵌运行时不存在时才回退到注册表 / 常见安装路径。
//   返回完整路径（含文件名），失败返回空字符串
// ============================================================
function GetPythonPath(): string;
var
  RegValue: string;
  Embedded: string;
begin
  // 0. 优先：安装包内嵌 Python 运行时（目标机无需预装 Python）
  Embedded := ExpandConstant('{app}\python\python.exe');
  if FileExists(Embedded) then begin Result := Embedded; Exit; end;
  // 1. 注册表 HKLM
  if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\Python\PythonCore\3.14\InstallPath', '', RegValue) then
  begin
    Result := AddBackslash(RegValue) + 'python.exe';
    if FileExists(Result) then Exit;
  end;
  // 2. 注册表 HKCU
  if RegQueryStringValue(HKEY_CURRENT_USER, 'SOFTWARE\Python\PythonCore\3.14\InstallPath', '', RegValue) then
  begin
    Result := AddBackslash(RegValue) + 'python.exe';
    if FileExists(Result) then Exit;
  end;
  // 3. 常见路径 fallback
  if FileExists('C:\Python314\python.exe') then begin Result := 'C:\Python314\python.exe'; Exit; end;
  if FileExists('C:\Program Files\Python314\python.exe') then begin Result := 'C:\Program Files\Python314\python.exe'; Exit; end;
  if FileExists('C:\Program Files (x86)\Python314\python.exe') then begin Result := 'C:\Program Files (x86)\Python314\python.exe'; Exit; end;
  // 4. 找不到返回空
  Result := '';
end;

// ============================================================
//   InitializeSetup - 安装前检查
//   安装包已内嵌 Python 运行时（{app}\python\python.exe），
//   目标机无需预装 Python，因此这里不做阻断性检查、也不弹任何提示。
//   「找不到解释器」的兜底报错已移到 RegisterService()（真正需要时再报）。
// ============================================================
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

// ============================================================
//   CreateLauncherBat - 生成启动 Web UI 的 bat 文件
// ============================================================
procedure CreateLauncherBat();
var
  LauncherPath: string;
  Content: string;
begin
  LauncherPath := ExpandConstant('{app}\启动UI.bat');
  Content :=
    '@echo off' + #13#10 +
    'start "" "http://127.0.0.1:8848"' + #13#10 +
    'exit';
  SaveStringToFile(LauncherPath, Content, False);
end;

// ============================================================
//   CreateURLFile - 写 .url Internet 快捷方式
//   内容纯 ASCII，无需考虑编码
// ============================================================
procedure CreateURLFile(const FilePath, URL: string);
var
  Content: AnsiString;
begin
  // 图标一并写成品牌图，别再让开始菜单显示浏览器的默认图标
  // （.url 的这个字段按 ANSI 写盘：安装路径若含中文，这行会失效，但快捷方式本身仍然可用）
  Content := '[InternetShortcut]' + #13#10 + 'URL=' + URL + #13#10 +
             'IconFile=' + ExpandConstant('{app}\branding\app.ico') + #13#10 +
             'IconIndex=0';
  SaveStringToFile(FilePath, Content, False);
end;

// ============================================================
//   CreateStartMenuShortcuts - 创建桌面/开始菜单 .url 快捷方式
// ============================================================
procedure CreateStartMenuShortcuts();
var
  DesktopPath: string;
  StartMenuPath: string;
begin
  DesktopPath := ExpandConstant('{userdesktop}');
  StartMenuPath := ExpandConstant('{userstartmenu}') + '\Programs';
  if WizardIsTaskSelected('desktopicon') then
  begin
    CreateURLFile(DesktopPath + '\Dr.COM 校园网自动登录.url', 'http://127.0.0.1:8848');
  end;
  CreateURLFile(StartMenuPath + '\Dr.COM 校园网自动登录.url', 'http://127.0.0.1:8848');
end;

// ============================================================
//   RegisterService - 通过 NSSM 注册 Windows 服务
// ============================================================
procedure RegisterService();
var
  PythonPath: string;
  AppDir: string;
  ScriptPath: string;
  NSSM: string;
  ResultCode: Integer;
begin
  AppDir := ExpandConstant('{app}');
  PythonPath := GetPythonPath();
  ScriptPath := AppDir + '\联网_service.py';
  NSSM := AppDir + '\tools\nssm.exe';

  // 兜底报错：内嵌 Python 缺失（正常安装包不会走到这里）
  if PythonPath = '' then
  begin
    MsgBox('未找到可用的 Python 解释器。' + #13#10 + #13#10 +
           '安装包应自带内嵌 Python：' + AppDir + '\python\python.exe' + #13#10 +
           '若该文件缺失，说明安装包不完整，请重新下载安装包。' + #13#10 + #13#10 +
           '（若确实想用系统 Python，请安装 Python 3，例如 C:\Python314\）',
           mbError, MB_OK);
    Exit;
  end;

  // 幂等：先停再删
  Exec(NSSM, 'stop DrcomAutoLogin', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'remove DrcomAutoLogin confirm', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  // 注册
  // 只传 Application，AppParameters 随后用注册表直写（值含引号字符）。
  // 为什么不走 nssm 命令行传参：nssm 会把 AppParameters 原样拼在 Application 之后，
  // 安装路径含空格时（如 D:\Program Files\...）Windows 会在空格处劈开参数，
  // Python 只会收到 "D:\Program"，服务反复启动失败。
  Exec(NSSM, 'install DrcomAutoLogin "' + PythonPath + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if not RegWriteStringValue(HKEY_LOCAL_MACHINE,
                             'SYSTEM\CurrentControlSet\Services\DrcomAutoLogin\Parameters',
                             'AppParameters',
                             '"' + ScriptPath + '"') then
  begin
    MsgBox('注册服务失败：无法写入服务参数 AppParameters。' + #13#10 + #13#10 +
           '注册表项：HKLM\SYSTEM\CurrentControlSet\Services\DrcomAutoLogin\Parameters' + #13#10 + #13#10 +
           '请确认以管理员身份运行安装程序后重试。', mbError, MB_OK);
  end;
  Exec(NSSM, 'set DrcomAutoLogin AppDirectory "' + AppDir + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'set DrcomAutoLogin DisplayName "Dr.COM 校园网自动登录"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'set DrcomAutoLogin Description "星尘闪连 (Stardust Flash Link) - Dr.COM 校园网自动登录（v2.0.6.0）"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'set DrcomAutoLogin Start SERVICE_AUTO_START', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'set DrcomAutoLogin AppStdout "' + AppDir + '\logs\service_stdout.log"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'set DrcomAutoLogin AppStderr "' + AppDir + '\logs\service_stderr.log"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'set DrcomAutoLogin AppRotateFiles 1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'set DrcomAutoLogin AppRotateBytes 1048576', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'set DrcomAutoLogin AppExit Default Ignore', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NSSM, 'set DrcomAutoLogin AppExit 0 Ignore', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  // 启动（仅当用户在 wizard 勾选）
  if IsTaskSelected('startservice') then
  begin
    Exec(NSSM, 'start DrcomAutoLogin', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

// ============================================================
//   WaitServiceStopped - 轮询等待服务进入 SERVICE_STOPPED 状态
//
//   用途：在 CurStepChanged(ssInstall) 调 nssm stop 之后再调，
//         最长等待 TimeoutMs 毫秒。超时未停会弹 MsgBox 提示用户。
//
//   实现说明：Inno Setup 6 没有 QueryServiceStatus / GetTickCount builtin，
//             因此用 nssm stop 的退出码做轮询：
//             - 服务已停止（ERROR_SERVICE_NOT_ACTIVE）→ nssm 退出码 0
//             - 服务仍在停止中或调用失败                  → nssm 退出码 1
//             每隔 500ms 再调一次 nssm stop，直到它返回 0 或超时。
//             单次 nssm stop 内部最多轮询 ~2.75s（10 次递增 sleep）。
//             超时用迭代次数（TimeoutMs ÷ 500ms）控制，避免依赖 GetTickCount。
// ============================================================
procedure WaitServiceStopped(const SvcName: string; TimeoutMs: Integer);
var
  NSSM: string;
  Iterations: Integer;
  ResultCode: Integer;
begin
  NSSM := ExpandConstant('{app}') + '\tools\nssm.exe';
  Iterations := TimeoutMs div PollIntervalMs;
  while Iterations > 0 do
  begin
    Exec(NSSM, 'stop ' + SvcName, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode = 0 then Exit; // 已停止
    Sleep(PollIntervalMs);
    Iterations := Iterations - 1;
  end;
  // 超时未停：交互安装时提示用户；**静默安装绝不弹窗** ——
  // 自动升级场景下安装器跑在服务会话里（无人在场），MsgBox 会把安装
  // 永久挂起，导致升级反复"启动成功但装不上"（v2.0.4.0 修）。
  if WizardSilent then
    Log('WARN: 服务未在 30 秒内停止，继续安装（静默模式不提示）')
  else
    MsgBox('服务未在 30 秒内停止，安装可能失败。建议先手动停止服务再重试安装。',
           mbError, MB_OK);
end;

// ============================================================
//   CurStepChanged - 安装过程中分阶段钩子
//
//   - ssInstall   : 升级场景先把 DrcomAutoLogin 服务停掉再让 Inno 复制新文件，
//                   避免旧 Python 进程仍持有 联网_service.py 句柄导致复制失败
//                   或旧版继续跑（Web UI / 登录逻辑没升级）。全新安装场景下
//                   服务不存在，跳过整个分支。
//   - ssPostInstall: 注册服务、生成启动器、创建快捷方式（原有逻辑不变）。
// ============================================================
procedure CurStepChanged(CurStep: TSetupStep);
var
  NSSM: string;
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    if RegKeyExists(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\DrcomAutoLogin') then
    begin
      // 调 nssm stop 请求 SCM 停止服务（首次调用内部最长等约 2.75s）
      NSSM := ExpandConstant('{app}') + '\tools\nssm.exe';
      Exec(NSSM, 'stop DrcomAutoLogin', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      // 轮询直到 STOPPED 或 30 秒超时（超时未停则弹 MsgBox 提示但继续安装）
      WaitServiceStopped('DrcomAutoLogin', 30000);
    end;
  end
  else if CurStep = ssPostInstall then
  begin
    CreateLauncherBat();
    RegisterService();
    CreateStartMenuShortcuts();
  end;
end;
