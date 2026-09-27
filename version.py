# -*- coding: utf-8 -*-
"""version.py — 单点版本号。其它模块从这里 import VERSION。

变更日志：
    v2.0.2 (refactor) — 与 联网_service.py 解耦（当时版本字符串仍停在 v2.0.1）。
    版本号唯一来源 = 本文件的 VERSION；`packaging/setup.iss`、`install.bat`、
    `uninstall.bat`、`packaging/build.ps1` 与模块 docstring 里的版本必须同步更新。
"""

VERSION = "2.0.2.4"