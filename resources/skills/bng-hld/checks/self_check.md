## 自校验

生成完成后，逐条核查以下清单。**有任意一项不满足，则重新推理并重新生成：**

### 旧机房配置表（{{ROOM_A_TABLE}}）
- [ ] 状态列由端口配置表推导得出，Eth-Trunk 条目已过滤
- [ ] 端口协议状态有任意 up 的槽位，状态列为"使用"；全部 down 的槽位，状态列为"未使用"

### 新机房配置表（{{ROOM_B_TABLE}}）
- [ ] 不含任何状态=未使用的行
- [ ] 不含 EOM 列
- [ ] 所有状态=使用且生命周期为EOM/EOS的单板均已完成替换（或标注 ⚠️）
- [ ] 替换后的 BomCode 和 Description 与 `references/procurement_replacement.xlsx` 完全一致
- [ ] 所有保留槽位的 Slot Number 和 Card 子槽位与旧机房保持一致
- [ ] 非EOX、状态=使用的单板原样保留，BomCode 和 Description 未被改动
- [ ] 每块单板的 Type 字段与旧机房保持一致

### 搬迁变更说明（{{CHANGE_LIST}}）
- [ ] 变更说明条数与实际变动行数一致（剔除行 + 替换行），无遗漏、无多余
- [ ] 输出格式严格按照 `references/output_template.md`，三个占位符均已替换，无残留占位符
