"""
BNG 旧机房配置表组装脚本
从三张表组装固定7列的 {{ROOM_A_TABLE}}：
  1. 旧机房BNG单板配置表（用户上传）→ Slot Number / Card / BomCode / Description / Type
  2. BNG端口配置表（内置）→ 调用 derive_slot_status 推导"状态"列
  3. EOX生命周期对照表（内置）→ 填入"EOM"列（EOM/EOS → Y，其余 → N）

使用方式：
    python parse_bng_config.py <input_file> <port_config.xlsx> <eox_lifecycle.xlsx>

输出：固定7列 Markdown 表格
"""

import sys
import os
import pandas as pd

# 复用同目录下的 derive_slot_status 模块
sys.path.insert(0, os.path.dirname(__file__))
from derive_slot_status import derive


FIXED_COLS = ["Slot Number", "Card", "BomCode", "Description", "Type", "状态", "EOM"]


def load(path):
    df = pd.read_excel(path, dtype=str).fillna("")
    return df[df.iloc[:, 0].str.strip() != ""].reset_index(drop=True)


def find_col(df, *keywords):
    for kw in keywords:
        for c in df.columns:
            if kw in str(c):
                return c
    return None


def find_status(slot_val, card_val, status_map):
    slot = str(slot_val).strip()
    card = str(card_val).strip() if str(card_val).strip() not in ("", "nan") else ""

    if "-" in slot:
        parts = slot.split("-")
        return status_map.get(f"{parts[0]}-{parts[1]}", "未使用")
    if card:
        return status_map.get(f"{slot}-{card}", "未使用")
    # 主板：前缀匹配所有 slot-* 子槽位
    matched = [v for k, v in status_map.items() if k.startswith(f"{slot}-")]
    return "使用" if "使用" in matched else "未使用"


def build_eox_map(eox_df):
    bom_col  = find_col(eox_df, "编码", "BomCode")
    life_col = find_col(eox_df, "生命周期")
    result = {}
    for _, r in eox_df.iterrows():
        code   = str(r[bom_col]).strip()
        status = str(r[life_col]).strip().upper()
        result[code] = "Y" if status in ("EOM", "EOS") else "N"
    return result


def to_markdown(df):
    cols = df.columns.tolist()
    lines = [
        "| " + " | ".join(str(c) for c in cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row.values) + " |")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 4:
        print("用法：python parse_bng_config.py <board_config.xlsx> <port_config.xlsx> <eox_lifecycle.xlsx>")
        sys.exit(1)

    board_df = load(sys.argv[1])
    status_map = derive(sys.argv[2])   # 调用 derive_slot_status 脚本逻辑
    eox_df   = load(sys.argv[3])

    slot_col = find_col(board_df, "Slot", "槽位")
    card_col = find_col(board_df, "Card", "子槽")
    bom_col  = find_col(board_df, "BomCode", "部件编码")
    desc_col = find_col(board_df, "Description", "描述", "部件名称")
    type_col = find_col(board_df, "Type", "类型")
    eox_map  = build_eox_map(eox_df)

    rows = []
    for _, r in board_df.iterrows():
        slot   = r[slot_col] if slot_col else ""
        card   = r[card_col] if card_col else ""
        bom    = r[bom_col]  if bom_col  else ""
        desc   = r[desc_col] if desc_col else ""
        typ    = r[type_col] if type_col else ""
        status = find_status(slot, card, status_map)
        eom    = eox_map.get(str(bom).strip(), "N")
        rows.append([slot, card, bom, desc, typ, status, eom])

    print(to_markdown(pd.DataFrame(rows, columns=FIXED_COLS)))


if __name__ == "__main__":
    main()
