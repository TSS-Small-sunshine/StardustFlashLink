# 交接材料（Windows 侧）

2026-09-27 交接快照，覆盖 Windows 端（星尘闪连）与 Android 端（DrcomAutoLogin）的全部 54 项待办。

| 项 | 位置 |
| --- | --- |
| 快照目录 | [`handover/20260927/`](20260927/) —— 入口 [`交接说明.md`](20260927/交接说明.md) |
| 交接包 ZIP（CI 封包） | Release [`handover-20260927`](https://github.com/TSS-Small-sunshine/StardustFlashLink/releases/tag/handover-20260927)（附 `.sha256`） |
| 另一份同内容快照 | Android 仓库 `TSS-Small-sunshine/DrcomAutoLogin` → `handover/20260927/` |

## 本仓库（Windows）的封包通道

| 产物 | workflow | 触发 | 取件 |
| --- | --- | --- | --- |
| 安装包 `.exe`（Inno Setup + 内嵌 Python + NSSM，全在 CI） | `Build Windows Installer` | 推 `main`（改 `**.md` 不触发）/ 手动 | Release `installer` |
| 交接包 ZIP | `Package Handover Bundle` | 推 `handover/**` / 手动 | Release `handover-<快照>` |

```powershell
gh workflow run "Build Windows Installer" -R TSS-Small-sunshine/StardustFlashLink --ref main
gh release download installer -R TSS-Small-sunshine/StardustFlashLink -D .\dist
```

## Windows 侧先做什么

1. **P0-1** 补 `import time` + 5 处 `_auto_update_mod.` 前缀 → 「检查更新/立即升级/升级开关/升级历史」恢复可用（30 分钟）
2. **P0-2** 升级 SHA256 校验改 fail-closed（`digest` 缺失即拒绝安装）
3. **P0-3** `password.txt` 跳过 `#` 注释行 + 清空模板

> ⚠️ 本仓库为 **public**：材料含修复前的安全细节，请勿在 Release 说明 / PR 描述 / Issue 里展开；P0、P1 完成后可考虑把本目录下架。
