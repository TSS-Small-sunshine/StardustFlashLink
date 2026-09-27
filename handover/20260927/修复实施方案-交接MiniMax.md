# 修复实施方案 v2 —— MiniMax 交接文档

> **本文件是给下一位执行 Agent（MiniMax）的施工图**，不是总结。
> 配套证据库：[`项目不足分析报告.md`](项目不足分析报告.md) · 静态检查工具：[`_verify_names.py`](_verify_names.py)
>
> **工作区根**：`D:\Student_Workstation\Using_Workstation`
> **Python**：`C:\Program Files\Python314\python.exe`（3.14.6，已实测）
> **PowerShell**：可用（当前 `danger-full-access`）
> **编码**：源文件 UTF-8。读文件用 `read` 工具（PowerShell 的 `Get-Content` 会按系统代码页解码，中文显示为乱码，**不是文件损坏**）。`.bat` 保留 CRLF。

---

## 0. v2 修订说明（先读这一节）

v1 方案基于「静态分析 + 源码阅读」。v2 新增了 **git 历史考古**，结果**推翻了 v1 的 3 条结论**。如果你只读一节，读这节。

### ❌ 更正 1（最重要）：WebView 是**主登录路径**，绝不能降级或移除

- **v1 的错误建议**：「评估改用 `LoginEngine` 的纯 HTTP 接口登录，把 WebView 路径降级为可选」
- **实际情况**：git 提交 `ec9ffb9` 记录了**真机实测**结论：

  > 同一台手机 / 同一张网卡 / 同一份 DHCP 指纹下：手机浏览器登录认证页 → 成功且有网；本 App 直接请求 `/eportal/portal/login` → 始终返回
  > `{"result":0,"msg":"该账号PC终端在线数已上限"}`
  > 已逐个排除：① IP/MAC/ac_ip/ac_name 取错（已按 `a41.js` 优先级对齐，与浏览器 URL 逐字一致）② 多发 `terminal_type=1` ③ UA 是自定义串 ④ 缺 `mac_type`（1/2/3 三个值报错完全不变）⑤ AC 按 MAC 缓存终端类型（关闭随机 MAC 后仍失败）
  > **⇒ AC 的终端归类不取决于可控制的 HTTP 参数。唯一可靠解法是让门户页面自己的 JS 去提交登录。**

- **当前架构**（[`LoginEngine.kt:653-663`](campus-network-service/android/app/src/main/java/com/drcom/autologin/LoginEngine.kt#L653)）：
  ```
  OFFLINE（拿到网关响应但未认证）
    ├─ 首选：LoginWorker.tryStartPortalLogin()  → WebView 跑门户自己的 JS
    │        └─ 返回 true → 日志「WebView 已接管本次登录，跳过 HTTP 登录」
    └─ 回落：login()  → 纯 HTTP（无悬浮窗权限 / 启动失败时）
  ```
- **结论**：**WebView 路径必须保留并加固，不得移除、不得降级为「可选」**。HTTP 路径是给**其它学校**的兜底，也不得删除。任何「简化成只用 HTTP」的改动都会让产品在该校**完全不可用**。

### ⚠️ 更正 2：`exported="true"` 是**故意加的**，但**加错了理由** —— 正确修法是「debug 变体覆盖」而非直接改 false

- **v1 的做法**：直接把 `exported="true"` 改成 `false`
- **git 证据**：提交 `3fd8e9a` 明确写：

  > `PortalLoginActivity` 加 `android:exported="true"` —— 修 `SecurityException: am start -n com.drcom.autologin/.PortalLoginActivity` 不再被拒

- **为什么这个理由不成立**：

  | 诉求 | 是否需要 `exported=true` |
  | --- | --- |
  | App 自己（`LoginWorker`）启动自己的 Activity | **不需要**。同 UID 显式 Intent 永远可用，与 `exported` 无关 |
  | `adb shell am start` 调试启动 | **需要**。`adb shell` 是 `shell` UID（另一个应用），`exported=false` 时确实被拒 |
  | Android 10+ **后台**启动 Activity | **不需要** `exported`。真正的开关是 [`LoginWorker.kt:74`](campus-network-service/android/app/src/main/java/com/drcom/autologin/LoginWorker.kt#L74) 的 `Settings.canDrawOverlays(ctx)` + `SYSTEM_ALERT_WINDOW` 权限 —— **已经做了** |

  ⇒ 「修 SecurityException」修的是**调试便利性**，却把**发布包的攻击面**打开了。两者不该用同一个开关。
- **v2 的做法**：主 manifest 回 `exported="false"`；**新增 `app/src/debug/AndroidManifest.xml`** 让 debug 包仍是 `true`。这样 release 收口、adb 调试照旧可用。

### ⚠️ 更正 3：107 处日志是**故意加的调试设施**，应「按构建类型门禁」而非删除

- **git 证据**：`3fd8e9a` 一次性加了 **50 处直接 `Log` + 57 处 `LogStore` 自动镜像 = 107 个 logcat 输出点**，并统一 tag `DrcomAutoLogin`，明确是**排障设施**（该校网络问题排查极度依赖 logcat）。
- **v1 的说法**：「日志泄露账号，建议脱敏 + 删除 `Log.v/d`」
- **v2 的做法**：**保留全部日志能力**，只做两件事：① 账号/IP/MAC 值**脱敏**；② `Log.v/d` 加 `BuildConfig.DEBUG` 门禁 + R8 `-assumenosideeffects` 自动剔除。release 包仍保留 `Log.i/w/e`（排障需要），但不含敏感值。

### ✅ 未变更的结论（v1 仍然有效）

Windows 侧的 6 个未定义裸名、fail-closed、模板注释、`.gitignore`、token 鉴权、升级信任链等**全部维持原判**。本次重跑 AST 复核，`web_api.py` 仍是那 6 个：

```
未定义也未注入的裸名 (6):
   L320   time
   L356   _do_check_now
   L368   _do_update_now
   L371   _set_update_state
   L391   _log_upgrade
   L402   UPGRADE_HISTORY_MAX_LINES
```

---

## 1. 已确证的架构事实（**禁止推翻**）

执行前必须接受以下事实，它们是 git 历史 + 真机实测的结论，不是猜测。**任何与之冲突的"优化"都是回归。**

| # | 事实 | 证据来源 | 违反后果 |
| --- | --- | --- | --- |
| **F1** | 该校 AC 的终端归类**不受 HTTP 参数控制**，纯 HTTP 登录**必然失败** | `ec9ffb9` 提交信息（真机实测 + 5 项排除） | 该校用户完全无法上网 |
| **F2** | 因此 **WebView 路径 = 主路径**，HTTP 路径 = 其它学校的兜底 | `LoginEngine.kt:653-663` | 同上 |
| **F3** | 后台启动 Activity 靠的是 `SYSTEM_ALERT_WINDOW` + `canDrawOverlays`，**不是** `exported` | `LoginWorker.kt:74-81`、`ec9ffb9` | 误改会以为在"修 bug"实际在"破功能" |
| **F4** | `exported=true` 只服务 `adb shell am start` 调试 | `3fd8e9a` 提交信息 | release 攻击面（B-H2） |
| **F5** | 门户参数取值链以 `a41.js` 为准：<br>IP: `v46ip → ss5 → v4ip → hex16ToString(ss3) → 本机网卡`<br>MAC: `ss4 → olmac → 000000000000` | `4be92e4`（含 a41.js 行号）、`a41.js:1183-1188,1206-1207` | AC 认证失败（此坑已踩过） |
| **F6** | `terminal_type` 字段门户根本不存在，**多发会被判 PC** | `4be92e4`（全文搜 `terminal_type` 0 命中） | AC 报「PC 终端在线数已上限」 |
| **F7** | 终端类型字段名是 **`mac_type`**（值 0-4，默认 2=手机），0 时不发送 | `0d96e7c`（a41.js:423、681-711） | 同上 |
| **F8** | Windows 侧 `_attach()` 用 `globals()` 注入 27 个裸名 —— 静态分析对跨模块引用失效 | `_verify_names.py` 输出 | 本次 6 个高危里 3 个源于此 |

> **给 MiniMax 的硬性要求**：不要"清理"上面任何一条。如果你的改动与 F1–F8 冲突，**停下来**，在 CHANGELOG 写明冲突点并跳过该任务，而不是按自己的想法改。

---

## 2. 全局约束

### 2.1 不变量（动任何代码前先核对）

| # | 不变量 | 原因 |
| --- | --- | --- |
| **I1** | NSSM 服务名 `DrcomAutoLogin`、`AppId {A8F2E3D1-7C4B-4F89-9D5E-1A2B3C4D5E6F}`、安装路径 `C:\Program Files\DrcomAutoLogin\` 不变 | 已发布用户的卸载/覆盖升级依赖 |
| **I2** | HTTP API 路径全部不变（`/api/status`、`/api/config`、`/api/login`、`/api/password`、`/api/update/*`、`/api/config/export`、`/api/config/import`、`/api/restart`） | Web UI 内嵌页硬编码 |
| **I3** | Web UI 只监听 `127.0.0.1`，**不得**改 `0.0.0.0` | 安全模型底线 |
| **I4** | 密码只存 `password.txt`；接口永不回传密码原文 | 已对外承诺 |
| **I5** | `config.json` / `password.txt` 保持 tmp + `os.replace` 原子写 | 断电一致性 |
| **I6** | Windows 侧**零第三方 Python 依赖**（仅 stdlib） | 内嵌 CPython 3.12 |
| **I7** | Android 版本号单一来源 = `app/build.gradle.kts` | 当前 5 处漂移的根因 |
| **I8** | Android 除已声明的 5 个 implementation 外不新增依赖（`androidx.security` 是唯一例外，Phase 2 批准） | 控制 APK 与兼容风险 |
| **I9** | 安装包产物名 `StardustFlashLink-Setup-v{ver}.exe` 不变 | 现有下载链接固定 |
| **I10** | **不重构 `_attach()` 注入模式**（Phase 4 之后可选） | 见 F8，重构成本高、回归面大 |
| **I11** | **WebView 登录路径保留**（见 F1/F2） | 该校唯一可用路径 |
| **I12** | **`a41.js` 只读**，不得修改（它是协议事实基准） | F5/F6/F7 的依据 |

### 2.2 完成定义（DoD）

每条任务完成 = 以下全部满足：

- [ ] 代码改完，且 `python -m py_compile` / `gradlew assembleDebug` 通过
- [ ] 跑过该任务的「验证」命令，输出符合预期
- [ ] **逐条核对 §1 的 F1–F8 未被违反**
- [ ] 未破坏 §2.1 的 I1–I12
- [ ] 新增文件已加入合适的 `.gitignore`
- [ ] `CHANGELOG.md` 追加 `## [unreleased]` 条目
- [ ] 按模板提交 git

### 2.3 commit 模板

```
<type>(<scope>): <中文简述>

<可选：为什么这么改 / 验证方式 / 影响面>

type  ∈ fix | feat | refactor | chore | docs | test | security | ci
scope ∈ web_api | auto_update | protocol | password | android | ci | docs | version
```

**禁止 `git add .`**，逐个文件 `git add <path>`。

---

## 3. Phase 0：阻断级（半天）

> 6 条，每条 ≤30 分钟（T0.4 除外）。

### T0.1 修 `web_api.py` 6 个未定义裸名 ⏱30min

**作用域**：`DrcomAutoLogin-Windows/web_api.py`（**仅此文件**）

| # | 位置 | 改动 |
| --- | --- | --- |
| 1 | import 块 [:18-31](DrcomAutoLogin-Windows/web_api.py#L18) | 在 `import threading` 后插入 `import time`（保持字母序） |
| 2 | [:356](DrcomAutoLogin-Windows/web_api.py#L356) | `_do_check_now()` → `_auto_update_mod._do_check_now()` |
| 3 | [:368](DrcomAutoLogin-Windows/web_api.py#L368) | `_do_update_now()` → `_auto_update_mod._do_update_now()` |
| 4 | [:358](DrcomAutoLogin-Windows/web_api.py#L358) | `_log_upgrade(` → `_auto_update_mod._log_upgrade(` |
| 5 | [:370](DrcomAutoLogin-Windows/web_api.py#L370) | 同上 |
| 6 | [:371](DrcomAutoLogin-Windows/web_api.py#L371) | `_set_update_state(` → `_auto_update_mod._set_update_state(` |
| 7 | [:391](DrcomAutoLogin-Windows/web_api.py#L391) | `_log_upgrade(` → `_auto_update_mod._log_upgrade(` |
| 8 | [:402](DrcomAutoLogin-Windows/web_api.py#L402) | `UPGRADE_HISTORY_MAX_LINES` → `_auto_update_mod.UPGRADE_HISTORY_MAX_LINES` |

**前置**：确认已注入 `_auto_update_mod`：

```powershell
& "C:\Program Files\Python314\python.exe" -c "import re,pathlib; s=pathlib.Path(r'D:\Student_Workstation\Using_Workstation\DrcomAutoLogin-Windows\联网_service.py').read_text(encoding='utf-8'); m=re.search(r'_attach\(.*?auto_update_mod\s*=\s*([A-Za-z_][A-Za-z0-9_]*)', s, re.S); print('INJECTED AS:', m.group(1)) if m else print('NOT FOUND - 需在 web_api._attach 调用处补 auto_update_mod=_auto_update_mod')"
```

若 `NOT FOUND`：先补 `联网_service.py` 里 `web_api._attach(...)` 的 `auto_update_mod=_auto_update_mod` 实参，再做上表 8 处替换。

**参照物**：[:331](DrcomAutoLogin-Windows/web_api.py#L331) 已有正确写法 `_auto_update_mod._schedule_success_clear()`（v2.0.2.3.2 修的），照抄它的风格。

**验证**：
```powershell
cd DrcomAutoLogin-Windows
& "C:\Program Files\Python314\python.exe" ..\_verify_names.py web_api.py
# 预期：未定义也未注入的裸名 (0):（无）
& "C:\Program Files\Python314\python.exe" -m py_compile web_api.py
```

**commit**：`fix(web_api): 修复 6 个未定义裸名（time 未导入 + 5 处跨模块调用漏改 _auto_update_mod 前缀）`

> 影响：修好后「立即检查更新 / 立即升级 / 升级开关 / 升级历史」4 个功能才真正可用。这 4 个功能**目前全部 NameError**。

---

### T0.2 自动升级 SHA256 改 fail-closed ⏱5min

**作用域**：`DrcomAutoLogin-Windows/auto_update.py` 的 `_do_update_now`（[:532-543](DrcomAutoLogin-Windows/auto_update.py#L532)）

**现状（fail-open，源码原文）**：
```python
if digest and not _verify_sha256(installer_path, digest):
    ...  # 拒绝
elif digest:
    _log_upgrade("INFO", "SHA256 校验通过")
# digest 为 None → 不做任何校验，直接备份 + 执行 installer
```

**改为**：
```python
if not digest:
    msg = "远端未返回 digest，无法校验完整性，拒绝安装"
    _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
    _log_upgrade("ERROR", msg)
    try:
        os.remove(installer_path)
    except OSError:
        pass
    return {"ok": False, "error": msg}
if not _verify_sha256(installer_path, digest):
    try:
        os.remove(installer_path)
    except OSError:
        pass
    msg = "SHA256 校验失败，已删除安装器"
    _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
    _log_upgrade("ERROR", "{}（期望 {}）".format(msg, digest[:16] + "..."))
    return {"ok": False, "error": msg}
_log_upgrade("INFO", "SHA256 校验通过")
```

**验证**：
```powershell
cd DrcomAutoLogin-Windows
& "C:\Program Files\Python314\python.exe" -c "import pathlib; s=pathlib.Path('auto_update.py').read_text(encoding='utf-8'); print('OK' if 'if not digest:' in s and '拒绝安装' in s else 'FAIL')"
```

**commit**：`fix(auto_update): SHA256 校验 fail-closed，digest 缺失拒绝安装`

---

### T0.3 `password.txt` 注释行被当密码 ⏱10min

**作用域**：`DrcomAutoLogin-Windows/联网_service.py` 的 `_load_password_from_disk`（[:148-149](DrcomAutoLogin-Windows/联网_service.py#L148)）+ `packaging/password.txt.template`

**现状**：模板唯一一行是 `# 在此行写入你的校园网账号密码（去掉本注释行）...`；读取逻辑是 `line = f.readline()` → `pwd = line.strip()` → **非空即当前密码**，无 `#` 跳过。

**改动 1** —— 替换：
```python
with open(PASSWORD_FILE, "r", encoding="utf-8") as f:
    line = f.readline()
```
为：
```python
with open(PASSWORD_FILE, "r", encoding="utf-8") as f:
    # 跳过空行与 '#' 注释行（防止装包时模板提示被当成真实密码）
    line = ""
    for raw in f:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        line = s
        break
```

**改动 2** —— `packaging/password.txt.template` 内容清空（保留文件，避免改 `setup.iss` 引入回归）。

**验证**：
```powershell
cd DrcomAutoLogin-Windows
& "C:\Program Files\Python314\python.exe" -c "
import tempfile, os, pathlib, sys
sys.path.insert(0, '.')
import 联网_service as m
d = tempfile.mkdtemp(); p = os.path.join(d, 'password.txt')
pathlib.Path(p).write_text('# 在此行写入你的校园网账号密码\nrealpass\n', encoding='utf-8')
m.PASSWORD_FILE = p
print('GOT:', repr(m._load_password_from_disk()), '=> OK' if m._load_password_from_disk() == 'realpass' else '=> FAIL')
"
# 另验证纯注释文件返回 None
```

**commit**：`fix(password): _load_password_from_disk 跳过 # 注释行 + 清空模板内容`

---

### T0.4 轮换 Android 签名密钥 + 删明文口令文件 ⏱1h

**作用域**（**新建/删除，不改现有源码**）：

| 动作 | 路径 |
| --- | --- |
| 删除 | `campus-network-service/android-signing/keystore-credentials.txt` |
| 替换 | `campus-network-service/android-signing/drcom-release.p12`（重新生成） |
| 新建 | `campus-network-service/android-signing/.gitignore` |
| 新建 | `campus-network-service/android-signing/README.md` |

**`.gitignore` 内容**：
```gitignore
# 签名密钥绝不入库
*
!.gitignore
!README.md
```

**生成新密钥**：
```powershell
cd campus-network-service\android-signing
keytool -genkeypair -v -storetype PKCS12 -keystore drcom-release.p12 -alias drcom `
  -keyalg RSA -keysize 2048 -validity 10000 `
  -storepass "<新口令>" -keypass "<新口令>" `
  -dname "CN=TSS-Small-sunshine, OU=DrcomAutoLogin, O=TSS-Small-sunshine, L=Fuzhou, ST=Fujian, C=CN"
```

**口令去向**：GitHub Actions Secrets（`SIGNING_STORE_PASSWORD` / `SIGNING_KEY_ALIAS` / `SIGNING_KEY_PASSWORD`）。`app/build.gradle.kts:10,33-35` **早已**按环境变量读取 —— 不需要改 Gradle。

**⚠️ 发版影响（必须写进 CHANGELOG 与 Release notes）**：
> 签名密钥已轮换。**已安装旧版本的用户无法直接覆盖升级**，需先卸载再装新包（或使用 Play App Signing 的 key lineage 传承）。
> 建议后续接入 **Google Play App Signing**：本地只保留 upload key，密钥泄露影响面从「全量用户」降到「上传通道」。

**验证**：
```powershell
Test-Path campus-network-service\android-signing\keystore-credentials.txt   # 预期 False
Get-ChildItem -Force campus-network-service\android-signing | Select-Object Name
# 预期：.gitignore  README.md  drcom-release.p12
```

**commit**：`security(android): 删除明文签名口令 + 轮换发布密钥 + 目录自保护`

---

### T0.5 仓库根 `.gitignore` ⏱5min

**作用域**：新建 `campus-network-service/.gitignore`（**仓库根**，注意不是 `android/` 下）

> 背景：`campus-network-service/` **目前不是 git 仓库**（`git rev-parse` → `fatal: not a git repository`）。私钥与口令目前未被跟踪**纯属巧合**。此任务把巧合变成约束。

```gitignore
# ===== 凭据与签名密钥（绝不入库）=====
android-signing/
*.p12
*.jks
*credentials*
keystore*

# ===== 构建产物与日志 =====
**/build.log
**/output/
*.exe
*.zip

# ===== 归档区第三方二进制 =====
_archive/**/tools/
```

**不要**在这里重复 `android/.gitignore` 已有的规则（`build/`、`*.apk` 等），避免规则冲突难排查。

> 若 `campus-network-service/` 至今仍非 git 仓库，**建议**在 T0.5 之后执行 `git init` 并首次提交（`git add` 逐个文件，**不要 `git add .`**），使 `.gitignore` 真正生效。这一步会改变仓库结构，**执行前先向用户确认**。

**commit**（若已 init）：`chore(gitignore): 仓库根忽略签名密钥、p12、build.log、output/`

---

### T0.6 `PortalLoginActivity` 收口：release `exported=false` + 停止信任凭据 extras ⏱45min

> **本任务已按 §1 的 F3/F4 重写。不要照 v1 的做法直接改 false 就完事。**

**作用域**（3 个文件）：

| 文件 | 动作 |
| --- | --- |
| `android/app/src/main/AndroidManifest.xml` | `PortalLoginActivity` 回 `exported="false"` |
| `android/app/src/debug/AndroidManifest.xml` | **新建**，debug 变体覆盖为 `true` |
| `android/app/src/main/java/com/drcom/autologin/PortalLoginActivity.kt` | 凭据不再从 Intent 取 |

**改动 1 —— main manifest**（[:37-42](campus-network-service/android/app/src/main/AndroidManifest.xml#L37)）：
```xml
<activity
    android:name=".PortalLoginActivity"
    android:exported="false"
    android:excludeFromRecents="true"
    android:noHistory="true"
    android:theme="@style/Theme.Transparent" />
```

**改动 2 —— 新建 `app/src/debug/AndroidManifest.xml`**（保住 `adb shell am start` 调试能力，即 F4 的真实诉求）：
```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools">
    <!-- 仅供 debug 构建：允许 adb shell am start 显式启动，便于真机排障。
         release 构建不合入本文件，保持 exported=false。 -->
    <application>
        <activity
            android:name=".PortalLoginActivity"
            android:exported="true"
            tools:replace="android:exported" />
    </application>
</manifest>
```

**改动 3 —— `PortalLoginActivity.kt` 停止信任凭据 extras**

`buildIntent()`（[:355-373](campus-network-service/android/app/src/main/java/com/drcom/autologin/PortalLoginActivity.kt#L355)）目前的 extras 分两类：

| 类别 | extras | 处置 |
| --- | --- | --- |
| **凭据类**（敏感） | `EXTRA_HOST` `EXTRA_PORT` `EXTRA_ACCOUNT` `EXTRA_SUFFIX` `EXTRA_PASSWORD` | **不再通过 Intent 传**。Activity 自己 `Prefs.load(this)` 取（它本来就已经在用 `cfg`，见 [:62-70](campus-network-service/android/app/src/main/java/com/drcom/autologin/PortalLoginActivity.kt#L62)） |
| **网关运行时参数**（非敏感，探测得到） | `EXTRA_USER_IP` `EXTRA_USER_MAC` `EXTRA_AC_IP` `EXTRA_AC_NAME` | **保留**（这是运行期发现的值，无法从 Prefs 得到） |

具体：`onCreate` 里
```kotlin
host     = extras.getStringExtra(EXTRA_HOST)?.trim().orEmpty().ifEmpty { cfg.host }   // ← 删掉整行
```
改为直接用 `cfg`：
```kotlin
val cfg = Prefs.load(this)
host     = cfg.host
port     = cfg.port
account  = cfg.account
suffix   = cfg.suffix
password = cfg.password
userIp   = extras.getStringExtra(EXTRA_USER_IP)?.trim().orEmpty()
userMac  = extras.getStringExtra(EXTRA_USER_MAC)?.trim().orEmpty()
acIp     = extras.getStringExtra(EXTRA_AC_IP)?.trim().orEmpty()
acName   = extras.getStringExtra(EXTRA_AC_NAME)?.trim().orEmpty()
```
`buildIntent()` 相应删掉 5 个 `.putExtra(EXTRA_HOST/PORT/ACCOUNT/SUFFIX/PASSWORD, ...)`，并删掉不再使用的 5 个常量。

**改动 4 —— 注入前校验 host**（[:222](campus-network-service/android/app/src/main/java/com/drcom/autologin/PortalLoginActivity.kt#L222) 附近）

当前 `onPageDone` 只判断 URL 含 `a79.htm` 就注入密码。改为**同时**校验 host 一致（防被重定向到攻击者页面后仍注入真实密码 —— 这是 B-H3 的直接利用点）：
```kotlin
if (url == null || !url.startsWith("http://$host")) {
    Log.w(TAG, "WebView 跳转到非预期域，拒绝注入密码: $url")
    return
}
```

**验证**：
```powershell
Select-String -Path campus-network-service\android\app\src\main\AndroidManifest.xml -Pattern 'PortalLoginActivity' -Context 0,6
# 预期 exported="false"
Test-Path campus-network-service\android\app\src\debug\AndroidManifest.xml   # 预期 True
Select-String -Path campus-network-service\android\app\src\main\java\com\drcom\autologin\PortalLoginActivity.kt -Pattern 'EXTRA_PASSWORD|EXTRA_ACCOUNT'
# 预期：仅剩常量定义处（或已删除），onCreate/buildIntent 中无引用
```

**commit**：`security(android): release 收口 exported=false（debug 变体保留 adb 调试）+ 凭据不再走 Intent + 注入前校验 host`

---

### Phase 0 验收

```powershell
cd D:\Student_Workstation\Using_Workstation

# 1. AST 必须 0 未定义名
cd DrcomAutoLogin-Windows
& "C:\Program Files\Python314\python.exe" ..\_verify_names.py web_api.py 联网_service.py protocol.py auto_update.py eula.py
# 预期：web_api.py (0)（无）；其余模块 (0)

# 2. 语法编译
& "C:\Program Files\Python314\python.exe" -m py_compile web_api.py protocol.py auto_update.py eula.py version.py 联网_service.py
# 预期：无输出（成功）

# 3. fail-closed 生效
& "C:\Program Files\Python314\python.exe" -c "import pathlib; s=pathlib.Path('auto_update.py').read_text(encoding='utf-8'); print('OK' if 'if not digest:' in s else 'FAIL')"

# 4. 凭据文件已清
Test-Path ..\campus-network-service\android-signing\keystore-credentials.txt   # False

# 5. Android 清单
Select-String -Path ..\campus-network-service\android\app\src\main\AndroidManifest.xml -Pattern 'exported'
```

Phase 0 全绿后**先提交再进 Phase 1**。

---

## 4. Phase 1：Windows 安全收口（2 天）

> 与 Phase 2 并行。三个子任务互相独立。

### T1.1 Web UI token 鉴权 + Host/Origin 校验 ⏱4h

**作用域**：`联网_service.py`（token 生成）、`web_api.py`（`_Handler` 校验）、`install.bat`（快捷方式）

**背景**：当前全项目 grep `Authorization|token|cookie|session|csrf|Origin|Referer|Sec-Fetch` **零命中**。唯一防线是绑 `127.0.0.1`，但浏览器同机上下文（CSRF / DNS rebinding）可以绕过。

**步骤 1 —— 启动生成 token**（`联网_service.py` 的 `main()`，启动 `ThreadingHTTPServer` 之前）：
```python
import hmac, secrets
SESSION_TOKEN = secrets.token_urlsafe(32)
_TOKEN_FILE = os.path.join(BASE_DIR, ".session_token")
try:
    with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(SESSION_TOKEN)
    if os.name == "nt":
        subprocess.run(["icacls", _TOKEN_FILE, "/inheritance:r",
                        "/grant:r", "*S-1-5-18:(R)"],
                       check=False, capture_output=True)
except OSError:
    logger.warning("无法写 .session_token，Web UI 鉴权降级")
```
并把 `SESSION_TOKEN` 通过 `web_api._attach(session_token=...)` 注入（新增参数）。

**步骤 2 —— `_Handler` 入口校验**（`web_api.py`）：
```python
def _check_request(self):
    """Host → Origin → Token 三重校验。返回 True=放行。"""
    port = self.server.server_address[1]
    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}

    # 1) Host 白名单（挡 DNS rebinding —— 这是最关键的一步）
    if self.headers.get("Host", "") not in allowed_hosts:
        self.send_response(400); self.send_header("Content-Length", "0"); self.end_headers()
        return False

    # 2) 静态资源与健康检查放行
    path = self.path.split("?", 1)[0]
    if path in ("/", "/index.html", "/api/health"):
        return True

    # 3) 写接口：必须带自定义头（触发 CORS 预检）+ Origin 同源
    if self.command == "POST":
        if self.headers.get("X-Requested-With") != "DrcomUI":
            self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers()
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port):
            self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers()
            return False

    # 4) Token（常数时间比较）
    expected = _SESSION_TOKEN
    if not expected:
        return True          # 兜底：未生成 token 时不锁死（保持旧行为）
    got = self._extract_token()
    if not hmac.compare_digest(got, expected):
        self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers()
        return False
    return True

def _extract_token(self):
    qs = self.path.split("?", 1)[1] if "?" in self.path else ""
    for kv in qs.split("&"):
        if kv.startswith("token="):
            return kv[6:]
    for part in self.headers.get("Cookie", "").split(";"):
        p = part.strip()
        if p.startswith("token="):
            return p[6:]
    return ""
```
在 `do_GET` / `do_POST` 的**第一行**调用 `if not self._check_request(): return`。

**步骤 3 —— 前端配合**（`web_api.py` 内嵌 JS，约 [:1250-2652](DrcomAutoLogin-Windows/web_api.py#L1250)）：
- 所有 `fetch(...)` 加 `credentials: "include"` 与 `headers: {"X-Requested-With": "DrcomUI", "Content-Type": "application/json"}`
- 页面加载时读 `location.search` 里的 token 并写 Cookie（`document.cookie = "token=...; SameSite=Strict; path=/"`）

**步骤 4 —— 快捷方式带 token**（`install.bat:182` 附近）
- 桌面/开始菜单 `.url` 改为指向 `启动UI.bat`
- 新建 `启动UI.bat`：读 `<脚本目录>\.session_token` 后 `start "" "http://127.0.0.1:8848/?token=%TOKEN%"`
- 记得 `setup.iss` 的 `[Files]` 段加这个 bat

**验证**（服务运行中）：
```powershell
# 无 token → 401
Invoke-WebRequest http://127.0.0.1:8848/api/status -UseBasicParsing -ErrorAction SilentlyContinue | Select-Object StatusCode
# 带 token → 200
$t = Get-Content "$env:ProgramFiles\DrcomAutoLogin\.session_token" -Raw
(Invoke-WebRequest "http://127.0.0.1:8848/api/status?token=$t" -UseBasicParsing).StatusCode
# Host 伪造 → 400
$r = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:8848/api/status"); $r.Host="evil.com"
try { $r.GetResponse() } catch { $_.Exception.Response.StatusCode.value__ }
```

**commit**：`security(web_api): token 鉴权 + Host/Origin 校验 + 写接口强制自定义头`

---

### T1.2 升级信任链：校验元数据与下载通道分离 ⏱3h

**作用域**：`auto_update.py`

**问题**（`auto_update.py:56-67`）：`GITHUB_API_MIRRORS` 与 `GITHUB_DOWNLOAD_MIRRORS` **内容完全相同**（都含 `gh-proxy.com` / `ghfast.top` / `mirror.ghproxy.com`）。镜像可同时伪造 releases JSON、asset URL 和 `digest` ⇒ **SHA256 校验必然通过**（校验值与被校验对象同源）。

**改动**：
1. `_check_github_latest()` **只用主源 `api.github.com`**，删除镜像 fallback。失败就返回 `None`（宁可不升级，不可被投毒）。
2. `_download_installer()` **保留镜像 fallback**（下载走镜像没问题），但 digest 一律来自步骤 1 的主源结果。
3. `_check_github_latest` 里对 `tag_name` 做白名单校验（防路径穿越，见 T1.3）。

**验证**：单测（Phase 3 补）mock 主源不可达 → 断言 `_check_github_latest()` 返回 `None` 且**不尝试镜像**。

**commit**：`security(auto_update): digest 只信 api.github.com 主源，镜像仅用于下载`

---

### T1.3 静默升级默认关闭 + 版本串白名单 ⏱1h

**作用域**：`auto_update.py`（`_default_config`）、`config.json.template`

**改动 1**：`"auto_update_enabled": True` → `False`
> 理由：当前升级链路既不可用（`/releases/latest` 不返回 prerelease + tag 名 `installer` 无法解析版本）也不安全（T1.2）。在 T1.2 + T4.2 修好之前默认关闭。

**改动 2**：`_do_update_now` 里对 `remote_ver` 加白名单（当前 `tag_name.lstrip("vV")` 后直接拼进 `%TEMP%` 文件名，含 `..\` 可穿越）：
```python
if not re.fullmatch(r"[0-9A-Za-z._-]{1,32}", remote_ver):
    msg = "远端版本串非法：{!r}".format(remote_ver)
    _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
    _log_upgrade("ERROR", msg)
    return {"ok": False, "error": msg}
```

**commit**：`security(auto_update): 静默升级默认关闭 + 远端版本串白名单校验`

---

### T1.4 `/api/config/export` 不再打包密码 ⏱15min

**作用域**：`web_api.py:411-443`

**改动**：删除 `zf.writestr("password.txt", ...)`（[:426-429](DrcomAutoLogin-Windows/web_api.py#L426)），改为在 `manifest` 里记状态：
```python
pwd_path = os.path.join(BASE_DIR, "password.txt")
manifest["password_status"] = (
    "set" if os.path.isfile(pwd_path) and os.path.getsize(pwd_path) > 0 else "missing"
)
```
> 这样 README「接口不返回密码」的承诺才成立（当前被此端点直接证伪）。

**验证**：下载 zip 后 `python -c "import zipfile;print(zipfile.ZipFile('config-export-xxx.zip').namelist())"` → 不含 `password.txt`。

**commit**：`security(web_api): /api/config/export 不再打包明文密码`

---

## 5. Phase 2：Android 收口（1.5 天，与 Phase 1 并行）

> T0.4 / T0.5 / T0.6 已在 Phase 0 完成。这里做剩余项。

### T2.1 密码走 `EncryptedSharedPreferences` ⏱1h

**作用域**：`Prefs.kt`、`app/build.gradle.kts`

**现状**：[`Prefs.kt:22,34-35,63`](campus-network-service/android/app/src/main/java/com/drcom/autologin/Prefs.kt#L22) 用 `getSharedPreferences(FILE, MODE_PRIVATE)` + `.putString("password", ...)` 明文存储。

**改动**：
```kotlin
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

private fun sp(ctx: Context): SharedPreferences {
    val key = MasterKey.Builder(ctx, "drcom_master_key")
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()
    return EncryptedSharedPreferences.create(
        ctx, FILE, key,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )
}
```
`build.gradle.kts` dependencies 追加（**I8 的批准例外**）：
```kotlin
implementation("androidx.security:security-crypto:1.1.0-alpha06")
```

**⚠️ 迁移注意**：旧明文 `SharedPreferences` 里的值需要**一次性迁移**后再清除，否则老用户升级后密码丢失。`EncryptedSharedPreferences.create()` 对同名旧文件会抛异常，因此**必须换文件名**（如 `drcom_prefs_v2`），并在首次读取时从旧文件迁移。

**验证**：`adb shell run-as com.drcom.autologin cat shared_prefs/*.xml` → 密码字段为密文（Base64 乱码）。

**commit**：`security(android): 密码改 EncryptedSharedPreferences 存储 + 旧数据迁移`

---

### T2.2 `networkSecurityConfig` 收敛明文流量 ⏱1h

**作用域**：新建 `app/src/main/res/xml/network_security_config.xml`、`AndroidManifest.xml`

**现状**：[`AndroidManifest.xml:24`](campus-network-service/android/app/src/main/AndroidManifest.xml#L24) `usesCleartextTraffic="true"` **全局放行**。

**改动 1** —— 新建 `res/xml/network_security_config.xml`：
```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <!-- 默认禁止明文 -->
    <base-config cleartextTrafficPermitted="false">
        <trust-anchors><certificates src="system" /></trust-anchors>
    </base-config>
    <!-- 仅校园网内网段允许明文（认证协议本身是 http） -->
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">172.16.80.2</domain>
        <domain includeSubdomains="true">172.16.80.3</domain>
    </domain-config>
</network-security-config>
```

**改动 2** —— `AndroidManifest.xml` 的 `<application>`：删 `android:usesCleartextTraffic="true"`，加 `android:networkSecurityConfig="@xml/network_security_config"`。

**⚠️ 关键约束**：`LoginEngine.kt` 的连通性探测列表（[:430-445](campus-network-service/android/app/src/main/java/com/drcom/autologin/LoginEngine.kt#L430)）包含 **12 个公网 HTTP 地址**（`connect.rom.miui.com`、`www.baidu.com`、`1.1.1.1` 等）。全局禁明文会**打断这些探测**。
**两种处理**（二选一，推荐 A）：
- **A**：把这些探测 URL 改为 **HTTPS**（`https://connect.rom.miui.com/generate_204` 等大多支持 https）
- **B**：在 `domain-config` 里逐个放行这 12 个域名（不推荐，攻击面仍大）

**验证**：`adb shell am start` 后观察连通性探测仍返回预期状态。

**commit**：`security(android): networkSecurityConfig 收敛 cleartext + 探测地址改 HTTPS`

---

### T2.3 WebView 加固 ⏱1h

**作用域**：`PortalLoginActivity.kt`（WebView 配置段 [:162-190](campus-network-service/android/app/src/main/java/com/drcom/autologin/PortalLoginActivity.kt#L162)）

**改动**（只加严，不动业务逻辑）：
```kotlin
w.settings.apply {
    javaScriptEnabled = true                 // 必需：门户 JS 登录（F1）
    domStorageEnabled = true                 // 必需：门户依赖
    setSupportZoom(false)
    allowFileAccess = false                  // 新增
    allowContentAccess = false               // 新增
    setGeolocationEnabled(false)             // 新增
    mediaPlaybackRequiresUserGesture = true  // 新增
    mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW  // 新增
}
w.setAcceptThirdPartyCookies(w, false)       // 从 true 改 false
w.webViewClient = object : WebViewClient() {
    override fun shouldOverrideUrlLoading(v: WebView?, req: WebResourceRequest?): Boolean {
        val u = req?.url?.toString() ?: return true
        return !u.startsWith("http://$host")      // 非本网关的跳转一律拦截
    }
}
```

**⚠️ 不要**把 `javaScriptEnabled` 关掉 —— 那会直接破坏 F1 的唯一可行登录路径。`setAcceptThirdPartyCookies` 改 `false` 前需**实测门户登录仍成功**（门户可能依赖第三方 Cookie）。

**验证**：真机跑一次「立即登录」，确认 WebView 路径仍能成功（这是 F1 的回归测试，**必做**）。

**commit**：`security(android): WebView 配置收紧 + 跳转域白名单`

---

### T2.4 日志脱敏（**保留日志能力**）⏱1h

> **按 §0 更正 3：不删日志，只脱敏 + 按构建类型门禁。**

**作用域**：`LogStore.kt`、`LoginEngine.kt`、`PortalLoginActivity.kt`、`MainActivity.kt`、`proguard-rules.pro`

**改动 1 —— 脱敏工具**（加到 `LogStore.kt`）：
```kotlin
/** 账号脱敏：保留前 3 后 2。20231234567 -> 202*****67 */
fun maskAccount(s: String): String =
    if (s.length <= 5) "*".repeat(s.length)
    else s.take(3) + "*".repeat(s.length - 5) + s.takeLast(2)

/** IP 脱敏：保留前两段。172.16.80.3 -> 172.16.*.* */
fun maskIp(s: String): String =
    s.split(".").take(2).joinToString(".") + (if (s.contains(".")) ".*.*" else "")

/** MAC 脱敏：保留前 4 位。E25B367B8DAC -> E25B******** */
fun maskMac(s: String): String = if (s.length <= 4) "****" else s.take(4) + "*".repeat(s.length - 4)
```

**改动 2 —— 替换敏感输出点**（共 4 处，见下表），其余 100+ 处日志**保持不动**：

| 位置 | 现状 | 改为 |
| --- | --- | --- |
| [`PortalLoginActivity.kt:73`](campus-network-service/android/app/src/main/java/com/drcom/autologin/PortalLoginActivity.kt#L73) | `账号=$account$suffix userIp=$userIp userMac=$userMac` | `账号=${maskAccount(account)}$suffix userIp=${maskIp(userIp)} userMac=${maskMac(userMac)}` |
| [`PortalLoginActivity.kt:72`](campus-network-service/android/app/src/main/java/com/drcom/autologin/PortalLoginActivity.kt#L72) | `账号=$account$suffix`（LogStore） | 同上脱敏 |
| [`LoginWorker.kt:88`](campus-network-service/android/app/src/main/java/com/drcom/autologin/LoginWorker.kt#L88) | `userMac=$userMac` | `userMac=${maskMac(userMac)}` |
| [`LoginEngine.kt:600`](campus-network-service/android/app/src/main/java/com/drcom/autologin/LoginEngine.kt#L600) | `探测网关 http://${cfg.host}` | 保留（内网 IP 非敏感，且排障必需） |

**改动 3 —— `Log.v/d` 构建门禁**（`LogStore.kt` 的 `android.util.Log` dispatch 处）：
```kotlin
"DEBUG" -> if (BuildConfig.DEBUG) Log.d(LOG_TAG, msg)
"INFO"  -> Log.i(LOG_TAG, msg)      // release 保留：排障必需
"WARN"  -> Log.w(LOG_TAG, msg)
"ERROR" -> Log.e(LOG_TAG, msg)
```

**改动 4 —— `proguard-rules.pro`**（release 自动剔除 verbose/debug）：
```proguard
-assumenosideeffects class android.util.Log {
    public static int v(...);
    public static int d(...);
}
```

**验证**：
- `adb logcat -s DrcomAutoLogin:V | findstr 账号` → 只出现脱敏串
- release 包 `adb logcat -s DrcomAutoLogin:V` → 无 `D/` 行

**commit**：`security(android): 日志账号/IP/MAC 脱敏 + debug 级别构建门禁（保留 INFO+ 排障能力）`

---

### T2.5 架构与健壮性修复 ⏱2h

| # | 位置 | 改动 |
| --- | --- | --- |
| 1 | [`KeepAliveService.kt:28-37`](campus-network-service/android/app/src/main/java/com/drcom/autologin/KeepAliveService.kt#L28) | `startForeground` 失败 catch 内加 `stopSelf()`（否则稍后抛 `ForegroundServiceDidNotStartInTimeException` 崩溃） |
| 2 | [`LoginWorker.kt:54`](campus-network-service/android/app/src/main/java/com/drcom/autologin/LoginWorker.kt#L54) | 恒 `Result.success()` → 失败返回 `Result.retry()` |
| 3 | [`Scheduler.kt:36`](campus-network-service/android/app/src/main/java/com/drcom/autologin/Scheduler.kt#L36) | `ensurePeriodic` 的 `ExistingPeriodicWorkPolicy.UPDATE` → `KEEP`（当前每次打开 App 都重置周期计时） |
| 4 | [`MainActivity.kt:351-363`](campus-network-service/android/app/src/main/java/com/drcom/autologin/MainActivity.kt#L351) | `onRequestPermissionsResult` 只处理 `REQ_LOCATION`，补 `POST_NOTIFICATIONS` 被拒的提示 |
| 5 | [`LoginEngine.kt:697-701`](campus-network-service/android/app/src/main/java/com/drcom/autologin/LoginEngine.kt#L697) | `enc()` 失败返回未编码串会拼坏 URL → 改为返回 null 并中止本次登录 |
| 6 | [`PortalLoginActivity.kt:120-151`](campus-network-service/android/app/src/main/java/com/drcom/autologin/PortalLoginActivity.kt#L120) | 后台线程裸写 `userIp` 等字段 → 加 `@Volatile` 或用 `runOnUiThread` 回写 |

**commit**：`fix(android): 前台服务失败自停 + Worker 重试 + 周期任务 KEEP + 权限回调 + 编码失败中止`

---

### T2.6 CI workflow 挪到仓库根 ⏱30min

**作用域**：`campus-network-service/android/.github/workflows/build-apk.yml` → `campus-network-service/.github/workflows/build-apk.yml`

> **背景**：GitHub Actions **只识别仓库根的 `.github/workflows/`**。当前文件在 `android/` 子目录下，在 monorepo 布局下**永不触发**。而它看起来是一整套发布流水线（含 apksigner 签名），团队很可能以为 CI 在跑。

**改动**：
1. 移动文件到 `campus-network-service/.github/workflows/`
2. 文件内所有 `cd android` 路径相应调整（`working-directory: android`）
3. `paths` 过滤加上 `android/**`
4. 修 [`build-apk.yml:141`](campus-network-service/android/.github/workflows/build-apk.yml#L141) 硬编码的 `"1.1 (versionCode 2)"` → 从 gradle 动态读
5. 修 [:30](campus-network-service/android/.github/workflows/build-apk.yml#L30) `echo "${{ secrets... }}"` **内联 secret**（会进日志）→ 改为写入 `$GITHUB_ENV` 或 `local.properties`
6. `permissions: contents: write` → 按需最小化

**验证**：push 后在 GitHub Actions 页面确认 workflow 出现。

**commit**：`ci(android): workflow 移到仓库根 + 移除 secret 内联 + 版本动态读取`

---

## 6. Phase 3：工程化底座（2 天）

> **依赖**：Phase 0/1/2 全部完成（否则测的是没改完的代码）。

### T3.1 Windows：pytest + ruff + CI 门禁 ⏱1d

**新建 `DrcomAutoLogin-Windows/pyproject.toml`**：
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q"

[tool.ruff]
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
```

**新建 `tests/test_pure_functions.py`**（覆盖 6 个纯函数）：
```python
import sys, os, hashlib, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

def test_parse_version():
    from auto_update import _parse_version
    assert _parse_version("1.0") == (1, 0)
    assert _parse_version("v1.2.3") == (1, 2, 3)
    assert _parse_version("2.0.2.3") == (2, 0, 2, 3)

def test_compare_versions_none_on_garbage():
    from auto_update import _compare_versions
    assert _compare_versions("installer", "2.0.2.3") is None
    assert _compare_versions("2.0.2.3", "2.0.2.3") == 0

def test_verify_sha256(tmp_path):
    from auto_update import _verify_sha256
    p = tmp_path / "x.exe"; p.write_bytes(b"hello")
    assert _verify_sha256(str(p), hashlib.sha256(b"hello").hexdigest()) is True
    assert _verify_sha256(str(p), "0" * 64) is False

def test_password_skips_comment(tmp_path, monkeypatch):
    import importlib
    m = importlib.import_module("联网_service")
    f = tmp_path / "password.txt"
    f.write_text("# 注释\nrealpass\n", encoding="utf-8")
    monkeypatch.setattr(m, "PASSWORD_FILE", str(f))
    assert m._load_password_from_disk() == "realpass"

def test_validate_config_rejects_empty_account():
    import importlib
    m = importlib.import_module("联网_service")
    errs = m._validate_config({**m.DEFAULT_CONFIG, "account": ""})
    assert any("account" in e for e in errs)
```

**新建 `tests/test_web_api_smoke.py`**（**这条专治 T0.1 那类 bug**）：
```python
import sys, pathlib, types, importlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

def _attach_web_api():
    import web_api
    noop = lambda *a, **k: None
    log = types.SimpleNamespace(info=noop, warning=noop, error=noop, debug=noop, exception=noop)
    web_api._attach(
        logger=log, run_lock=types.SimpleNamespace(), base_dir=".",
        log_file="x.log", log_dir=".",
        config_file="config.json", password_file="password.txt", upgrade_log_file="u.log",
        default_config={}, state={}, state_lock=types.SimpleNamespace(),
        pwd_lock=types.SimpleNamespace(), allowed_suffixes=set(),
        allowed_intervals=set(), allowed_update_intervals=set(),
        load_config=lambda: {}, save_config=noop, save_password_to_disk=noop,
        validate_config=lambda c: [], snapshot_state=lambda: {}, get_password=lambda: None,
        now_iso=lambda: "", stop_event=types.SimpleNamespace(), run_once_fn=noop,
        eula_api_get_changelog=lambda: "", auto_update_mod=None,
    )
    return web_api

def test_every_api_symbol_exists():
    """T0.1 的 6 个 NameError 全靠这条兜住。"""
    wa = _attach_web_api()
    for name in ("api_get_status","api_get_config","api_get_log_tail","api_get_about",
                 "api_get_changelog","api_get_update_status","api_post_password",
                 "api_post_login","api_post_restart","api_post_update_check",
                 "api_post_update_install","api_post_update_toggle",
                 "api_get_update_history","api_get_config_export","api_post_config_import"):
        assert callable(getattr(wa, name, None)), f"{name} 缺失或不可调用"
```

**新建 `tests/test_no_undefined_names.py`**（把静态检查搬进 pytest）：
```python
import subprocess, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
VERIFY = ROOT.parent / "_verify_names.py"

def test_no_undefined_bare_names():
    r = subprocess.run(
        [sys.executable, str(VERIFY), "web_api.py", "protocol.py", "auto_update.py", "eula.py"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    assert "(0)" in r.stdout, f"存在未定义裸名:\n{r.stdout}"
```

**改 `.github/workflows/build-installer.yml`** —— 在 `build` job 前插 `test` job，并让 `build` 依赖它：
```yaml
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install pytest ruff
      - run: python -m compileall -q .
      - run: ruff check .
      - run: pytest -v
  build:
    needs: test
    # ...（原有步骤不动）
```

**验证**：
```powershell
cd DrcomAutoLogin-Windows
& "C:\Program Files\Python314\python.exe" -m pip install pytest ruff
& "C:\Program Files\Python314\python.exe" -m pytest -v      # 全绿
& "C:\Program Files\Python314\python.exe" -m ruff check .   # 无错
```

**commit**：`test: 引入 pytest + ruff + 静态检查门禁 + CI test job`

---

### T3.2 Android：JUnit + lint ⏱半天

**`app/build.gradle.kts` dependencies 追加**：
```kotlin
testImplementation("junit:junit:4.13.2")
```

**新建 `app/src/test/java/com/drcom/autologin/LoginEngineTest.kt`** —— 测纯函数 `hex16ToIp` / `jsEscape` / `dashMac` / `extractJson`。**这些函数正是 F5 参数取值链的核心**，最值得测。

**新建 `app/src/test/java/com/drcom/autologin/ProtocolParityTest.kt`** —— 把 `a41.js:1183-1188` 的降级链做成**表驱动测试**，锁死 F5：
```kotlin
// IP: v46ip -> ss5 -> v4ip -> hex16ToIp(ss3) -> 本机网卡
// MAC: ss4 -> olmac -> 000000000000
```
> 这样以后任何人改取值顺序都会被测试拦住 —— 这正是 F5 那个坑的防复发措施。

**`proguard-rules.pro`** 补 WebView JS 接口 keep 规则；`build.gradle.kts:49` `isMinifyEnabled = true`。

**⚠️ R8 风险**：开启混淆后**必须真机跑一次 WebView 登录**。R8 最易打断 `evaluateJavascript` 里的字符串拼接。若登录失败，检查 keep 规则。

**验证**：`cd android && ./gradlew testDebugUnitTest`（无 wrapper，需本机 gradle；建议同时补 `gradle wrapper` 使构建可复现）

**commit**：`test(android): 补参数取值链表驱动单测 + R8 混淆`

---

## 7. Phase 4：收敛（1.5 天）

### T4.1 版本号单一来源 ⏱3h

**Windows**（当前 8 套口径，`version.py` 是唯一真源）：

| 文件 | 当前 | 改为 |
| --- | --- | --- |
| [`version.py:5`](DrcomAutoLogin-Windows/version.py#L5) docstring | 「版本字符串仍为 v2.0.1」 | 删除该行（与 `VERSION` 矛盾） |
| [`packaging/build.ps1:26`](DrcomAutoLogin-Windows/packaging/build.ps1#L26) | `$AppVersionText = 'v2.0.1'` | `$AppVersionText = (& python -c "import version,sys;sys.stdout.write('v'+version.VERSION)")` |
| [`install.bat:6,37,156`](DrcomAutoLogin-Windows/install.bat#L6) | `v2.0.1` | 从 `version.py` 读（bat 里用 `for /f` 调 python） |
| [`uninstall.bat:6,31`](DrcomAutoLogin-Windows/uninstall.bat#L6) | `v2.0.1` | 同上（或干脆去掉版本字样） |
| [`packaging/setup.iss:3,5`](DrcomAutoLogin-Windows/packaging/setup.iss#L3) 头注释 | `v2.0.1` | 删除硬编码，注释指向 `version.py` |
| [`README.md:94,409-413`](DrcomAutoLogin-Windows/README.md#L94) | `v2.0.1` / 「当前版本为 2.0.0」/「不再存在多套并存的编号」 | 统一为 `version.py` 的值，删掉自相矛盾的句子 |
| [`packaging/README.md:9,21`](DrcomAutoLogin-Windows/packaging/README.md#L9) | `v2.0.1` | 改为引用 |

**CI 加一致性断言**（build-installer.yml）：
```yaml
- name: Assert version consistency
  shell: pwsh
  run: |
    $v = (& python -c "import version;print(version.VERSION)").Trim()
    Write-Host "version.py = $v"
    $bad = Select-String -Path install.bat,uninstall.bat -Pattern 'v2\.0\.[0-9]'
    if ($bad) { throw "install/uninstall.bat 仍有硬编码版本: $bad" }
```

**Android**（真源 = `app/build.gradle.kts`）：

| 文件 | 当前 | 改为 |
| --- | --- | --- |
| [`MainActivity.kt:45`](campus-network-service/android/app/src/main/java/com/drcom/autologin/MainActivity.kt#L45) | `"启动 (v1.6.1-debug)"` | `"启动 (${BuildConfig.VERSION_NAME}-${if (BuildConfig.DEBUG) "debug" else "release"})"` |
| [`PortalLoginActivity.kt:58`](campus-network-service/android/app/src/main/java/com/drcom/autologin/PortalLoginActivity.kt#L58) | 同上 | 同上 |
| `build-apk.yml:141` | `"1.1 (versionCode 2)"` | 动态读取 |

`build.gradle.kts` 加 `buildConfigField` 并开启 `buildFeatures.buildConfig = true`。

**commit**：`refactor(version): 版本号单一来源收敛（version.py / build.gradle.kts）`

---

### T4.2 文档一致性 ⏱半天

**Windows `README.md`** —— 修正以下**已确证的文档-实现矛盾**（每条都有源码证据）：

| README 位置 | 声称 | 实际 |
| --- | --- | --- |
| :119, :233-253 | 「`联网_service.py` 单文件主程序」 | 已拆 6 模块，目录结构缺 `version/protocol/eula/web_api/auto_update` |
| :152, :169 | 「网关不可达→静默返回，不计失败、不拉长退避」 | [`protocol.py:298-301`](DrcomAutoLogin-Windows/protocol.py#L298) 明确调用 `_set_backoff()` —— **离线静默未实现** |
| :168 | 「实际等待 `max(自检间隔, 剩余退避)`」 | 代码已是 `min`（[`联网_service.py:437-449`](DrcomAutoLogin-Windows/联网_service.py#L437)） |
| :320-331 | 配置表 | 缺 `auto_update_enabled` / `update_check_interval_hours` / `update_min_free_disk_mb` 三个字段 |
| :364 | 「接口不返回密码」 | 修完 T1.4 后成立；修完前是错的 |
| :397-413 | 版本记录缺 v2.0.2 / .1 / .2 / .3 四版 | 补全 |

**Android 侧**：

| 文件 | 问题 | 动作 |
| --- | --- | --- |
| [`campus-network-service/README.md`](campus-network-service/README.md) | 通篇讲项目 A 的 `install.bat` / `config.json` / `tools\nssm.exe`，而这些**在项目 B 根目录根本不存在** | **重写**为「Android 端 + 协议逆向样本」定位 + 指向 `DrcomAutoLogin-Windows` |
| [`android/README.md:77,299-300`](campus-network-service/android/README.md#L299) | 「密钥材料只存在于 GitHub Secrets，仓库内不含任何密钥文件」 | 被 `android-signing/` 直接证伪；删掉或落实 |
| [`android/README.md:101-113`](campus-network-service/android/README.md#L101) | 称「9 个 Kotlin 文件」 | 实际 10 个（漏 `PortalLoginActivity.kt`） |
| [`docs/03-使用与排错.md:330-354`](campus-network-service/android/docs/03-使用与排错.md#L330) | 推荐「两边同时用」已归档的 `联网_service.py` | 标注已废弃 |

**新建 `campus-network-service/_archive/README.md`**：
```markdown
# 归档：v1.x Windows 单文件版

**状态：已废弃（ARCHIVED），不再维护。**

本目录是 v1.x 时期的 Windows 单文件实现的只读快照，仅供追溯协议实现历史。

- 现行 Windows 版 → `../../DrcomAutoLogin-Windows/`
- 现行 Android 版 → `../android/`
- 协议事实基准 → `../a41.js`（只读，勿修改）
```

**新建 `campus-network-service/docs/协议逆向说明.md`** —— 说明 `a41.js` 的来源、用途（提取 `wlanuserip`/`mac`/`wlanacip` 降级链）、与三端实现的对应关系、以及「仅用于自有账号认证」的合规边界。当前 `a41.js` 内注释是 GBK→UTF-8 乱码且无任何说明文件。

**commit**：`docs: README/CHANGELOG 与实现对齐 + 归档标记 + 协议说明`

---

### T4.3 死代码与重复实现清理 ⏱半天

**Windows 死代码**（经 AST + grep 确认无引用）：

| 位置 | 符号 |
| --- | --- |
| [`web_api.py:146`](DrcomAutoLogin-Windows/web_api.py#L146) | `_read_json_body` |
| [`web_api.py:105`](DrcomAutoLogin-Windows/web_api.py#L105) | `_log` |
| [`eula.py:66`](DrcomAutoLogin-Windows/eula.py#L66) | `read_eula`（自注「未启用」） |
| [`auto_update.py:796`](DrcomAutoLogin-Windows/auto_update.py#L796) | `_log` |
| [`auto_update.py:20`](DrcomAutoLogin-Windows/auto_update.py#L20) | `import datetime as _dt`（自注 `# noqa: F401 备用`） |
| [`auto_update.py:29`](DrcomAutoLogin-Windows/auto_update.py#L29) | `import urllib.parse` |
| [`联网_service.py:31`](DrcomAutoLogin-Windows/联网_service.py#L31) | `import re` |
| [`联网_service.py:66-68`](DrcomAutoLogin-Windows/联网_service.py#L66) | `CONFIG_EXPORT_*` 三常量（与 `web_api.py:37-39` 重复，本模块不用） |
| [`protocol.py:42`](DrcomAutoLogin-Windows/protocol.py#L42) | `_PWD_VALUE`（只注入不使用） |
| [`web_api.py:72`](DrcomAutoLogin-Windows/web_api.py#L72) | `g["BACKOFF"] = state.get` ← **把 bound method 当字典占位**，纯埋雷，删除 |

**重复实现**：`_log` 包装器在 `protocol` / `web_api` / `auto_update` 三份拷贝 → 统一；密码写盘逻辑在 `_save_password_to_disk` 与 `api_post_config_import` 各一份 → 统一走前者。

**⚠️ 删 `web_api.py:72` 前**先确认 `BACKOFF` 在该模块确实无引用（grep `BACKOFF`）；若有引用则改为正确注入。

**commit**：`chore: 清理死代码与重复实现`

---

### T4.4 升级备份与回滚 ⏱2h

**现状**：`_backup_service_py`（[`auto_update.py:356-365`](DrcomAutoLogin-Windows/auto_update.py#L356)）**只复制 `联网_service.py` 一个文件**，而 v2.0.2 已拆成 6 个模块 ⇒ 回滚时另外 5 个模块无法恢复。且备份放 `%TEMP%`（会被磁盘清理删除）。

**改动**：
1. 备份整个应用目录的 6 个 `.py` + `config.json` 到 `BASE_DIR/backup/<version>/`
2. 加 `POST /api/rollback`（走 T1.1 的 token 鉴权）
3. `BACKUP_RETENTION_DAYS = 7` 的自清理覆盖所有备份子目录

**commit**：`fix(auto_update): 备份改为整目录 + 提供回滚端点`

---

### T4.5 协议规格统一 ⏱1d

**目标**：把 F5/F6/F7 从「散落在 3 个实现里的隐式知识」变成「一份规格 + 共享测试」。

1. 新建 `docs/协议规格.md` —— 以 `a41.js:1183-1188, 1206-1207` 为唯一事实基准，写清：
   - 在线检查：`GET http://{host}/drcom/chkstatus?callback=cb&jsVersion=4.X` (端口 80)，JSONP `result==1` 表示在线
   - 登录：`GET http://{host}:801/eportal/portal/login?callback=dr{rand}&login_method=1&...`
   - 参数降级链（IP / MAC / wlan_ac_ip / wlan_ac_name）
   - **终端类型字段名是 `mac_type`，`terminal_type` 不存在**（F6）
   - AC 终端归类不受 HTTP 参数控制 ⇒ WebView 路径必需（F1/F2）
2. `protocol.py` / `LoginEngine.kt` / `_archive/联网_service.py` 各处加注释指向规格
3. 加跨语言一致性测试（Python + Kotlin 用同一组输入输出表）

**commit**：`docs: 建立协议规格单一来源 + 三端引用`

---

## 8. 依赖与执行顺序

```
Phase 0（顺序：T0.5 → T0.4，其余并行）
├─ T0.5 根 .gitignore   ─┐
├─ T0.4 轮换密钥         ─┘ (T0.5 先，避免"已删但未忽略"的窗口)
├─ T0.1 web_api 裸名     ← 独立，最优先（4 个功能当前全挂）
├─ T0.2 fail-closed      ← 独立，1 行
├─ T0.3 密码模板         ← 独立
└─ T0.6 Android 收口     ← 依赖 §1 的 F3/F4 理解

Phase 1（Windows）  ┐
├─ T1.1 token 鉴权    │  依赖 T0.1（_attach 注入面稳定）
├─ T1.2 信任链        │  独立
├─ T1.3 默认关闭      │  依赖 T1.2
└─ T1.4 export 去密码 ┘  独立

Phase 2（Android，与 Phase 1 并行）
├─ T2.1 EncryptedPrefs  独立（注意数据迁移）
├─ T2.2 networkSecurity 依赖 T2.3 一起验证（探测地址改 HTTPS）
├─ T2.3 WebView 加固    必须真机回归（F1 路径）
├─ T2.4 日志脱敏        独立
├─ T2.5 架构修复        独立
└─ T2.6 CI 挪位置       独立

Phase 3（依赖 Phase 0/1/2 全绿）
├─ T3.1 Windows pytest  依赖 T0.1/T0.2/T0.3/T1.x
└─ T3.2 Android 测试    依赖 T2.x

Phase 4（依赖 Phase 3）
└─ T4.1 → T4.2 → T4.3 → T4.4 → T4.5
```

**关键路径**：T0.5 → T0.4 → T0.1 → T1.1 → T3.1 → T4.1

**可并行**：Phase 1 与 Phase 2 完全独立，可两人同时做。

---

## 9. 验收清单

### Phase 0
- [ ] `python ..\_verify_names.py web_api.py ...` → `web_api.py` 报 **0** 个未定义名
- [ ] `python -m py_compile` 6 个模块全通过
- [ ] `auto_update.py` 含 `if not digest:` 且含「拒绝安装」
- [ ] `packaging/password.txt.template` 为空
- [ ] `Test-Path android-signing\keystore-credentials.txt` → **False**
- [ ] `Test-Path android\app\src\debug\AndroidManifest.xml` → **True**
- [ ] main manifest `PortalLoginActivity` 为 `exported="false"`
- [ ] `PortalLoginActivity.kt` 的 `onCreate` 不再读 `EXTRA_PASSWORD`/`EXTRA_ACCOUNT`

### Phase 1
- [ ] 无 token `GET /api/status` → **401**
- [ ] 带 token → **200**
- [ ] `POST /api/restart` 无 `X-Requested-With` → **403**
- [ ] `Host: evil.com` → **400**
- [ ] `config-export-*.zip` 内**无** `password.txt`
- [ ] `_check_github_latest` 中无镜像 fallback
- [ ] 默认配置 `"auto_update_enabled": false`
- [ ] AST 复跑仍 0

### Phase 2
- [ ] main manifest 无 `usesCleartextTraffic="true"`，有 `networkSecurityConfig`
- [ ] **真机回归：WebView 登录仍成功**（F1，最关键的一条）
- [ ] `adb shell run-as ... cat shared_prefs/*.xml` → 密码为密文
- [ ] `adb logcat -s DrcomAutoLogin:V | findstr 账号` → 仅脱敏串
- [ ] release 包 logcat 无 `D/` 行
- [ ] `KeepAliveService` catch 内有 `stopSelf()`
- [ ] `campus-network-service/.github/workflows/build-apk.yml` 存在且旧路径已删

### Phase 3
- [ ] `pytest -v` 全绿
- [ ] `ruff check .` 无输出
- [ ] `build-installer.yml` 的 `build` job 有 `needs: test`
- [ ] `gradlew testDebugUnitTest` 通过（含 `ProtocolParityTest`）

### Phase 4
- [ ] `Select-String -Path *.bat -Pattern 'v2\.0\.[0-9]'` → 0 命中
- [ ] `Select-String -Path android\app\src -Pattern '1\.6\.1-debug'` → 0 命中
- [ ] 死代码 10 处全部删除且 `ruff` 无 F401
- [ ] `_archive/README.md`、`docs/协议逆向说明.md`、`docs/协议规格.md` 存在
- [ ] README 6 处矛盾全部修正

---

## 10. 陷阱与红线

### 10.1 绝对不要做（会破坏产品）

| # | 禁止 | 原因 |
| --- | --- | --- |
| 1 | **移除或降级 WebView 登录路径** | F1/F2：该校唯一可用路径，移除 = 产品不可用 |
| 2 | **关闭 `javaScriptEnabled`** | 门户靠 JS 提交登录 |
| 3 | 在 WebView 路径改 `exported="false"` 后**不做真机验证** | 虽然同 UID 启动不受影响，但必须实测确认 |
| 4 | **修改 `a41.js`** | F5/F6/F7 的事实基准 |
| 5 | 发**未重新签名**的 APK 给存量用户 | 签名变更后无法覆盖安装，必须公告 |
| 6 | 把 `127.0.0.1` 改成 `0.0.0.0` | 安全底线 |
| 7 | 改 NSSM 服务名 / `AppId` / 安装路径 | 破坏存量升级 |
| 8 | `git add .` | 会把签名密钥、产物一起提交 |
| 9 | 把密码/口令写进 commit message、注释、日志、CHANGELOG | 凭据泄露 |
| 10 | 在 `web_api.py` 留调试 endpoint | 攻击面 |

### 10.2 技术陷阱

| # | 陷阱 | 应对 |
| --- | --- | --- |
| 1 | **PowerShell `Get-Content` 读 UTF-8 中文源码显示乱码** | 用 `read` 工具；这不是文件损坏 |
| 2 | **`_attach()` 注入顺序敏感** | `main()` 里 `STOP_EVENT` 等必须在 `_attach` 前建好；不要重排 |
| 3 | **`web_api.py` 2652 行，勿整文覆盖** | 用 `edit` 做最小改动，避免 CRLF/编码事故 |
| 4 | **`.bat` 必须保留 CRLF** | 改动后用 `git diff --stat` 确认行数变化合理 |
| 5 | **Android 无 gradle wrapper** | 构建不可复现；建议补 `gradle wrapper` |
| 6 | **`EncryptedSharedPreferences` 换文件名** | 对同名旧明文文件会抛异常，需迁移逻辑 |
| 7 | **R8 开启后必须真机验证 WebView 注入** | 混淆最易打断 `evaluateJavascript` 字符串拼接 |
| 8 | **`networkSecurityConfig` 全局禁明文会打断 12 个 HTTP 探测** | 先改 HTTPS（见 T2.2） |
| 9 | **T1.1 的 token 兜底分支** | token 生成失败时 `_SESSION_TOKEN` 为空要放行（保持旧行为），不要锁死 |
| 10 | **`git status` 当前两个仓库都干净** | 若发现未提交改动，先 `git diff` 确认是否他人所为 |

### 10.3 不确定时的处理原则

1. 与 §1 的 F1–F8 冲突 → **停下来**，在 CHANGELOG 记录冲突，跳过该任务，不要自行决定
2. 改动超出任务声明的「作用域」→ 先确认是否必要，必要时拆成新任务
3. 真机行为与代码推理不符 → **信真机**（F1 就是这么发现的）
4. 拿不准优先级 → 按 `阻断级 > 安全 > 工程化 > 收敛` 排序
5. **先看 git log**：本项目的设计意图大量写在提交信息里（F1/F3/F5 全是这么找到的）

---

## 11. 附录：本次（v2）新增的验证证据

| 结论 | 验证命令 | 结果 |
| --- | --- | --- |
| `web_api.py` 仍有 6 个未定义名 | `python ..\_verify_names.py web_api.py` | ✅ 6 个（L320/356/368/371/391/402） |
| 两仓库工作区干净 | `git -C <repo> status --short` | ✅ 无输出 |
| Android 最近提交 | `git -C campus-network-service\android log --oneline -5` | `3fd8e9a fix(android): PortalLoginActivity exported=true + 全量 logcat 输出` |
| `exported=true` 是**故意**改的 | `git show 3fd8e9a -- app/src/main/AndroidManifest.xml` | ✅ diff 为 `false` → `true` |
| 改动理由是 `adb shell am start` | `git log -1 --format=%B 3fd8e9a` | ✅「修 SecurityException: am start ... 不再被拒」 |
| WebView 是**主**路径 | `ec9ffb9` 提交信息 + `LoginEngine.kt:653-663` | ✅「WebView 已接管本次登录，跳过 HTTP 登录」 |
| AC 终端归类不受 HTTP 控制 | `ec9ffb9` 提交信息（5 项排除） | ✅ 真机实测 |
| 终端类型字段是 `mac_type` | `0d96e7c` + `a41.js:423,681-711` | ✅ |
| 参数降级链 | `4be92e4` + `a41.js:1183-1188,1206-1207` | ✅ IP: `v46ip→ss5→v4ip→hex16→网卡`；MAC: `ss4→olmac→000...` |
| `campus-network-service` 非 git 仓库 | `git -C campus-network-service rev-parse --show-toplevel` | ✅ `fatal: not a git repository` |
| 无 debug 变体 manifest | `Test-Path android\app\src\debug\AndroidManifest.xml` | ✅ `False`（需新建） |
| 后台启动靠悬浮窗权限 | `LoginWorker.kt:74` | ✅ `Settings.canDrawOverlays(ctx)` |

---

## 12. 收尾建议

- **Day 1**：Phase 0 六条 + 提交（**T0.1 最优先**，4 个功能当前全挂）
- **Day 2–3**：Phase 1 与 Phase 2 并行
- **Day 4–5**：Phase 3（CI 变绿）
- **Day 6–7**：Phase 4 + 发版
- **发版前必做**：真机回归 **WebView 登录**（F1）+ 在 Release notes 公告**签名密钥轮换**导致存量用户需卸载重装

遇到本文件未覆盖的问题，按此顺序自查：
1. `git log -1 --format=%B <相关文件最近提交>` —— 很多设计意图写在提交信息里（**F1/F3/F5 都是这么找到的**）
2. `python _verify_names.py <模块>` —— 静态层问题
3. `read` 相关文件确认当前真实代码
4. 仍不确定 → 停下来问，不要按猜测改
