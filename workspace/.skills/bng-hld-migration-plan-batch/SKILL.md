---
name: BNG HLD 搬迁方案生成（多次割接）
description: >
  生成新垦机房BNG搬迁方案文档（拓扑不变、利旧IP地址池、站点搬迁场景）。
  适用场景：BNG多次分批割接，下挂OLT量较大（30以上）。用户地址池根据业务类型
  选择新建或利旧IP：利旧IP仅适用于专线用户、静态用户；PPPoE、DHCP用户需新建地址池。
  触发词：BNG多次割接、BNG分批割接、OLT量大搬迁、BNG HLD 多次、大规模BNG搬迁。
scene: bng
doc_type: hld
order: 3
---

# BNG HLD 搬迁方案生成（多次割接）

读取 `skills/bng-hld-migration-plan-batch/references/migration_content.md` 获取方案完整内容。

---

## 执行步骤

### Step 1：读取方案内容

读取 `skills/bng-hld-migration-plan-batch/references/migration_content.md`，获取以下全部内容模块，记录为 `{{MIGRATION_CONTENT}}`：
- 大标题（新垦机房BNG搬迁方案）
- 割接关键步骤说明
- 客户依赖列表
- 说明列表
- 关键步骤表格

### Step 2：原样输出

将 `{{MIGRATION_CONTENT}}` 完整输出，**不得增删、改写或重排任何内容**。输出格式保持 Markdown，表格对齐，列表缩进一致。
