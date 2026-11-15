<!--
  这个模板同时会被「PR 机器人审核」读取（.github/scripts/pr_review.py）：
    - 标题必须符合 Conventional Commits：feat(scope): 中文说明 / fix: ...
    - 正文必须保留「## 这是什么」与「## 怎么验证」两个小节；
    - 不适用的条目请删掉，不要留空。
  完整策略见 docs/PR-WORKFLOW.md。
-->

## 这是什么

<!-- 一句话：改了什么、为什么。相关 issue 用 #编号 关联。 -->

## 改动范围

- [ ] 2.x（Vega / Windows 稳定线）—— 合入 \`main\`
- [ ] 3.0（Altair / Rust 跨平台线）—— 合入 \`dev/3.0-altair\`
- [ ] 仅文档 / CI / 发版流程

<!-- 一条线一个 PR；不要把两条线的改动混在一起。 -->

## 怎么验证

<!-- 贴命令与真实输出，不要只写「已测试」。 -->

\`\`\`powershell
python _smoke_static.py   # 结果：___ 项 0 失败
python _smoke_http.py     # 结果：___ 项 0 失败
\`\`\`

- [ ] 涉及服务 / 安装器 / NSSM / 升级：已在真机验证（写明机器与步骤）
- [ ] 纯文档 / CI 改动：写明怎么验证（如 \`python .github/scripts/pr_review.py --self-test\`）

## 风险与回滚

<!-- 会影响哪些用户行为？出问题怎么回滚（revert PR / 手动步骤）？ -->

## 检查清单

- [ ] 没有提交真机凭据、\`logs\\\`、真机截图（AGENTS.md §5 隐私红线）
- [ ] 版本号已按 \`docs/VERSIONING.md\` 同步（本 PR 发版时才需要）
- [ ] 用户可见改动已更新 \`CHANGELOG.md\`
- [ ] 接口路径 / 字段名、服务名、安装路径没有破坏性变更（有的话在正文说明）
- [ ] 改动过大时已在「风险与回滚」说明为什么必须一次改
