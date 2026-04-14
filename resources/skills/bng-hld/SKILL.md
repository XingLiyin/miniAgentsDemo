---
name: BNG HLD 设备搬迁配置建议方案生成
description: >
  接收华为BNG设备的旧机房单板配置表（Excel），结合内置的端口配置表、EOX生命周期对照表和移动集采
  框架替换表，按搬迁设计原则（剔除未使用槽位、EOX单板1:1替换为集采新型号）生成新机房配置表，
  并输出搬迁变更说明。适用场景：BNG设备由旧机房搬迁至新机房，新机房统一采用移动集采框架单板。
scene: bng
doc_type: hld
order: 1
scripts:
  - name: derive_slot_status
    description: 从内置端口配置表推导各槽位使用状态，过滤Eth-Trunk、解析GE端口槽位、按协议状态分组判断
    command: "python scripts/derive_slot_status.py {port_config}"
    defaults:
      port_config: "references/port_config.xlsx"
  - name: parse_bng_config
    description: 从三张表组装固定7列的ROOM_A_TABLE：单板配置表提供基础字段，调用derive_slot_status推导状态列，EOX表填入EOM列
    command: "python scripts/parse_bng_config.py {input_file} {port_config} {eox_lifecycle}"
    defaults:
      port_config: "references/port_config.xlsx"
      eox_lifecycle: "references/eox_lifecycle.xlsx"
---

# BNG HLD 设备搬迁配置建议方案生成

读取 `skills/bng-hld/references/design_principles.md` 了解设计原则。

---

## 用户需提供的文件

| 文件 | 说明 |
|------|------|
| 旧机房BNG单板配置表 | Excel，含槽位/Card/BomCode/Description/Type等列 |

以下文件已内置于 references，无需用户上传：

| 内置文件 | 说明 |
|----------|------|
| `references/port_config.xlsx` | BNG端口配置表，含端口名称/物理状态/协议状态/描述 |
| `references/eox_lifecycle.xlsx` | 单板EOX生命周期对照表，含部件编码/生命周期状态 |
| `references/procurement_replacement.xlsx` | 移动集采替换对照表，含旧BomCode→新BomCode映射 |

如用户未上传旧机房单板配置表，**停止执行并提示用户上传该文件**。

---

## 执行步骤

### Step 1：组装旧机房配置表 {{ROOM_A_TABLE}}

**Step 1a：推导子卡使用状态**

调用 `derive_slot_status` 脚本（自动读取内置 `port_config.xlsx`，无需额外参数），获取各子卡使用状态映射，记录备用。

**Step 1b：组装完整配置表**

调用 `parse_bng_config` 脚本，将用户上传的单板配置表路径作为 `input_file` 传入，输出固定 7 列格式的 Markdown 表格（Slot Number / Card / BomCode / Description / Type / 状态 / EOM），记录为 `{{ROOM_A_TABLE}}`。

### Step 2：读取集采替换表，确定需变更的单板

使用 `Read` 工具读取 `skills/bng-hld/references/procurement_replacement.xlsx`（以"旧BomCode"为键，查"新BomCode"和"新Description"）。

对 `{{ROOM_A_TABLE}}` 中的每一行判断：

| 条件 | 标记 |
|------|------|
| 状态 = 未使用 | **剔除**（无论EOM是否为Y） |
| 状态 = 使用，且 EOM = Y | **替换**（查集采替换表获取新型号；查不到则标注 ⚠️） |
| 状态 = 使用，且 EOM = N | **保留** |

### Step 3：生成新机房配置表

按以下规则构建 `{{ROOM_B_TABLE}}`：

- **剔除**行：不出现在新机房表中
- **替换**行：Slot Number、Card、Type、状态不变；BomCode 和 Description 更新为集采新型号
- **保留**行：原样输出
- 输出列格式：`Slot Number | Card | BomCode | Description | Type | 状态`（去掉 EOM 列）

### Step 4：生成搬迁变更说明

逐行对比，**只描述发生变动的条目**，记录为 `{{CHANGE_LIST}}`：

| 变动类型 | 描述格式 |
|----------|----------|
| 槽位被剔除（未使用） | `Slot <槽位> <旧BomCode>（<Description简称>）未使用，不迁移` |
| 单板型号替换（EOX） | `Slot <槽位> <旧BomCode> 替换为 <新BomCode>（<新Description简称>）` |

无变动的行不出现在变更说明中。未使用槽位只写剔除，不写替换。

### Step 5：按模板输出

读取 `skills/bng-hld/outputs/output_template.md`，替换所有占位符后原样输出，不得增删任何内容：

- `{{ROOM_A_TABLE}}` → Step 1 结果
- `{{ROOM_B_TABLE}}` → Step 3 结果
- `{{CHANGE_LIST}}` → Step 4 结果（每条以 `- ` 开头）

生成完成后读取 `skills/bng-hld/checks/self_check.md` 进行自校验，不满足则重新推理生成。
