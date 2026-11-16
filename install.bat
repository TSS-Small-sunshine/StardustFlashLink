@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ============================================================
REM   install.bat - 星尘闪连 (Stardust Flash Link) 服务安装脚本 (v2.1.3.5)
REM   修复: UTF-8 BOM + chcp 65001（修复 cmd 中文编码问题）
REM   ⚠️ 已知限制（v2.1.0.0 记录，未修）：脚本用 setlocal EnableDelayedExpansion（需要 !VAR!），
REM      因此**安装路径里不能含 `!`**（会被当成变量展开吃掉）。请放在不含 `!` 的目录下。
REM   PowerShell 通过 ASCII 临时 .ps1 文件执行，避开 cmd→ps 编码边界
REM   含中文路径通过环境变量传递（Unicode 通道）
REM ============================================================

REM 把脚本自身路径放到环境变量，供 PowerShell 读取（Unicode 安全）
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

REM ============================================================
REM   路径解析
REM ============================================================
set "SCRIPT_DIR=%~dp0"
if "!SCRIPT_DIR:~-1!"=="\" set "SCRIPT_DIR=!SCRIPT_DIR:~0,-1!"
set "NSSM=!SCRIPT_DIR!\tools\nssm.exe"
set "SERVICE_SCRIPT=!SCRIPT_DIR!\联网_service.py"
set "LOG_DIR=!SCRIPT_DIR!\logs"

echo.
echo ============================================================
echo   星尘闪连 (Stardust Flash Link) - Windows 服务安装 (v2.1.3.5)
echo ============================================================
echo   脚本目录: !SCRIPT_DIR!
echo ============================================================
echo.

REM ============================================================
REM   检测 Python（优先使用脚本同目录的内嵌运行时）
REM ============================================================
set "PYTHON="

REM 0. 优先：脚本同目录下的内嵌 Python（CI 构建 / 安装包自带，免预装）
if exist "!SCRIPT_DIR!\python\python.exe" (
    set "PYTHON=!SCRIPT_DIR!\python\python.exe"
)

REM 1. 其次：PATH 中的 python
if not defined PYTHON (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%i in ('where python') do (
            if not defined PYTHON set "PYTHON=%%i"
        )
    )
)
if "!PYTHON!"=="" (
    rem v2.1.0.0（P7-3）：别硬编码 C:\Python314 —— 小版本一升就漂移 ✗。
    rem 先问官方启动器 `py -3`，再退到 %LOCALAPPDATA%\Programs\Python\Python3* 这一常见位置。
    where py >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
            if not defined PYTHON if exist "%%i" set "PYTHON=%%i"
        )
    )
)
if "!PYTHON!"=="" (
    for /d %%d in ("!LOCALAPPDATA!\Programs\Python\Python3*") do (
        if not defined PYTHON if exist "%%d\python.exe" set "PYTHON=%%d\python.exe"
    )
)
if "!PYTHON!"=="" (
    echo [ERROR] 未找到 python.exe。请把 Python 加入 PATH（或用 py 启动器），或改用自带内嵌 Python 的安装包（推荐）
    pause
    exit /b 1
)
echo [INFO] Python: !PYTHON!

REM ============================================================
REM   检查脚本
REM ============================================================
if not exist "!SERVICE_SCRIPT!" (
    echo [ERROR] 找不到脚本: "!SERVICE_SCRIPT!"
    pause
    exit /b 1
)
echo [INFO] 服务脚本: !SERVICE_SCRIPT!

REM ============================================================
REM   准备 logs/ 目录
REM ============================================================
if not exist "!LOG_DIR!" mkdir "!LOG_DIR!"
echo [INFO] 日志目录: !LOG_DIR!

REM ============================================================
REM   确保 NSSM（用 ASCII 临时 .ps1 下载，避开 cmd→ps 编码边界）
REM ============================================================
if exist "!NSSM!" (
    echo [INFO] NSSM 已存在: !NSSM!
) else (
    echo [INFO] 正在下载 NSSM 2.24...
    if not exist "!SCRIPT_DIR!\tools" mkdir "!SCRIPT_DIR!\tools"

    REM 把目标 zip 路径 + .ps1 名称放到环境变量（Unicode 安全）
    REM 审计 P2-4：下载完立刻按仓库根的 tools.lock.json 比对 SHA256（哈希值只在 lock 里，本文件不写死）
    set "NSSM_LOCK_PATH=!SCRIPT_DIR!\tools.lock.json"
    set "NSSM_ZIP_PATH=!TMP!\nssm.zip"
    set "NSSM_DL_PS1_NAME=drcom_nssm_dl_!RANDOM!.ps1"
    powershell -NoProfile -Command "try { $ErrorActionPreference='Stop'; $p = Join-Path ([IO.Path]::GetTempPath()) $env:NSSM_DL_PS1_NAME; $body='$ErrorActionPreference = ''Stop''; try { Invoke-WebRequest -Uri ''https://nssm.cc/release/nssm-2.24.zip'' -OutFile $env:NSSM_ZIP_PATH -UseBasicParsing } catch { Write-Host (''NSSM_FAIL: '' + $_.Exception.Message); exit 1 }; Write-Host ''NSSM_DL_OK''; $zip = $env:NSSM_ZIP_PATH; $lockPath = $env:NSSM_LOCK_PATH; $url = ''https://nssm.cc/release/nssm-2.24.zip''; $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower(); if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) { Write-Host (''[WARN] 未找到 tools.lock.json（'' + $lockPath + ''），跳过 NSSM 哈希校验''); exit 0 }; try { $doc = Get-Content -LiteralPath $lockPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Write-Host (''[WARN] tools.lock.json 解析失败（'' + $_.Exception.Message + ''），跳过 NSSM 哈希校验''); exit 0 }; $expected = ''''; foreach ($t in @($doc.tools)) { if ($t -and $t.url -eq $url -and $t.sha256) { $expected = ([string]$t.sha256).Trim().ToLower(); break } }; if (-not $expected) { Write-Host ''[WARN] tools.lock.json 里没有 NSSM 条目，跳过哈希校验''; exit 0 }; if ($actual -eq $expected) { Write-Host (''[INFO] 校验通过：NSSM SHA256 '' + $actual); exit 0 }; Write-Host (''[ERROR] 校验不匹配：期望 '' + $expected + ''，实际 '' + $actual); exit 2'; [IO.File]::WriteAllText($p, $body, (New-Object Text.UTF8Encoding $true)); & powershell -NoProfile -ExecutionPolicy Bypass -File $p; $rc=$LASTEXITCODE; Remove-Item $p -Force -ErrorAction SilentlyContinue; exit $rc } catch { Write-Host ('FAIL: ' + $_.Exception.Message); exit 1 }"
    REM 审计 P2-16：块内取退出码必须用延迟展开写法 —— 百分号写法在解析整个 if 块时就已展开，
    REM 拿到的是块执行前的陈旧值：下载失败却判成功 / 反之，后面 tar 才炸、错误信息误导 ✗
    set "NSSM_DL_RC=!ERRORLEVEL!"
    REM 审计 P2-4：退出码 2 = SHA256 与 tools.lock.json 不匹配（供应链风险）→ 必须中止安装；
    REM            退出码 1 = 下载 / 写文件失败；lock 缺失或解析失败时脚本内部已降级为退出码 0（只 warning）。
    if !NSSM_DL_RC! equ 2 (
        echo [ERROR] NSSM 压缩包 SHA256 校验不匹配，已中止安装，避免把未经校验的 nssm.exe 注册成系统服务。
        echo         期望值与实际值见上一行「校验不匹配」。可能原因：
        echo           1. 上游文件被替换，或网络 / 代理被投毒
        echo           2. 本机代理缓存了旧版本压缩包
        echo           3. tools.lock.json 过期，即本仓库升级了 nssm 版本却没更新哈希锁
        echo [INFO] 处置：确认网络可信后重试；或手动从 https://nssm.cc/download 下载 2.24，
        echo [INFO] 解压后把 win64\nssm.exe 复制到 !NSSM! 再重跑本脚本。
        pause
        exit /b 1
    )
    if !NSSM_DL_RC! neq 0 (
        echo [ERROR] 下载 NSSM 失败，请检查网络后重试
        pause
        exit /b 1
    )

    if exist "!TMP!\nssm-extract" rd /s /q "!TMP!\nssm-extract"
    if not exist "!TMP!\nssm-extract" mkdir "!TMP!\nssm-extract" 2>nul
    tar -xf "!TMP!\nssm.zip" -C "!TMP!\nssm-extract"
    if errorlevel 1 (
        echo [ERROR] 解压 NSSM 失败，请确认 Windows 10+ 自带 tar.exe
        pause
        exit /b 1
    )

    copy /Y "!TMP!\nssm-extract\nssm-2.24\win64\nssm.exe" "!NSSM!" >nul
    if errorlevel 1 (
        echo [ERROR] 复制 nssm.exe 失败
        pause
        exit /b 1
    )
    echo [INFO] NSSM 已安装到: !NSSM!
)

REM ============================================================
REM   幂等：先停再删
REM ============================================================
"!NSSM!" stop DrcomAutoLogin >nul 2>&1
"!NSSM!" remove DrcomAutoLogin confirm >nul 2>&1

REM ============================================================
REM   注册服务
REM ============================================================
REM 只传 Application；AppParameters 随后用 reg add 直写（值含引号字符）
REM 为什么不走 nssm 命令行传参：nssm 会把 AppParameters 原样拼在 Application 之后，
REM 安装路径含空格时（如 D:\Program Files\...）Windows 会在空格处劈开参数，
REM Python 只会收到 "D:\Program"，服务反复启动失败。
"!NSSM!" install DrcomAutoLogin "!PYTHON!"
if errorlevel 1 (
    echo [ERROR] nssm install 失败
    pause
    exit /b 1
)

REM 直写 AppParameters：\" 是传给 reg.exe 的转义引号，最终注册表值为 "...\联网_service.py"
reg add "HKLM\SYSTEM\CurrentControlSet\Services\DrcomAutoLogin\Parameters" /v AppParameters /t REG_SZ /d "\"!SERVICE_SCRIPT!\"" /f >nul
if errorlevel 1 (
    echo [ERROR] 写入服务参数 AppParameters 失败
    pause
    exit /b 1
)

"!NSSM!" set DrcomAutoLogin AppDirectory "!SCRIPT_DIR!"
"!NSSM!" set DrcomAutoLogin DisplayName "Dr.COM 校园网自动登录"
"!NSSM!" set DrcomAutoLogin Description "星尘闪连 (Stardust Flash Link) - Dr.COM 校园网自动登录（v2.1.3.5）"
"!NSSM!" set DrcomAutoLogin Start SERVICE_AUTO_START
"!NSSM!" set DrcomAutoLogin AppStdout "!LOG_DIR!\service_stdout.log"
"!NSSM!" set DrcomAutoLogin AppStderr "!LOG_DIR!\service_stderr.log"
"!NSSM!" set DrcomAutoLogin AppRotateFiles 1
"!NSSM!" set DrcomAutoLogin AppRotateBytes 1048576
REM P0-7：AppExit 与 setup.iss 统一为 Ignore（v2.0.2.3 的 a77ea46 修复：
REM 端口占用时 NSSM 不再无限重启循环）。之前这里是 Restart，与安装器行为打架。
"!NSSM!" set DrcomAutoLogin AppExit Default Ignore
"!NSSM!" set DrcomAutoLogin AppExit 0 Ignore

REM ============================================================
REM   立即启动测试
REM ============================================================
"!NSSM!" start DrcomAutoLogin
if errorlevel 1 (
    echo [WARN] 服务注册成功，但立即启动失败。可手动启动。
) else (
    echo [INFO] 服务已启动
)

REM ============================================================
REM   创建桌面 + 开始菜单快捷方式（用 ASCII 临时 .ps1 + 环境变量）
REM ============================================================
echo [INFO] 创建桌面 + 开始菜单快捷方式...
REM 把快捷方式名 + .ps1 名称放到环境变量（Unicode 安全）
set "SHORTCUT_NAME=Dr.COM 配置.url"
set "SHORTCUT_PS1_NAME=drcom_shortcut_%RANDOM%.ps1"
powershell -NoProfile -Command "try { $ErrorActionPreference='Stop'; $p = Join-Path ([IO.Path]::GetTempPath()) $env:SHORTCUT_PS1_NAME; $body='$ErrorActionPreference=''Stop''; $s=[Environment]::GetFolderPath(''StartMenu''); $n=$env:SHORTCUT_NAME; $content=''[InternetShortcut]''+[Environment]::NewLine+''URL=http://127.0.0.1:8848''; $desktopCandidates=@([Environment]::GetFolderPath(''Desktop''), (Join-Path $env:USERPROFILE ''Desktop''), (Join-Path $env:PUBLIC ''Desktop'')); $desktop=$null; foreach ($c in $desktopCandidates) { if ($c -and (Test-Path -LiteralPath $c -PathType Container)) { $desktop=$c; break } }; $ok=$false; try { if ($desktop) { [IO.File]::WriteAllText((Join-Path $desktop $n), $content, [Text.Encoding]::ASCII); $ok=$true } else { Write-Host ''DESKTOP_SKIP: no valid Desktop path'' }; [IO.File]::WriteAllText((Join-Path $s (Join-Path ''Programs'' $n)), $content, [Text.Encoding]::ASCII); Write-Host ''SHORTCUT_OK''; exit 0 } catch { Write-Host (''SHORTCUT_FAIL: ''+$_.Exception.Message); exit 1 }'; [IO.File]::WriteAllText($p, $body, (New-Object Text.UTF8Encoding $true)); & powershell -NoProfile -ExecutionPolicy Bypass -File $p; $rc=$LASTEXITCODE; Remove-Item $p -Force -ErrorAction SilentlyContinue; exit $rc } catch { Write-Host ('FAIL: ' + $_.Exception.Message); exit 1 }"
REM 审计 P2-16：统一用延迟展开的退出码（这里虽在块外，但与块内写法保持同一口径）
set "SHORTCUT_RC=!ERRORLEVEL!"
if !SHORTCUT_RC! neq 0 (
    echo [WARN] 创建快捷方式失败，可手动打开 http://127.0.0.1:8848
) else (
    echo [INFO] 桌面快捷方式已创建: !SHORTCUT_NAME!（实际目录由 PowerShell GetFolderPath 解析，兼容 OneDrive 重定向）
    echo [INFO] 开始菜单快捷方式已创建
)

echo.
echo ============================================================
echo   安装完成！
echo ============================================================
echo   管理命令:
echo     启动:  "!NSSM!" start DrcomAutoLogin
echo     停止:  "!NSSM!" stop DrcomAutoLogin
echo     卸载:  请运行 uninstall.bat
echo.
echo   桌面图标:
echo     Dr.COM 配置  (双击打开 Web UI)
echo.
echo   Web UI:
echo     http://127.0.0.1:8848
echo.
echo   日志路径:
echo     业务日志:  !LOG_DIR!\campus_login.log
echo     stdout:    !LOG_DIR!\service_stdout.log
echo     stderr:    !LOG_DIR!\service_stderr.log
echo ============================================================
echo.
pause
endlocal