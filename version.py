# -*- coding: utf-8 -*-
"""version.py — 单点版本号与版本代号。其它模块从这里 import。

版本号规则（详见 docs/VERSIONING.md）：
    MAJOR.MINOR.PATCH.BUILD
      - MAJOR.MINOR  = **版本线**，每条线有一个代号（CODENAME），如 2.0 = "Sirius"
      - PATCH        = 该线内的功能 / 修复批次
      - BUILD        = 同批次的补丁构建（hotfix）

命名示例：v2.0.4.0 "Sirius"、v2.1.0.0 "Vega"。

唯一来源 = 本文件的 VERSION / CODENAME；`packaging/setup.iss`、`install.bat`、
`uninstall.bat`、`packaging/build.ps1`、README 与模块 docstring 里的版本必须同步。
"""

VERSION = "2.0.7.1"

# —— 版本线代号（MAJOR.MINOR 级别；换线必须换名）——
# 主题：亮星名（中英对照），呼应「星尘闪连」。候选表见 docs/VERSIONING.md。
CODENAME = "Sirius"
CODENAME_CN = "天狼星"

# 展示用全名，如 "2.0.4.0 Sirius（天狼星）"
VERSION_FULL = "{} {}（{}）".format(VERSION, CODENAME, CODENAME_CN)