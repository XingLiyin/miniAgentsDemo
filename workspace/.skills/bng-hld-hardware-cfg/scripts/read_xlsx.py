"""Read xlsx files without pandas - try openpyxl first, then fallback to zip+xml"""
import sys
import os

# Resolve paths relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)

def resolve_path(path):
    """Resolve path: absolute paths stay, relative paths are relative to skill dir"""
    if os.path.isabs(path):
        return path
    # Try relative to skill dir first
    candidate = os.path.join(SKILL_DIR, path)
    if os.path.exists(candidate):
        return candidate
    # Try as-is from CWD
    return path

def read_xlsx_openpyxl(path):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append([str(c) if c is not None else "" for c in row])
        wb.close()
        return rows
    except ImportError:
        return None

def read_xlsx_zip(path):
    """Fallback: parse xlsx as zip+XML"""
    import zipfile
    from xml.etree import ElementTree as ET
    
    with zipfile.ZipFile(path, 'r') as z:
        # Read shared strings
        try:
            ss_xml = z.read('xl/sharedStrings.xml')
            ss_tree = ET.fromstring(ss_xml)
            ns = {'ns': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            shared_strings = []
            for si in ss_tree.findall('.//ns:si', ns):
                texts = [t.text or '' for t in si.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')]
                shared_strings.append(''.join(texts))
        except:
            shared_strings = []
        
        # Read sheet data
        sheet_xml = z.read('xl/worksheets/sheet1.xml')
        sheet_tree = ET.fromstring(sheet_xml)
        
        ns2 = {'ns': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        rows = []
        for row_elem in sheet_tree.findall('.//ns:row', ns2):
            row_data = {}
            for cell in row_elem.findall('ns:c', ns2):
                ref = cell.get('r')
                col_letter = ''.join(c for c in ref if c.isalpha())
                col_idx = ord(col_letter) - ord('A') if len(col_letter) == 1 else (ord(col_letter[0])-ord('A')+1)*26 + (ord(col_letter[1])-ord('A'))
                cell_type = cell.get('t')
                value = cell.find('ns:v', ns2)
                if value is not None and value.text:
                    if cell_type == 's':
                        idx = int(value.text)
                        row_data[col_idx] = shared_strings[idx] if idx < len(shared_strings) else ''
                    else:
                        row_data[col_idx] = value.text
                else:
                    row_data[col_idx] = ''
            
            if row_data:
                max_col = max(row_data.keys())
                row_list = [row_data.get(i, '') for i in range(max_col + 1)]
                rows.append(row_list)
        
        return rows

if __name__ == "__main__":
    path = resolve_path(sys.argv[1])
    rows = read_xlsx_openpyxl(path)
    if rows is None:
        rows = read_xlsx_zip(path)
    
    for row in rows[:100]:
        print("|" + "|".join(str(c) for c in row) + "|")
