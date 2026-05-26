"""
OLT 端口业务分析脚本
从网管导出的 ONU 状态 Excel 中统计四类端口数量。

使用方式：
    python analyze_ports.py <onu_excel路径>

输出（stdout）：
    网元名称|UP且有业务|UP但无业务|Down但配置有业务|Down且无业务
"""

import sys
from collections import defaultdict
from openpyxl import load_workbook


TOTAL_PORTS_PER_SLOT = 16  # 每槽固定 0-15 共 16 个端口

REQUIRED_COLUMNS = ["网元名称", "槽", "端口", "状态"]


def analyze(xlsx_path):
    wb = load_workbook(xlsx_path, read_only=True)
    ws = wb.active

    # 读表头，定位各列索引
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    missing = [name for name in REQUIRED_COLUMNS if name not in headers]
    if missing:
        raise ValueError(f"Excel 缺少必要列：{missing}，实际列：{headers}")

    idx_ne   = headers.index("网元名称")
    idx_slot = headers.index("槽")
    idx_port = headers.index("端口")
    idx_stat = headers.index("状态")

    # ne -> slot -> set of ports with online ONU
    ne_port_online = defaultdict(lambda: defaultdict(set))   # (slot, port) -> has online
    ne_port_any    = defaultdict(lambda: defaultdict(set))   # (slot, port) -> has any record
    ne_slots       = defaultdict(set)                        # ne -> set of slots seen

    for row in ws.iter_rows(min_row=2, values_only=True):
        ne = str(row[idx_ne]).strip()
        try:
            slot = int(row[idx_slot])
            port = int(row[idx_port])
        except (TypeError, ValueError):
            continue
        status = str(row[idx_stat]).strip()

        ne_port_any[ne][(slot, port)].add(port)
        ne_slots[ne].add(slot)
        if status == "在线":
            ne_port_online[ne][(slot, port)].add(port)

    results = []
    for ne in sorted(ne_port_any):
        up_biz    = 0
        dn_biz    = 0
        dn_no_biz = 0

        for sp_key in ne_port_any[ne]:
            if sp_key in ne_port_online[ne]:
                up_biz += 1
            else:
                dn_biz += 1

        for slot in ne_slots[ne]:
            present = {p for (s, p) in ne_port_any[ne] if s == slot}
            dn_no_biz += TOTAL_PORTS_PER_SLOT - len(present)

        results.append((ne, up_biz, 0, dn_biz, dn_no_biz))

    return results


def main():
    if len(sys.argv) < 2:
        print("用法：python analyze_ports.py <onu_excel路径>")
        sys.exit(1)

    try:
        results = analyze(sys.argv[1])
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)

    print("网元名称|UP且有业务|UP但无业务|Down但配置有业务|Down且无业务")
    for row in results:
        print("|".join(str(x) for x in row))


if __name__ == "__main__":
    main()
