#!/usr/bin/env python3
"""生成 BNG HLD 搬迁所需的模拟 Excel 数据文件"""

import os

try:
    import openpyxl
except ImportError:
    print("openpyxl not available, falling back to CSV")
    openpyxl = None

def create_old_room_board_config():
    """创建旧机房单板配置表 - 华为 ME60-X16 BNG 设备"""
    
    # 表头
    headers = [
        "槽位号", "单板型号", "单板类型", "单板描述", "端口数量",
        "端口类型", "是否在用", "EOX状态", "序列号", "备注"
    ]
    
    # 模拟数据：一台 ME60-X16 搬迁前配置
    # 槽位 0-3: MPU/SFU, 槽位 4-15: LPU 业务板
    rows = [
        [0, "CR52-MPUB", "主控板", "主处理板MPUB", 0, "N/A", "是", "EOX", "SN-MPUB-001", "需替换"],
        [1, "CR52-MPUB", "主控板", "主处理板MPUB", 0, "N/A", "是", "EOX", "SN-MPUB-002", "需替换"],
        [2, "CR52-SFUB", "交换网板", "交换网板SFUB", 0, "N/A", "是", "EOX", "SN-SFUB-001", "需替换"],
        [3, "CR52-SFUB", "交换网板", "交换网板SFUB", 0, "N/A", "是", "EOX", "SN-SFUB-002", "需替换"],
        [4, "CR52-SFUB", "交换网板", "交换网板SFUB", 0, "N/A", "是", "正常", "SN-SFUB-003", ""],
        [5, "CR52-P4CF", "业务板", "4端口OC-3/STM-1 POS", 4, "OC-3/STM-1", "否", "正常", "SN-P4CF-001", "未使用槽位"],
        [6, "CR52-P2CF", "业务板", "2端口OC-12/STM-4 POS", 2, "OC-12/STM-4", "否", "正常", "SN-P2CF-001", "未使用槽位"],
        [7, "CR52-E8GF", "业务板", "8端口千兆以太网光接口板", 8, "1000BASE-X", "是", "EOX", "SN-E8GF-001", "需替换"},
        [8, "CR52-E8GF", "业务板", "8端口千兆以太网光接口板", 8, "1000BASE-X", "是", "EOX", "SN-E8GF-002", "需替换"},
        [9, "CR52-P4CF", "业务板", "4端口OC-3/STM-1 POS", 4, "OC-3/STM-1", "否", "正常", "SN-P4CF-002", "未使用槽位"},
        [10, "CR52-E24GF", "业务板", "24端口千兆以太网光接口板", 24, "1000BASE-X", "是", "EOX", "SN-E24GF-001", "需替换"},
        [11, "CR52-E24GF", "业务板", "24端口千兆以太网光接口板", 24, "1000BASE-X", "是", "EOX", "SN-E24GF-002", "需替换"},
        [12, "CR52-E12GF", "业务板", "12端口千兆以太网光接口板", 12, "1000BASE-X", "是", "正常", "SN-E12GF-001", "在网正常"},
        [13, "CR52-E12GF", "业务板", "12端口千兆以太网光接口板", 12, "1000BASE-X", "否", "正常", "SN-E12GF-002", "空闲"},
        [14, "CR52-P1CF", "业务板", "1端口OC-48/STM-16 POS", 1, "OC-48/STM-16", "否", "正常", "SN-P1CF-001", "未使用槽位"},
        [15, "CR52-E8GF", "业务板", "8端口千兆以太网光接口板", 8, "1000BASE-X", "是", "EOX", "SN-E8GF-003", "需替换"},
    ]
    
    filename = "旧机房单板配置表_ME60-X16.xlsx"
    
    if openpyxl:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "旧机房单板配置"
        
        # 写入表头
        for col, header in enumerate(headers, 1):
            ws.cell(row=1, column=col, value=header)
        
        # 写入数据
        for row_idx, row_data in enumerate(rows, 2):
            for col_idx, value in enumerate(row_data, 1):
                ws.cell(row=row_idx, column=col_idx, value=value)
        
        # 调整列宽
        for col in range(1, len(headers) + 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 18
        
        wb.save(filename)
        print(f"Created: {filename}")
    else:
        # Fallback to CSV
        csv_filename = filename.replace('.xlsx', '.csv')
        with open(csv_filename, 'w', encoding='utf-8-sig') as f:
            f.write(','.join(headers) + '\n')
            for row in rows:
                f.write(','.join(str(v) for v in row) + '\n')
        print(f"Created (CSV fallback): {csv_filename}")
    
    return filename if openpyxl else csv_filename


def create_olt_info():
    """创建下挂 OLT 信息表"""
    headers = ["OLT名称", "OLT IP", "所属BNG", "上联端口", "用户类型", "用户数量", "VLAN范围", "备注"]
    
    rows = [
        ["OLT-BJ-DX-01", "10.1.1.1", "BNG-ME60-01", "GE7/0/0", "PPPoE", 1200, "1001-1100", ""],
        ["OLT-BJ-DX-02", "10.1.1.2", "BNG-ME60-01", "GE7/0/1", "PPPoE", 980, "1101-1200", ""],
        ["OLT-BJ-DX-03", "10.1.1.3", "BNG-ME60-01", "GE7/0/2", "PPPoE+IPTV", 1500, "1201-1300", ""],
        ["OLT-BJ-DX-04", "10.1.1.4", "BNG-ME60-01", "GE7/0/3", "PPPoE", 800, "1301-1400", ""],
        ["OLT-BJ-DX-05", "10.1.1.5", "BNG-ME60-01", "GE8/0/0", "DHCP", 1100, "1401-1500", ""],
        ["OLT-BJ-DX-06", "10.1.1.6", "BNG-ME60-01", "GE8/0/1", "DHCP+IPTV", 1300, "1501-1600", ""],
        ["OLT-BJ-DX-07", "10.1.1.7", "BNG-ME60-01", "GE8/0/2", "PPPoE", 950, "1601-1700", ""],
        ["OLT-BJ-DX-08", "10.1.1.8", "BNG-ME60-01", "GE8/0/3", "PPPoE", 1050, "1701-1800", ""],
        ["OLT-BJ-DX-09", "10.1.1.9", "BNG-ME60-01", "GE10/0/0", "专线+静态", 200, "1801-1850", "专线用户"],
        ["OLT-BJ-DX-10", "10.1.1.10", "BNG-ME60-01", "GE10/0/1", "专线+静态", 180, "1851-1900", "专线用户"],
        ["OLT-BJ-DX-11", "10.1.1.11", "BNG-ME60-01", "GE10/0/2", "PPPoE", 880, "1901-2000", ""],
        ["OLT-BJ-DX-12", "10.1.1.12", "BNG-ME60-01", "GE10/0/3", "PPPoE", 920, "2001-2100", ""],
        ["OLT-BJ-DX-13", "10.1.1.13", "BNG-ME60-01", "GE11/0/0", "PPPoE", 780, "2101-2200", ""],
        ["OLT-BJ-DX-14", "10.1.1.14", "BNG-ME60-01", "GE11/0/1", "DHCP", 1150, "2201-2300", ""],
        ["OLT-BJ-DX-15", "10.1.1.15", "BNG-ME60-01", "GE11/0/2", "PPPoE", 1020, "2301-2400", ""],
        ["OLT-BJ-DX-16", "10.1.1.16", "BNG-ME60-01", "GE11/0/3", "PPPoE", 860, "2401-2500", ""],
        ["OLT-BJ-DX-17", "10.1.1.17", "BNG-ME60-01", "GE12/0/0", "PPPoE+IPTV", 1400, "2501-2600", ""],
        ["OLT-BJ-DX-18", "10.1.1.18", "BNG-ME60-01", "GE12/0/1", "PPPoE", 750, "2601-2700", ""],
        ["OLT-BJ-DX-19", "10.1.1.19", "BNG-ME60-01", "GE12/0/2", "DHCP", 1080, "2701-2800", ""],
        ["OLT-BJ-DX-20", "10.1.1.20", "BNG-ME60-01", "GE12/0/3", "PPPoE", 900, "2801-2900", ""],
        ["OLT-BJ-DX-21", "10.1.1.21", "BNG-ME60-01", "GE15/0/0", "PPPoE", 850, "2901-3000", ""],
        ["OLT-BJ-DX-22", "10.1.1.22", "BNG-ME60-01", "GE15/0/1", "PPPoE", 960, "3001-3100", ""],
    ]
    
    filename = "下挂OLT信息表.xlsx"
    
    if openpyxl:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "下挂OLT信息"
        for col, header in enumerate(headers, 1):
            ws.cell(row=1, column=col, value=header)
        for row_idx, row_data in enumerate(rows, 2):
            for col_idx, value in enumerate(row_data, 1):
                ws.cell(row=row_idx, column=col_idx, value=value)
        for col in range(1, len(headers) + 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 18
        wb.save(filename)
        print(f"Created: {filename}")
    else:
        csv_filename = filename.replace('.xlsx', '.csv')
        with open(csv_filename, 'w', encoding='utf-8-sig') as f:
            f.write(','.join(headers) + '\n')
            for row in rows:
                f.write(','.join(str(v) for v in row) + '\n')
        print(f"Created (CSV fallback): {csv_filename}")
    
    return filename if openpyxl else csv_filename


if __name__ == "__main__":
    print("Generating mock data for BNG HLD relocation...")
    f1 = create_old_room_board_config()
    f2 = create_olt_info()
    print(f"\nDone! Files created:")
    print(f"  1. {f1}")
    print(f"  2. {f2}")
