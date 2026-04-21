---
name: PTN HLD 配置建议页生成
description: >
  当用户说"帮我生成PTN的HLD中的配置建议页"或类似表述时触发。
  从网元报表中列出所有网元供用户选择，根据用户选择的网元提取A机房LPU单板配置，
  判断是否需要EOX替换，生成B机房配置建议表，最终按模板输出完整配置建议页。
  适用场景：PTN设备由A机房搬迁至B机房时的HLD配置建议页编写。
scene: ptn
doc_type: hld
order: 1
scripts:
  - name: extract_ptn_config
    description: 提取指定网元的LPU单板配置，输出A机房配置表、EOX替换判断及候选单板列表。网元名称通过 extra_args.element_name 传入，无需 input_file
    command: "python scripts/extract_ptn_config.py {element_name} {refs_dir}"
    defaults:
      refs_dir: "references"
---

# PTN HLD 配置建议页生成

读取 `skills/ptn-hld-hardware-cfg/references/background.md` 了解搬迁场景与LPU单板知识。

读取 `skills/ptn-hld-hardware-cfg/references/design_principles.md` 了解设计原则。

---

## 执行步骤

### Step 1：列出网元供用户选择

使用 `Read` 工具读取 `skills/ptn-hld-hardware-cfg/references/网元报表.xlsx`，提取"网元名称"列的所有值，以编号列表形式展示给用户，请用户选择其中一个网元。

等待用户回复后，记录所选网元名称为 `{{ELEMENT_NAME}}`。

### Step 2：提取配置数据，生成 {{ROOM_A_TABLE}}

调用 `extract_ptn_config` 脚本，**不需要传 `input_file`**，将 `{{ELEMENT_NAME}}` 通过 `extra_args` 以 `element_name` 为键传入，references 目录已硬编码无需传入。

解析脚本输出的各分段：
- `=== TABLE_A ===` → `{{ROOM_A_TABLE}}`
- `=== NEEDS_REPLACE ===` → `{{NEEDS_REPLACE}}`（true/false）
- `=== NEW_DEVICE_TYPE ===` → `{{NEW_DEVICE_TYPE}}`（需替换时的新设备类型）
- `=== CANDIDATES ===` → `{{CANDIDATES}}`（新设备的候选 LPU 单板列表）
- `=== HARDWARE_CHANGE ===` → `{{HARDWARE_CHANGE}}`

### Step 3：语义匹配生成B机房配置表

**若 `{{NEEDS_REPLACE}}` 为 false：**
`{{ROOM_B_TABLE}}` 与 `{{ROOM_A_TABLE}}` 完全一致。

**若 `{{NEEDS_REPLACE}}` 为 true：**
读取 `skills/ptn-hld-hardware-cfg/references/matching_guide.md`，按其中的匹配优先级逐槽位为A机房每块 LPU 从 `{{CANDIDATES}}` 中选择对应的 B 机房单板。槽位号与A机房保持一致，设备类型替换为 `{{NEW_DEVICE_TYPE}}`。

匹配完成后，按与 `{{ROOM_A_TABLE}}` 相同的表格格式组装 `{{ROOM_B_TABLE}}`。单板描述列的内容格式为 `{单板类型} {单板描述}`，例如：`LPU TPA1EX24S`。

### Step 4：按模板输出

使用 `Read` 工具读取 `skills/ptn-hld-hardware-cfg/outputs/output_template.md`，替换所有占位符后原样输出，不得增删任何内容：

- `{{ROOM_A_TABLE}}` → Step 2 结果
- `{{ROOM_B_TABLE}}` → Step 3 结果
- `{{HARDWARE_CHANGE}}` → Step 2 结果

### Step 5：自校验

读取 `skills/ptn-hld-hardware-cfg/checks/self_check.md`，逐条核查，有任意一项不满足则重新推理并重新生成。
