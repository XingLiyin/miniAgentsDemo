---
name: OLT 端口业务分析
description: >
  接收网管导出的 ONU 状态 Excel 表，统计四类端口数量（UP且有业务、UP但无业务、
  Down但配置有业务、Down且无业务），按输出模板生成 Markdown 表格。
  触发场景：用户上传网管端口/ONU 状态表，要求生成"有效业务与端口识别"统计表。
scene: olt
doc_type: hld
order: 1
scripts:
  - name: analyze_ports
    description: 解析 ONU 状态 Excel，输出四类端口统计数据
    command: "python scripts/analyze_ports.py {input_file}"
---

# OLT 端口业务分析

读取 `skills/olt-hld-port-analysis/references/background.md` 了解字段含义与四类端口定义（**仅供内部理解，不得输出到结果中**）。

---

## 执行步骤

### Step 1：运行分析脚本

Excel 文件路径为 `skills/olt-hld-port-analysis/references/mock_nms_onu.xlsx`，直接使用该路径。调用 `analyze_ports` 脚本，脚本输出格式为：

```
网元名称|UP且有业务|UP但无业务|Down但配置有业务|Down且无业务
OLT001|61|0|5|14
```

记录各字段值：
- `{{NE_NAME}}` ← 网元名称
- `{{UP_WITH_BIZ}}` ← UP 且有业务端口数
- `{{DOWN_WITH_BIZ}}` ← Down 但配置有业务端口数
- `{{DOWN_NO_BIZ}}` ← Down 且无业务端口数

> "UP 但无业务"无法从 ONU 状态表判断，输出时固定填 `0`，无需从脚本读取。

### Step 2：按模板输出

读取 `skills/olt-hld-port-analysis/outputs/output_template.md`，将占位符替换后**原样输出模板全文，不得在模板之前或之后添加任何内容**（包括背景说明、定义表格、分析过程等）：

| 占位符 | 替换值 |
|--------|--------|
| `{{NE_NAME}}` | Step 1 中的网元名称 |
| `{{UP_WITH_BIZ}}` | Step 1 中 UP 且有业务端口数 |
| `{{UP_NO_BIZ}}` | 固定填 `0` |
| `{{DOWN_WITH_BIZ}}` | Step 1 中 Down 但配置有业务端口数 |
| `{{DOWN_NO_BIZ}}` | Step 1 中 Down 且无业务端口数 |

最终输出**必须以 `# 有效业务与端口识别` 开头**，无任何前置文字。
