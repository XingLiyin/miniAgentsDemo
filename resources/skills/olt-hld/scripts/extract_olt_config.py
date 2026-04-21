"""
OLT CFG 配置提取脚本
从华为OLT的cfg文件中提取业务单板配置，生成A机房原始配置表Excel。

使用方式：
    python extract_olt_config.py <cfg文件路径> <board_port_mapping.xlsx路径> [输出文件路径]

示例：
    python extract_olt_config.py room_a.cfg references/board_port_mapping.xlsx room_a_config.xlsx
"""

import re
import sys
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


# MA5800 业务槽位范围：0-17，高槽位（≥18）均为电源/风扇/上联，直接跳过
MAX_SERVICE_SLOT = 17


def parse_boards(cfg_text):
    """
    提取 board add 0/<slot> <board_type> 记录。
    返回：{slot_int: board_type_str}，槽位号 > MAX_SERVICE_SLOT 的跳过。
    """
    boards = {}
    pattern = re.compile(r'^\s*board add\s+\d+/(\d+)\s+(\S+)', re.MULTILINE)
    for match in pattern.finditer(cfg_text):
        slot = int(match.group(1))
        board_type = match.group(2)
        if slot <= MAX_SERVICE_SLOT:
            boards[slot] = board_type
    return boards


def parse_used_ports(cfg_text, slot):
    """
    提取指定槽位下有业务的不同端口号集合。
    定位：interface gpon 0/<slot> 开头，下一个 interface gpon 0/ 或文件末尾结束。
    统计片段内 ont add <port_no> ... 中不同的端口号数量。
    返回：set of int
    """
    start_pat = re.compile(r'^\s*interface gpon 0/' + str(slot) + r'\b', re.MULTILINE)
    start_match = start_pat.search(cfg_text)
    if not start_match:
        return set()

    # Block ends at the next 'interface gpon 0/' section or end of file
    next_pat = re.compile(r'^\s*interface gpon 0/', re.MULTILINE)
    next_match = next_pat.search(cfg_text, start_match.end())
    end = next_match.start() if next_match else len(cfg_text)

    block = cfg_text[start_match.start():end]
    port_pattern = re.compile(r'^\s*ont add\s+(\d+)', re.MULTILINE)
    return {int(m.group(1)) for m in port_pattern.finditer(block)}


def load_port_type_map(xlsx_path):
    """
    读取单板型号→端口类型映射表。
    Excel格式：第一列=单板型号，第二列=端口类型，首行为表头。
    返回：{board_type_str: port_type_str}
    """
    wb = load_workbook(xlsx_path, read_only=True)
    ws = wb.active
    mapping = {}
    first_row = True
    for row in ws.iter_rows(values_only=True):
        if first_row:
            first_row = False
            continue
        if row[0] and row[1]:
            mapping[str(row[0]).strip()] = str(row[1]).strip()
    return mapping


def extract_config(cfg_path, port_map_path):
    """
    主提取函数。
    返回：list of dict，每项对应一块业务单板，按槽位升序排列。
    """
    with open(cfg_path, 'r', encoding='utf-8', errors='ignore') as f:
        cfg_text = f.read()

    port_type_map = load_port_type_map(port_map_path)
    boards = parse_boards(cfg_text)

    results = []
    for slot in sorted(boards.keys()):
        board_type = boards[slot]
        port_type = port_type_map.get(board_type, "未知")
        used_ports = parse_used_ports(cfg_text, slot)
        results.append({
            "槽位":       slot,
            "单板类型":   board_type,
            "端口类型":   port_type,
            "单板端口数": 16,
            "使用端口数": len(used_ports),
        })
    return results


def write_excel(results, output_path):
    """
    将提取结果写入Excel，严格按输出模板：5列，首行表头，无其他装饰。
    """
    C_MID   = "2E75B6"
    C_WHITE = "FFFFFF"
    thin = Side(style="thin", color="AAAAAA")
    BD = Border(left=thin, right=thin, top=thin, bottom=thin)

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

    for ri, row in enumerate(results, start=2):
        for ci, key in enumerate(["槽位", "单板类型", "端口类型", "单板端口数", "使用端口数"], 1):
            c = ws.cell(row=ri, column=ci, value=row[key])
            c.font = Font(name="Arial", size=10)
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = BD

    ws.freeze_panes = "A2"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def to_markdown_table(results):
    """Convert results list to a Markdown table string."""
    headers = ["槽位", "单板类型", "端口类型", "单板端口数", "使用端口数"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for r in results:
        lines.append("| " + " | ".join(str(r[h]) for h in headers) + " |")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print("用法：python extract_olt_config.py <cfg文件> <board_port_mapping.xlsx> [输出文件.xlsx]")
        sys.exit(1)

    cfg_path      = sys.argv[1]
    port_map_path = sys.argv[2]
    output_path   = sys.argv[3] if len(sys.argv) > 3 else "room_a_config.xlsx"

    results = extract_config(cfg_path, port_map_path)
    write_excel(results, output_path)
    print(to_markdown_table(results))


if __name__ == "__main__":
    main()
