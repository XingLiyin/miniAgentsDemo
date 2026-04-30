"""Debug: list current dir and check file paths"""
import os
import sys

print("CWD:", os.getcwd())
print("Script dir:", os.path.dirname(os.path.abspath(__file__)))

# Check the reference path
ref_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references")
print("References dir:", os.path.abspath(ref_path))
print("Exists:", os.path.exists(ref_path))

if os.path.exists(ref_path):
    for f in os.listdir(ref_path):
        fpath = os.path.join(ref_path, f)
        print(f"  {f} ({os.path.getsize(fpath)} bytes)")

# Try reading procurement_replacement
proc_path = os.path.join(ref_path, "procurement_replacement.xlsx")
print("\nprocurement_replacement.xlsx absolute:", os.path.abspath(proc_path))
print("Exists:", os.path.exists(proc_path))
