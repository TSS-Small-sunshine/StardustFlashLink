# 版本号与版本线代号

本项目的版本号是**四段数字** `MAJOR.MINOR.PATCH.SERIAL`，每条**版本线**（`MAJOR.MINOR`）配一个**星名代号**。
一句话规则：**代号跟着 `MAJOR.MINOR` 走** —— 同一条版本线内的所有迭代共用同一个代号，进入下一条线才换代号。

---

## 1. 版本号格式

```
   2    .    0    .    4    .    0
   │         │         │       └── SERIAL  同一版本线内的发版序号（修复 / 打包 / 文案）
   │         │         └────────── PATCH   向后兼容的功能新增
   │         └──────────────────── MINOR   ┐ 这两段共同决定代号
   └────────────────────────────── MAJOR   ┘
```

| 变化 | 什么时候用 | 例子 |
| --- | --- | --- |
| `SERIAL` +1 | 纯修复 / 打包链路 / 文案，接口与配置结构不变 | `2.0.4.0` → `2.0.4.1` |
| `PATCH` +1 | 新增**向后兼容**的功能（新 API、新配置项、UI 重构） | `2.0.4.0` → `2.0.5.0` |
| `MINOR` +1 | 一条版本线收束、开下一条线 → **换代号** | `2.0.x` → `2.1.0.0 "Vega"` |
| `MAJOR` +1 | 不兼容变更（改服务名 / 配置结构 / 技术栈） | `2.x` → `3.0.0.0 "Polaris"` |

> **为什么代号不跟 `PATCH` / `SERIAL` 变**：代号回答的是「这是哪一代产品」，
> 不是「第几次发版」。`2.0.4.0` 与 `2.0.5.0` 同属 `2.0` 代（都叫 Sirius）；
> 到了 `2.1.0.0` 才换成下一个星名。

---

## 2. 唯一来源与同步清单

`version.py` 是**唯一来源**：

```python
VERSION      = "2.0.4.0"          # 机器读：安装包版本 / 服务 / API
CODENAME     = "Sirius"           # 人读：代号（英文星名）
CODENAME_CN  = "天狼星"            # 人读：代号（中文）
VERSION_FULL = "2.0.4.0 Sirius"   # 展示串：安装器标题 / Web UI 徽章 / 「关于」页
```

改版本时必须同步下面这些位置，**不同步 `_smoke_static.py` 会直接判失败**：

| 位置 | 要改什么 |
| --- | --- |
| `version.py` | `VERSION` / `CODENAME` / `CODENAME_CN` / `VERSION_FULL` |
| `packaging/setup.iss` | `MyAppVersion`、`MyAppCodename`（NSSM 服务描述） |
| `联网_service.py` | 文件头 docstring |
| `install.bat` / `uninstall.bat` | 标题行 |
| `packaging/build.ps1` | banner |
| `CHANGELOG.md` | 在最上方新增本版段落 |
| `packaging/RELEASE-NOTES.md` | 本版说明（CI 渲染） |

---

## 3. 代号命名规则

1. **只挑亮星**：肉眼可见、名字好念的恒星（天狼星、织女星、北极星…）；不要生僻编号（`HD 189733`）。
2. **不重复**：用过的代号进「已用」区，永不复用。
3. **不强制按亮度排序**：按发布顺序依次取即可。
4. **只出现在展示层**：日志、Web UI 徽章 / 「关于」页、安装器 `AppVerName`、Release 标题。
   **绝不**进文件名、NSSM 服务名、`AppId`、注册表项、API 路径 —— 那些是不变量，改了会破坏存量升级。
5. **中英一一对应**：`CODENAME_CN` 必须能直译回 `CODENAME`。

---

## 4. 候选星名表

| 代号 | 中文 | 备注 |
| --- | --- | --- |
| `Sirius` | 天狼星 | **已使用：`2.0` 线**（v2.0.4.0 起启用代号机制，至 v2.0.14.0 收束） |
| `Vega` | 织女星 | **已使用：`2.1` 线**（v2.1.0.0 起；Windows 稳定线，继续维护） |
| `Altair` | 河鼓二（牛郎星） | **已使用：`3.0` 线**（跨平台重写，`dev/3.0-altair`）—— 与织女星正好一对 ✓ |
| `Polaris` | 北极星 | 候选（适合 `MAJOR` 级里程碑） |
| `Rigel` | 参宿七 | 候选 |
| `Deneb` | 天津四 | 候选（夏季大三角的第三个，可与 Vega/Altair 凑齐 ✓） |
| `Antares` | 心宿二 | 候选 |
| `Canopus` | 老人星 | 候选 |
| `Arcturus` | 大角星 | 候选 |
| `Procyon` | 南河三 | 候选 |
| `Betelgeuse` | 参宿四 | 候选 |
| `Capella` | 五车二 | 候选 |
| `Aldebaran` | 毕宿五 | 候选 |
| `Spica` | 角宿一 | 候选 |
| `Fomalhaut` | 北落师门 | 候选 |

**已用 / 无代号区**

| 版本线 | 代号 | 状态 |
| --- | --- | --- |
| `1.x`（v1.0 – v1.4.0） | —（无） | 代号机制自 `2.0` 线起启用，历史版本不回填 |
| `2.0`（v2.0.0 – v2.0.14.0） | `Sirius` / 天狼星 | 已收束（v2.1.0.0 起进入 `2.1` 线） |
| `2.1`（v2.1.0.0 – 维护中） | `Vega` / 织女星 | ✅ **Windows 稳定线**（只修 bug / 安全） |
| `3.0`（v3.0.0.0 – 开发中） | `Altair` / 河鼓二（牛郎星） | 🚧 **跨平台线**（`dev/3.0-altair`，**只发预览版**） |

---

## 4.5 发行通道（学 Minecraft）

3.0 起把「版本号」和「通道」分开：**版本说是什么东西，通道说敢不敢给用户**。

| 通道 | 版本串例子 | git 分支 | GitHub Release | 谁能收到 |
| --- | --- | --- | --- | --- |
| **Snapshot** 快照版 | `3.0.0.0-snapshot.14` | `dev/3.0-altair` 每次 push | 只挂 workflow artifact | 开发/尝鲜（手动下载） |
| **Preview** 预览版 | `3.0.0.0-preview.1` | `dev/3.0-altair` 打 tag | `--prerelease` ✓ | 配置里把 `update_channel` 改成 `preview` 的用户 |
| **RC** 候选版 | `3.0.0.0-rc.1` | `dev/3.0-altair` 打 tag | `--prerelease` ✓ | 同上（rc 更接近正式） |
| **Release** 正式版 | `3.0.0.0` | `main` | **非 prerelease**（`/releases/latest` 能取到） | 所有人（含自动升级） |

规则（写进代码，不靠人记 ✓）：

1. **比较顺序**：四段数字 → 通道（snapshot < preview < rc < release）→ 通道内序号；
   实现见 `desktop/crates/drcom-core/src/channel.rs`，单测已覆盖 ✓；
2. **自动更新只认正式版**：`Channel::is_prerelease()` 为真的一律跳过 ——
   所以 3.0 开发期**不会**打扰 2.1.x 的用户 ✓（与 2.x 现有的「只认非 prerelease」逻辑同源）；
3. **2.1.x（Vega）与 3.0.x（Altair）并行**：两条线的 tag 不同名（`v2.1.0.0` / `v3.0.0.0-preview.N`），
   Release 列表里一眼能分清 ✓；
4. 正式版**必须**在 `main` 上；预览版**只**在 `dev/**` 上 —— 想发正式版就得先合回 main ✓。

---

## 5. 发布时的命名约定

| 位置 | 格式 | 例子 |
| --- | --- | --- |
| Git tag / Release tag | `v{VERSION}`（**非** prerelease，自动升级通道） | `v2.0.4.0` |
| 固定下载 Release | `installer`（prerelease，链接固定） | `installer` |
| 安装包文件名 | `StardustFlashLink-Setup-v{VERSION}.exe`（不变量 I9） | `StardustFlashLink-Setup-v2.0.4.0.exe` |
| 安装器显示名 | `星尘闪连 (Stardust Flash Link) {VERSION} "{CODENAME}"` | `… 2.0.4.0 "Sirius"` |
| Release 标题（建议） | `v{VERSION} "{CODENAME}" {CODENAME_CN}` | `v2.0.4.0 "Sirius" 天狼星` |

---

## 6. 发版 checklist

```
# 1) 改版本字面量（§2 清单）+ 写 CHANGELOG / RELEASE-NOTES
# 2) 本地自检（含隐私守卫；要连安装目录一起查就设 DRCOM_DATA_DIR）
cd DrcomAutoLogin-Windows
python _smoke_static.py
python _smoke_http.py

# 3) 提交到版本分支并开 PR（禁止 git add .）
git status --short
git add <逐个文件>
git commit -m "chore(release): v2.0.4.0 Sirius 天狼星"

# 4) 合并到 main 后触发 CI 构建（版本分支用 workflow_dispatch）
gh workflow run "Build Windows Installer" -R TSS-Small-sunshine/StardustFlashLink --ref main
```

CI 会一次产出两条 release（`installer` prerelease + `v{VERSION}` 非 prerelease）。
**没有非 prerelease 的 `v{VERSION}`，自动升级客户端就取不到版本号与 `digest`，整条升级链路失效。**

---

## 7. 常见问题

**Q：`2.0.4.1` 要不要换代号？**
不要。`MINOR` 没变（还是 `0`），仍是 `2.0` 线 = `Sirius`。

**Q：`2.0.5.0` 呢？**
仍是 `2.0` 线（`MINOR` = 0），代号不变。

**Q：什么时候换成 `Vega`？**
发布 `2.1.0.0` 那条线的第一个版本时。**已经发生过**：`v2.1.0.0` 起 `CODENAME = "Vega"`、
`CODENAME_CN = "织女星"`，`Sirius` 已移入「已用」区；下一条线（`2.2.0.0`）再取一个新星名。

**Q：`SERIAL` 有什么用？**
同一版本线里「只修 bug、不加功能」的发版用它，避免为一次 hotfix 就跳 `PATCH`。
