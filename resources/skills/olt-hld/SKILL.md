---
name: OLT HLD 设备配置建议方案生成
description: >
  接收华为OLT的cfg配置文件，自动提取A机房单板业务统计表，按机房搬迁设计原则
  （单板总数最小化、配置改动最小、GPON升级为10GPON）推理计算单板合并方案，
  生成B机房新配置表。适用场景：MA5600/MA5800设备由A机房搬迁至B机房（B机房统一使用MA5800）。
scene: olt
doc_type: hld
order: 1
scripts:
  - name: extract_olt_config
    args: '"{cfg_file_abs_path}" "resources/skills/olt-hld/references/board_port_mapping.xlsx" "resources/test_olt/outputs/room_a_config.xlsx"'
    description: 从cfg文件提取A机房单板业务统计表，输出Markdown表格和Excel
  - name: generate_b_room_config
    args: '"resources/test_olt/outputs/room_a_config.xlsx" "resources/test_olt/outputs/room_b_config.xlsx"'
    description: 根据A机房配置表推算B机房单板方案，输出Excel
---

# OLT HLD 设备配置建议方案生成

读取 `resources/skills/olt-hld/references/background.md` 了解搬迁背景与业务单板知识。

读取 `resources/skills/olt-hld/references/design_principles.md` 了解设计原则。

---

## 执行步骤

### Step 1：提取A机房配置表

cfg 文件路径已在 prompt 的"用户上传的原始文件"中提供，直接使用该路径，无需搜索。

调用 `exec_skill_script`，参数：
- `script_name`: `extract_olt_config`
- `args`: `"<cfg文件绝对路径>" "resources/skills/olt-hld/references/board_port_mapping.xlsx" "resources/test_olt/outputs/room_a_config.xlsx"`

返回 A 机房业务单板 Markdown 表格，记录为 `{{ROOM_A_TABLE}}`。

### Step 2：推理生成B机房配置表

读取 `resources/skills/olt-hld/references/merge_algorithm.md`，根据 `{{ROOM_A_TABLE}}` 和设计原则推理计算，生成 `{{ROOM_B_TABLE}}`。

### Step 3：生成搬迁变更说明

逐行对比 A、B 机房配置表，**只描述发生变动的单板**，记录为 `{{CHANGE_LIST}}`：

| 变动类型 | 描述格式 |
|----------|----------|
| 单板型号变更（GPON→10GPON） | `<槽位>槽位 <原型号> 更换为 <新型号>` |
| 使用端口数增加（接收迁入业务） | `<来源槽位>槽位 <原型号> 的 <N> 个端口迁移至 <目标槽位>槽位` |
| 不出现在B机房表中（业务已迁出） | `<原型号> 空闲单板后续备用` |

同一槽位同时发生型号变更和端口迁移，合并为一条描述。

### Step 4：按模板输出

读取 `resources/skills/olt-hld/outputs/output_template.md`，替换所有占位符后原样输出，不得增删任何内容：

- `{{ROOM_A_TABLE}}` → Step 1 结果
- `{{ROOM_B_TABLE}}` → Step 2 结果
- `{{CHANGE_LIST}}` → Step 3 结果（每条以 `- ` 开头）

生成完成后读取 `resources/skills/olt-hld/checks/self_check.md` 进行自校验，不满足则重新推理生成。
