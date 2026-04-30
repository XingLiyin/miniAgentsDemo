---
name: OTN HLD 硬件配置页生成
description: >
  接收网管数据、设备生命周期表、客户集采设备信息三个Excel，自动生成OTN设备搬迁
  HLD硬件配置页。提取A机房现网配置，识别EOS设备并从集采清单中匹配替代单板，
  生成B机房配置表，输出含A/B机房对比、设计原则和变更说明的完整方案文档。
  适用场景：华为OSN系列OTN设备（9800/1800V/OSN3500等）由A机房搬迁至B机房。
scene: otn
doc_type: hld
order: 1
---

# OTN HLD 硬件配置页生成

读取 `skills/otn-hld-hardware-cfg/references/background.md` 了解OTN搬迁背景与单板命名规则。

读取 `skills/otn-hld-hardware-cfg/references/design_principles.md` 了解设计原则。

所有参考数据已内置于 skill 中，无需用户提供任何文件：

- `skills/otn-hld-hardware-cfg/references/nms_data.xlsx` — 网管数据（A机房现网配置）
- `skills/otn-hld-hardware-cfg/references/lifecycle.xlsx` — 设备生命周期表
- `skills/otn-hld-hardware-cfg/references/procurement.xlsx` — 客户集采设备信息

---

## 执行步骤

### Step 1：生成A机房配置表

读取 `nms_data.xlsx`，将所有数据行直接输出为 Markdown 表格，表头为：**设备类型 / 单板类型 / 单板数量**。记录为 `{{ROOM_A_TABLE}}`。

### Step 2：推理生成B机房配置表

读取 `skills/otn-hld-hardware-cfg/references/merge_algorithm.md`，结合 `lifecycle.xlsx` 和 `procurement.xlsx`，逐行处理 `{{ROOM_A_TABLE}}`，生成 `{{ROOM_B_TABLE}}`。行数与A机房表完全一致。

### Step 3：生成硬件方案说明

对比 A、B 机房配置表，逐设备归纳变动，**GA设备不出现**，记录为 `{{CHANGE_LIST}}`：

| 变动类型 | 描述格式 |
|----------|----------|
| EOS设备替换 | `<旧设备类型>（EOS）替换为 <新设备类型>，单板由 <旧单板前缀>系列升级为 <新单板前缀>系列` |
| 无变化设备 | （不出现） |

每类替换一条。

### Step 4：按模板输出

读取 `skills/otn-hld-hardware-cfg/outputs/output_template.md`，替换所有占位符后原样输出，不得增删任何内容：

- `{{ROOM_A_TABLE}}` → Step 1 结果
- `{{ROOM_B_TABLE}}` → Step 2 结果
- `{{CHANGE_LIST}}` → Step 3 结果

生成完成后读取 `skills/otn-hld-hardware-cfg/checks/self_check.md` 进行自校验，不满足则重新推理生成。
