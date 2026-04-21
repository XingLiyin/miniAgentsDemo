"""
B机房配置表生成脚本
根据A机房配置表，按搬迁原则计算合并方案，生成B机房新配置表Excel。

使用方式：
    python generate_b_room_config.py <room_a_config.xlsx> [输出文件路径]

示例：
    python generate_b_room_config.py room_a_config.xlsx room_b_config.xlsx
"""

import math
import sys
import copy
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


BOARD_PORTS  = 16          # 每块单板端口数（固定）
GPON_REPLACE = "H907CGHF"  # GPON单板统一替换为此型号


def load_a_room(xlsx_path):
    """
    读取A机房配置表（5列格式：槽位/单板类型/端口类型/单板端口数/使用端口数）。
    首行为表头，跳过空行。
    返回：list of dict
    """
    wb = load_workbook(xlsx_path, read_only=True)
    ws = wb.active
    results = []
    header_found = False
    for row in ws.iter_rows(values_only=True):
        if not header_found:
            if row[0] == "槽位":
                header_found = True
            continue
        if row[0] is None:
            continue
        try:
            slot = int(row[0])
        except (TypeError, ValueError):
            continue
        results.append({
            "槽位":       slot,
            "单板类型":   str(row[1]).strip(),
            "端口类型":   str(row[2]).strip(),
            "单板端口数": int(row[3]) if row[3] else BOARD_PORTS,
            "使用端口数": int(row[4]) if row[4] else 0,
        })
    return results


def calc_min_boards(boards):
    """计算最少需要的单板数。"""
    total_used = sum(b["使用端口数"] for b in boards)
    return math.ceil(total_used / BOARD_PORTS), total_used


def merge_boards(boards, min_boards):
    """
    执行单板合并，返回合并后的单板列表。

    合并规则：
    - 每轮找使用端口数最少的单板作为被合并单板
    - 找空闲端口数最多的单板作为目标单板
    - 将被合并单板的业务迁入目标单板（端口号允许不连续）
    - 若目标单板空闲不足，拆分迁入多块目标单板
    - 持续合并直到剩余单板数 = min_boards
    """
    pool = copy.deepcopy(boards)
    for b in pool:
        b["空闲端口数"] = b["单板端口数"] - b["使用端口数"]
        b["退出"] = False

    while True:
        active = [b for b in pool if not b["退出"]]
        if len(active) <= min_boards:
            break

        src = sorted(active, key=lambda x: (x["使用端口数"], -x["槽位"]))[0]
        candidates = sorted(
            [b for b in active if b["槽位"] != src["槽位"]],
            key=lambda x: x["空闲端口数"],
            reverse=True
        )

        remaining = src["使用端口数"]
        for tgt in candidates:
            if remaining <= 0:
                break
            if tgt["空闲端口数"] <= 0:
                continue
            take = min(remaining, tgt["空闲端口数"])
            tgt["使用端口数"] += take
            tgt["空闲端口数"] -= take
            remaining -= take

        src["退出"] = True

    return pool


def determine_new_board_type(orig_board, orig_ptype):
    """原则3：GPON → H907CGHF，10GPON保留原型号。"""
    if orig_ptype == "GPON":
        return GPON_REPLACE, "10GPON"
    return orig_board, orig_ptype


def write_excel(pool, output_path):
    """
    生成B机房配置表Excel，严格按输出模板：5列，首行表头，无其他装饰。
    """
    C_MID   = "2E75B6"
    C_WHITE = "FFFFFF"
    thin = Side(style="thin", color="AAAAAA")
    BD = Border(left=thin, right=thin, top=thin, bottom=thin)

    result_boards = sorted([b for b in pool if not b["退出"]], key=lambda x: x["槽位"])
    orig_map = {b["槽位"]: b for b in pool}

    wb = Workbook()
    ws = wb.active
    ws.title = "配置表"

    headers = ["槽位", "单板类型", "端口类型", "单板端口数", "使用端口数"]
    widths  = [8, 14, 12, 12, 12]
    for i, (h, w) in enumerate(zip(headers, widths), 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(name="Arial", bold=True, color=C_WHITE, size=10)
        c.fill = PatternFill("solid", start_color=C_MID)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = BD
        ws.column_dimensions[chr(64 + i)].width = w
    ws.row_dimensions[1].height = 22

    for ri, b in enumerate(result_boards, start=2):
        orig = orig_map[b["槽位"]]
        new_board, new_ptype = determine_new_board_type(orig["单板类型"], orig["端口类型"])
        for ci, val in enumerate([b["槽位"], new_board, new_ptype, BOARD_PORTS, b["使用端口数"]], 1):
            c = ws.cell(row=ri, column=ci, value=val)
            c.font = Font(name="Arial", size=10)
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = BD

    ws.freeze_panes = "A2"
    wb.save(output_path)


def to_markdown_table(pool):
    """Convert merged pool to a Markdown table string (B-room boards only)."""
    result_boards = sorted([b for b in pool if not b["退出"]], key=lambda x: x["槽位"])
    orig_map = {b["槽位"]: b for b in pool}
    headers = ["槽位", "单板类型", "端口类型", "单板端口数", "使用端口数"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for b in result_boards:
        orig = orig_map[b["槽位"]]
        new_board, new_ptype = determine_new_board_type(orig["单板类型"], orig["端口类型"])
        lines.append("| " + " | ".join([str(b["槽位"]), new_board, new_ptype, str(BOARD_PORTS), str(b["使用端口数"])]) + " |")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("用法：python generate_b_room_config.py <room_a_config.xlsx> [输出文件.xlsx]")
        sys.exit(1)

    a_room_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "room_b_config.xlsx"

    boards = load_a_room(a_room_path)
    min_boards, total_used = calc_min_boards(boards)
    pool = merge_boards(boards, min_boards)
    write_excel(pool, output_path)
    print(to_markdown_table(pool))


if __name__ == "__main__":
    main()
