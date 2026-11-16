@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ============================================================
REM   uninstall.bat - 移除 星尘闪连 (Stardust Flash Link) 服务 (v2.1.3.5)
REM   修复: UTF-8 BOM + chcp 65001（修复 cmd 中文编码问题）
REM   ⚠️ 已知限制（v2.1.0.0 记录，未修）：脚本用 setlocal EnableDelayedExpansion（需要 !VAR!），
REM      因此**安装路径里不能含 `!`**（会被当成变量展开吃掉）。请放在不含 `!` 的目录下。
REM ============================================================

REM 把脚本自身路径放到环境变量，供 PowerShell 读取
set "INSTALL_BAT_PATH=%~f0"

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
echo   星尘闪连 (Stardust Flash Link) 服务 - 卸载 (v2.1.3.5)
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
REM   询问：是否同时删除配置 / 密码 / 日志
REM ============================================================
echo.
echo ============================================================
echo   是否同时删除以下用户数据?
echo     - config.json     (运行配置)
echo     - password.txt    (校园网密码)
echo     - logs\           (所有日志)
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
    echo [INFO] 用户数据已删除
) else (
    echo [INFO] 用户数据已保留
    if exist "!SCRIPT_DIR!\config.json" echo        - !SCRIPT_DIR!\config.json
    if exist "!SCRIPT_DIR!\password.txt" echo       - !SCRIPT_DIR!\password.txt
    if exist "!LOG_DIR!" echo                       - !LOG_DIR!\
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