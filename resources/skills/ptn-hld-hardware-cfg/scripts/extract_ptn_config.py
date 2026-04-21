"""
PTN HLD 配置提取脚本
根据用户选择的网元名称，从各报表Excel中提取A机房LPU单板配置。
若设备需EOX替换，同时输出新设备的候选单板列表，供Claude进行语义匹配生成B机房配置表。

使用方式：
    python extract_ptn_config.py <input_file（网元名称）> <references目录路径>

输出（stdout，依次打印）：
    === TABLE_A ===          A机房配置Markdown表格
    === NEEDS_REPLACE ===    true 或 false
    === NEW_DEVICE_TYPE ===  新设备类型（needs_replace=true时有效）
    === CANDIDATES ===       新设备候选LPU单板列表Markdown（needs_replace=true时有效）
    === HARDWARE_CHANGE ===  硬件方案文字描述
"""

import sys
import os
import pandas as pd

REFS = None

def load_refs(refs_dir):
    global REFS
    REFS = refs_dir

def read_excel(filename, **kwargs):
    # keep_default_na=False: 空单元格读成空字符串而非 NaN，
    # 避免对 float NaN 调用 .strip() 报 AttributeError
    kwargs.setdefault("keep_default_na", False)
    kwargs.setdefault("dtype", str)
    return pd.read_excel(os.path.join(REFS, filename), **kwargs)

def get_device_type(element_name):
    """从单板报表中获取该网元的子架类型（取第一条匹配行的子架类型列）。"""
    df = read_excel("单板报表.xlsx", dtype=str)
    df.columns = df.columns.str.strip()
    rows = df[df["所属网元"].str.strip() == element_name]
    if rows.empty:
        return None
    val = str(rows.iloc[0]["子架类型"]).strip()
    return val if val else None

def get_lpu_boards(element_name):
    """
    从单板报表中筛选该网元的所有LPU单板。
    LPU判断：单板描述匹配设备型号和业务单板对照表中单板类型=LPU的记录。
    返回：list of dict，含 槽位号、单板描述、类型，按槽位升序排列。
    """
    boards_df = read_excel("单板报表.xlsx", dtype=str)
    boards_df.columns = boards_df.columns.str.strip()
    element_boards = boards_df[boards_df["所属网元"].str.strip() == element_name].copy()

    ref_df = read_excel("设备型号和业务单板对照表.xlsx", dtype=str)
    ref_df.columns = ref_df.columns.str.strip()
    lpu_descs = set(
        ref_df[ref_df["单板类型"].str.strip().str.upper() == "LPU"]["单板描述"].str.strip()
    )

    lpu_boards = element_boards[element_boards["单板描述"].str.strip().isin(lpu_descs)]

    result = []
    for _, row in lpu_boards.iterrows():
        result.append({
            "槽位号":   str(row["槽位号"]).strip(),
            "单板描述": str(row["单板描述"]).strip(),
            "类型":     "LPU",
        })

    try:
        result.sort(key=lambda x: int(x["槽位号"]))
    except ValueError:
        result.sort(key=lambda x: x["槽位号"])

    return result

def check_eox_replacement(device_type):
    """检查设备是否需要EOX替换。返回：(needs_replace: bool, new_device_type: str or None)"""
    df = read_excel("老旧设备EOX替换表.xlsx", dtype=str)
    df.columns = df.columns.str.strip()
    row = df[df["老旧设备名称"].str.strip() == device_type]
    if row.empty:
        return False, None
    return True, str(row.iloc[0]["更新设备名称"]).strip()

def get_candidate_boards(new_device_type):
    """
    从设备型号和业务单板对照表中取出新设备类型的所有LPU候选单板（去重）。
    返回：list of dict，含 单板名称、单板描述。
    """
    df = read_excel("设备型号和业务单板对照表.xlsx", dtype=str)
    df.columns = df.columns.str.strip()

    candidates = df[
        (df["设备类型"].str.strip() == new_device_type) &
        (df["单板类型"].str.strip().str.upper() == "LPU")
    ]

    result, seen = [], set()
    for _, row in candidates.iterrows():
        desc = str(row["单板描述"]).strip()
        name = str(row["替换单板描述"]).strip() if "替换单板描述" in candidates.columns else ""
        if desc not in seen:
            seen.add(desc)
            result.append({"单板名称": name, "单板描述": desc})
    return result

def make_board_table(device_type, boards):
    lines = [
        f"| **设备类型** | **{device_type}** |  |",
        "| --- | --- | --- |",
        "| **槽位** | **单板描述** | **类型** |",
    ]
    for b in boards:
        lines.append(f"| {b['槽位号']} | {b['类型']} {b['单板描述']} | {b['类型']} |")
    return "\n".join(lines)

def make_candidate_table(candidates):
    lines = ["| 单板名称 | 单板描述 |", "| --- | --- |"]
    for c in candidates:
        lines.append(f"| {c['单板名称']} | {c['单板描述']} |")
    return "\n".join(lines)

def main():
    if len(sys.argv) < 3:
        print("用法：python extract_ptn_config.py <网元名称> <references目录路径>")
        sys.exit(1)

    element_name = sys.argv[1]
    load_refs(sys.argv[2])

    device_type = get_device_type(element_name)
    if not device_type:
        print(f"错误：在网元报表中未找到网元「{element_name}」", file=sys.stderr)
        sys.exit(1)

    lpu_boards = get_lpu_boards(element_name)
    needs_replace, new_device_type = check_eox_replacement(device_type)

    print("=== TABLE_A ===")
    print(make_board_table(device_type, lpu_boards))
    print("=== NEEDS_REPLACE ===")
    print("true" if needs_replace else "false")
    print("=== NEW_DEVICE_TYPE ===")
    print(new_device_type if needs_replace else "")
    print("=== CANDIDATES ===")
    if needs_replace:
        print(make_candidate_table(get_candidate_boards(new_device_type)))
    else:
        print("")
    print("=== HARDWARE_CHANGE ===")
    if needs_replace:
        print(f"将 {device_type} 替换为 {new_device_type}。")
    else:
        print("现网设备无需替换。")

if __name__ == "__main__":
    main()
