# stop-tray.ps1 — 安装/升级前把「本安装目录的托盘」请走（v2.0.8.1，由 setup.iss 调用）
#
# 为什么必须在**复制文件之前**做：
#   托盘是 {app}\python\pythonw.exe 起的用户会话进程，它 import urllib.request → ssl，
#   于是**锁定**了 {app}\python\libcrypto-3.dll 等运行库。安装器替换这些 DLL 时若被占用，
#   Inno 在静默模式下会默认 Abort → 回滚 → 结果「一半新一半旧」。
#   真机实测（2026-09-28，2.0.7.1 → 2.0.8.0）：新联网_service.py 已就位、新加的 metrics.py
#   却被回滚删掉 → 服务 import 失败 → Web UI 端口整个没了 ✗。
#
# 只杀「命令行里同时含 tray.py 与指定安装目录」的 pythonw —— 绝不按进程名一刀切，
# 免得误伤用户自己的其它 Python 程序。
param([string]$AppDir = '')
$ErrorActionPreference = 'SilentlyContinue'

$killed = 0
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if ($cmd -and ($cmd -like '*tray.py*') -and
        ((-not $AppDir) -or ($cmd -like ('*' + $AppDir + '*')))) {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
}
Write-Output ("stop-tray: app_dir=" + $AppDir + " killed=" + $killed)
exit 0
