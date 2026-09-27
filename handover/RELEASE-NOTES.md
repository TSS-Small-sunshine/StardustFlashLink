# 交接包 {{SNAPSHOT}}（GitHub Actions 自动封包）

| 项 | 值 |
| --- | --- |
| 内容 | `handover/{{SNAPSHOT}}/` 全部材料（入口 `交接说明.md`） |
| 附件 | `handover-{{SNAPSHOT}}.zip` + `handover-{{SNAPSHOT}}.zip.sha256` |
| 构建提交 | `{{SHA}}` |
| 构建编号 | #{{RUN}} |

**怎么用**：解压后先读 `交接说明.md` —— 含 54 项待办索引、阅读顺序、4 个待决策项、封包红线。
同机也可直接读仓库里的 `handover/{{SNAPSHOT}}/` 目录，内容与本包完全一致。

**校验**（PowerShell）

```
certutil -hashfile handover-{{SNAPSHOT}}.zip SHA256
```

**取件命令**

```
gh release download handover-{{SNAPSHOT}} -R TSS-Small-sunshine/StardustFlashLink -D .\dist
```

> - 本 Release 每次推送 `handover/` 后由 CI 自动覆盖更新（tag 固定 `handover-<快照>`）。
> - 附件名刻意用 ASCII：GitHub 会把 release 附件名里的非 ASCII 字符剥掉（实测 `交接包-x.zip` → `-x.zip`）。
> - 封包前有「凭据守卫」：材料里出现 `.p12` / `keystore-credentials` / 明文口令形态会直接拒绝封包。
