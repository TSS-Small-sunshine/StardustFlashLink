@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ============================================================
REM   uninstall.bat - 移除 星尘闪连 (Stardust Flash Link) 服务 (v2.1.3.16)
REM   编码: UTF-8（无 BOM）+ 首行 chcp 65001 —— cmd 才能正确显示中文。
REM   ⚠️ 不要给本文件加 BOM：BOM 会变成第一行的字节，首行 @echo off 成为乱码命令，
REM      cmd 会先报错或回显命令行，脚本行为全乱（实测过，不要加）。
REM   ⚠️ 已知限制（v2.1.0.0 记录，未修）：脚本用 setlocal EnableDelayedExpansion（需要 !VAR!），
REM      因此**安装路径里不能含 `!`**（会被当成变量展开吃掉）。请放在不含 `!` 的目录下。
REM ============================================================

REM 把脚本自身路径放到环境变量，供 PowerShell 读取
set "INSTALL_BAT_PATH=%~f0"

REM ============================================================
REM   只读预览：--check / /check
REM   必须在「自提升管理员」之前识别 —— 预览不要求管理员、不弹 UAC，
REM   只做 sc query / reg query / PowerShell Test-Path 这类只读查询，
REM   不停止服务、不删注册表、不删文件（任何破坏性操作都不做）。
REM ============================================================
if /I "%~1"=="--check" goto :check_only
if /I "%~1"=="/check" goto :check_only
goto :elevate

REM ============================================================
REM   只读预览分支：只报告「如果现在卸载会做 / 会删什么」，不做任何修改
REM ============================================================
:check_only
set "CHECK_DIR=%~dp0"
if "!CHECK_DIR:~-1!"=="\" set "CHECK_DIR=!CHECK_DIR:~0,-1!"
set "CHECK_NSSM=!CHECK_DIR!\tools\nssm.exe"
set "CHECK_LOGS=!CHECK_DIR!\logs"
set "CHECK_BACKUP=!CHECK_DIR!\backup"

echo.
echo ============================================================
echo   星尘闪连 (Stardust Flash Link) 卸载 - 只读预览 (v2.1.3.16)
echo   本预览不需要管理员权限，也不会修改任何东西
echo ============================================================
echo   脚本目录: !CHECK_DIR!
echo ============================================================
echo.
echo   如果现在卸载，本脚本（不带参数运行）会做这些事：
echo     1. 停止并移除 Windows 服务 DrcomAutoLogin（优先用 NSSM，失败退回系统命令）
echo     2. 清掉托盘自启项和托盘偏好注册表键（存在才清）
echo     3. 删除桌面 / 开始菜单里的 Dr.COM 配置.url 快捷方式（存在才删）
echo     4. 删除安装目录里的 启动UI.bat（存在才删）
echo     5. 询问是否一并删除 config.json / password.txt / logs\ / backup\（默认保留）
echo     6. 提示可手动删除整个安装目录，并等待按键
echo.
echo ---- 以下全部是只读查询，不会改动任何东西 ----
echo.

echo [1/6] Windows 服务 DrcomAutoLogin 当前状态（sc query 只读）：
sc query DrcomAutoLogin
if errorlevel 1 (
    echo        ^> 服务未安装（真卸载时没有服务需要停止 / 移除）
) else (
    echo        ^> 上面 STATE 行就是当前状态（RUNNING = 正在运行）
)
echo.

echo [2/6] NSSM 工具（真卸载优先用它停止 / 移除服务）：
if exist "!CHECK_NSSM!" (
    for %%F in ("!CHECK_NSSM!") do echo        [存在] !CHECK_NSSM!  字节数: %%~zF
) else (
    echo        [不存在] !CHECK_NSSM!
    echo        真卸载会退回系统命令移除服务
)
echo.

echo [3/6] 托盘自启项 / 托盘偏好键（reg query 只读）：
reg query "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v DrcomAutoLoginTray
if errorlevel 1 (
    echo        ^> 托盘自启项不存在，真卸载会跳过
) else (
    echo        ^> 托盘自启项存在，真卸载会删除
)
echo.
reg query "HKLM\SOFTWARE\DrcomAutoLogin"
if errorlevel 1 (
    echo        ^> 托盘偏好键不存在，真卸载会跳过
) else (
    echo        ^> 托盘偏好键存在，真卸载会删除
)
echo.

echo [4/6] 桌面 / 开始菜单快捷方式（PowerShell Test-Path 只读）：
set "SHORTCUT_NAME=Dr.COM 配置.url"
powershell -NoProfile -Command "try { $n=$env:SHORTCUT_NAME; $dirs=@([Environment]::GetFolderPath('Desktop'), (Join-Path $env:USERPROFILE 'Desktop'), (Join-Path $env:PUBLIC 'Desktop'), [Environment]::GetFolderPath('Programs'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')); $found=0; foreach ($d in $dirs) { if (-not $d) { continue }; $p = Join-Path $d $n; if (Test-Path -LiteralPath $p) { Write-Host ('       [存在] ' + $p); $found = $found + 1 } }; if ($found -eq 0) { Write-Host '       [不存在] 桌面 / 开始菜单都没有找到 Dr.COM 配置.url' } else { Write-Host ('       真卸载会删除以上 ' + $found + ' 个快捷方式') }; exit 0 } catch { Write-Host ('SHORTCUT_CHECK_FAIL: ' + $_.Exception.Message); exit 1 }"
echo.

echo [5/6] 安装目录里的文件（只读：存在性 + 字节数）：
call :check_file "启动UI.bat" "!CHECK_DIR!\启动UI.bat"
call :check_file "config.json" "!CHECK_DIR!\config.json"
call :check_file "password.txt" "!CHECK_DIR!\password.txt"
echo.

echo [6/6] 日志 / 备份目录（只读：存在性 + 文件数 + 占用）：
powershell -NoProfile -Command "foreach ($d in @($env:CHECK_LOGS, $env:CHECK_BACKUP)) { if (Test-Path -LiteralPath $d -PathType Container) { $m = Get-ChildItem -LiteralPath $d -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum; $sz = 0; if ($m.Sum) { $sz = [math]::Round($m.Sum / 1KB, 1) }; Write-Host ('       [存在] ' + $d + '  文件数: ' + $m.Count + '  大小: ' + $sz + ' KB') } else { Write-Host ('       [不存在] ' + $d) } }; exit 0"
echo.

echo ============================================================
echo   只读预览结束：没有修改任何东西（服务、注册表、文件都没动）。
echo   真正卸载请运行不带参数的本脚本（需要管理员权限）：
echo     本脚本路径: %~f0
echo ============================================================
echo.
exit /b 0

REM 只读预览用的小工具：打印单个文件的存在性与字节数（只读，不修改）
:check_file
if exist "%~2" (
    for %%F in ("%~2") do echo        [存在] %~1  字节数: %%~zF
) else (
    echo        [不存在] %~1
)
goto :eof

:elevate

REM ============================================================
REM   自提升到管理员
REM ============================================================
net session >nul 2>&1
if errorlevel 1 (
    echo [INFO] 当前非管理员，重启提升权限...
    set "ELEVATE_PS1_NAME=drcom_elevate_!RANDOM!.ps1"
    powershell -NoProfile -Command "try { $ErrorActionPreference='Stop'; $p = Join-Path ([IO.Path]::GetTempPath()) $env:ELEVATE_PS1_NAME; $body = 'Start-Process -FilePath $args[0] -Verb RunAs'; [IO.File]::WriteAllText($p, $body, (New-Object Text.UTF8Encoding $true)); Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$p,$env:INSTALL_BAT_PATH } catch { Write-Host ('ELEVATE_FAIL: ' + $_.Exception.Message); exit 1 }"
    exit /b
)

set "SCRIPT_DIR=%~dp0"
if "!SCRIPT_DIR:~-1!"=="\" set "SCRIPT_DIR=!SCRIPT_DIR:~0,-1!"
set "NSSM=!SCRIPT_DIR!\tools\nssm.exe"
set "LOG_DIR=!SCRIPT_DIR!\logs"

echo.
echo ============================================================
echo   星尘闪连 (Stardust Flash Link) 服务 - 卸载 (v2.1.3.16)
echo ============================================================
echo   脚本目录: !SCRIPT_DIR!
echo ============================================================
echo.

REM ============================================================
REM   停止 + 删除服务
REM ============================================================
if exist "!NSSM!" (
    "!NSSM!" stop DrcomAutoLogin >nul 2>&1
    "!NSSM!" remove DrcomAutoLogin confirm >nul 2>&1
    echo [INFO] 已调用 NSSM 移除服务（忽略错误信息）
) else (
    echo [WARN] 未找到 NSSM: !NSSM!
    echo [WARN] 仍将尝试通过系统命令移除服务...
)
sc stop DrcomAutoLogin >nul 2>&1
sc delete DrcomAutoLogin >nul 2>&1

REM ============================================================
REM   清理「服务启动时对齐」写入的托盘自启项与偏好键（审计 J1）
REM   这两项可能不是安装器写的（静默升级路径），Inno 的卸载记录里没有 → 必须显式删，
REM   否则卸载后每次登录都会尝试启动已删除的 pythonw.exe 并报错。
REM ============================================================
reg delete "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v DrcomAutoLoginTray /f >nul 2>&1
if errorlevel 1 (
    echo [INFO] 托盘自启项不存在，跳过
) else (
    echo [INFO] 已删除托盘自启项
)
reg delete "HKLM\SOFTWARE\DrcomAutoLogin" /f >nul 2>&1
if errorlevel 1 (
    echo [INFO] 托盘偏好键不存在，跳过
) else (
    echo [INFO] 已删除托盘偏好键
)

REM ============================================================
REM   删除桌面 + 开始菜单快捷方式
REM   审计 P2-16：桌面可能被 OneDrive / 企业策略重定向，硬编码「用户目录\Desktop」会漏删
REM   （卸载后留死链）；这里改用与 install.bat 同一套 GetFolderPath 候选。
REM   中文文件名经环境变量传给 PowerShell（与安装脚本同款，Unicode 安全）。
REM ============================================================
set "SHORTCUT_NAME=Dr.COM 配置.url"
powershell -NoProfile -Command "try { $n=$env:SHORTCUT_NAME; $dirs=@([Environment]::GetFolderPath('Desktop'), (Join-Path $env:USERPROFILE 'Desktop'), (Join-Path $env:PUBLIC 'Desktop'), [Environment]::GetFolderPath('Programs'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')); $removed=0; foreach ($d in $dirs) { if (-not $d) { continue }; $p = Join-Path $d $n; if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force; $removed++ } }; Write-Host ('SHORTCUT_REMOVED=' + $removed); exit 0 } catch { Write-Host ('SHORTCUT_FAIL: ' + $_.Exception.Message); exit 1 }"
if errorlevel 1 (
    echo [WARN] 删除快捷方式失败，可手动删除桌面 / 开始菜单里的 Dr.COM 配置.url
) else (
    echo [INFO] 已清理快捷方式（桌面 / 开始菜单，不存在的自动跳过）
)

REM ============================================================
REM   删除安装器生成的 {app}\启动UI.bat（审计 J5）
REM   这个文件是 Inno 安装器在安装时用 [Code] 写出来的，不在 [Files] 清单里 →
REM   安装器卸载不会自动删，源码方式的卸载脚本以前也不管它，卸载后会留下死启动器。
REM ============================================================
if exist "!SCRIPT_DIR!\启动UI.bat" (
    del /F /Q "!SCRIPT_DIR!\启动UI.bat"
    echo [INFO] 已删除启动器: !SCRIPT_DIR!\启动UI.bat
) else (
    echo [INFO] 启动器 启动UI.bat 不存在，跳过
)

REM ============================================================
REM   询问：是否同时删除配置 / 密码 / 日志
REM ============================================================
echo.
echo ============================================================
echo   是否同时删除以下用户数据?
echo     - config.json     (运行配置)
echo     - password.txt    (校园网密码)
echo     - logs\           (所有日志)
echo     - backup\         (升级备份目录，里面也有一份 config.json 副本)
echo.
echo   选择:
echo     Y = 是，一并删除
echo     N = 否，保留 (默认)
echo ============================================================
rem v2.1.0.0（P7-3）：补 /D N（回车 / 超时走默认）+ /T 30（无人值守时别把卸载挂死）——
rem 默认永远是「保留用户数据」这个安全选项 ✓
choice /C YN /N /D N /T 30 /M "删除用户数据？(Y/N, 30 秒无输入按 N)"
REM 审计 P2-16：统一用延迟展开写法取 choice 的退出码（与其他块保持同一口径）
set "REMOVE_DATA=!ERRORLEVEL!"
echo.

if "!REMOVE_DATA!"=="1" (
    if exist "!SCRIPT_DIR!\config.json" del /F /Q "!SCRIPT_DIR!\config.json"
    if exist "!SCRIPT_DIR!\password.txt" del /F /Q "!SCRIPT_DIR!\password.txt"
    if exist "!LOG_DIR!" rd /s /q "!LOG_DIR!"
    REM 审计 J2（v2.1.3.15）：自动升级每次都会把 config.json 副本备份进 backup\ 目录，
    REM 只删根目录配置会把它留下 → 一并删除整个备份目录（保留分支不删，只提示）。
    if exist "!SCRIPT_DIR!\backup" rd /s /q "!SCRIPT_DIR!\backup"
    echo [INFO] 用户数据已删除
) else (
    echo [INFO] 用户数据已保留
    if exist "!SCRIPT_DIR!\config.json" echo        - !SCRIPT_DIR!\config.json
    if exist "!SCRIPT_DIR!\password.txt" echo       - !SCRIPT_DIR!\password.txt
    if exist "!LOG_DIR!" echo                       - !LOG_DIR!\
    if exist "!SCRIPT_DIR!\backup" echo [WARN] 备份目录里也有 config.json 副本: !SCRIPT_DIR!\backup\
    if exist "!SCRIPT_DIR!\backup" echo [WARN] 如需彻底删除配置，请手动删除上述备份目录
)

echo.
echo ============================================================
echo   卸载完成！
echo.
echo   如需彻底清理:
echo     - 删除整个目录: !SCRIPT_DIR!
echo ============================================================
echo.
pause
endlocal