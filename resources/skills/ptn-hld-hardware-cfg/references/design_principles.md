## 设计原则

**原则1：槽位号保持不变**

B机房配置表中每块 LPU 的槽位号必须与A机房完全一致，不得重新编排。

**原则2：EOX设备整机替换**

若A机房网元的设备类型处于 EOX 状态，B机房设备类型更换为新款型号（`{{NEW_DEVICE_TYPE}}`），LPU 单板从候选列表（`{{CANDIDATES}}`）中按匹配规则重新选型（详见 `references/matching_guide.md`）。

**原则3：非EOX设备单板原样保留**

若A机房网元设备类型为在产型号（非EOX），`{{ROOM_B_TABLE}}` 与 `{{ROOM_A_TABLE}}` 完全一致，不做任何修改。
