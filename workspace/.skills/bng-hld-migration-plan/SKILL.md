---
name: BNG HLD 搬迁方案生成
description: >
  生成新垦机房BNG搬迁方案文档（拓扑不变、利旧IP地址池、站点搬迁场景）。
  输出固定结构的方案内容，包含割接关键步骤、客户依赖、背景说明及关键步骤表格。
  适用场景：BNG一次性割接，下挂OLT量较少（20-30以内）。
  触发词：BNG搬迁、BNG割接方案、BNG HLD、新垦机房搬迁、站点搬迁方案。
scene: bng
doc_type: hld
order: 2
---

# BNG HLD 搬迁方案生成

读取 `skills/bng-hld-migration-plan/references/migration_content.md` 获取方案完整内容。

---

## 执行步骤

### Step 1：读取方案内容

读取 `skills/bng-hld-migration-plan/references/migration_content.md`，获取以下全部内容模块，记录为 `{{MIGRATION_CONTENT}}`：
- 大标题（新垦机房BNG搬迁方案）
- 割接关键步骤说明
- 客户依赖列表
- 说明列表
- 关键步骤表格

### Step 2：原样输出

将 `{{MIGRATION_CONTENT}}` 完整输出，**不得增删、改写或重排任何内容**。输出格式保持 Markdown，表格对齐，列表缩进一致。
