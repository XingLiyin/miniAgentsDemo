"""
子卡使用状态推导脚本
从内置 BNG 端口配置表推导每个子卡（PIC）的使用状态。

处理逻辑（子卡级，槽位级汇总由 parse_bng_config.py 负责）：
  1. 过滤端口名称以 Eth-Trunk 开头的条目
  2. 解析 GE<slot>/<sub_slot>/<port> 格式，提取子卡标识 <slot>-<sub_slot>
  3. 按子卡分组：任意端口协议状态为 up → 使用，全部 down → 未使用

  槽位（LPU）状态由调用方推导：
    所有子卡均为"未使用" → 槽位为"未使用"；任意子卡为"使用" → 槽位为"使用"

使用方式：
    python derive_slot_status.py <port_config.xlsx>

输出：每行一条  <slot>-<sub_slot>\t<状态>，供 parse_bng_config.py 调用或独立查看
"""

import sys
import re
import pandas as pd


def parse_slot_key(port_name):
    """GE1/1/0(10G) → '1-1'，无法解析返回 None。"""
    m = re.match(r'[A-Za-z\-]+(\d+)/(\d+)/', str(port_name).strip())
    return f"{m.group(1)}-{m.group(2)}" if m else None


def derive(port_xlsx):
    df = pd.read_excel(port_xlsx, dtype=str).fillna("")
    df = df[df.iloc[:, 0].str.strip() != ""]

    # 过滤 Eth-Trunk
    df = df[~df.iloc[:, 0].str.startswith("Eth-Trunk")].copy()

    # 解析槽位
    df["_slot_key"] = df.iloc[:, 0].apply(parse_slot_key)
    df = df.dropna(subset=["_slot_key"])

    # 协议状态列（第3列）
    proto_col = df.columns[2]
    df["_up"] = df[proto_col].str.strip().str.lower() == "up"

    # 分组判断
    status = df.groupby("_slot_key")["_up"].any().map({True: "使用", False: "未使用"})
    return status.to_dict()


def main():
    if len(sys.argv) < 2:
        print("用法：python derive_slot_status.py <port_config.xlsx>")
        sys.exit(1)
    result = derive(sys.argv[1])
    for k, v in sorted(result.items()):
        print(f"{k}\t{v}")


if __name__ == "__main__":
    main()
