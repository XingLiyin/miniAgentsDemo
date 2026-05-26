"""Generate electron/assets/icon.ico by rendering resources/brand/icon.svg via cairosvg."""
import os, io, struct
from PIL import Image
import cairosvg

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
svg_path = os.path.join(root, 'packaging', 'brand', 'icon.svg')
out_path = os.path.join(root, 'electron', 'assets', 'icon.ico')
os.makedirs(os.path.dirname(out_path), exist_ok=True)

sizes = [256, 128, 64, 48, 32, 16]

def render(size):
    png = cairosvg.svg2png(url=svg_path, output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert('RGBA')

frames = {s: render(s) for s in sizes}

# Build ICO with PNG-compressed entries (valid for all sizes including 256)
entries_data = []
for s in sizes:
    buf = io.BytesIO()
    frames[s].save(buf, format='PNG')
    entries_data.append(buf.getvalue())

n = len(sizes)
header = struct.pack('<HHH', 0, 1, n)
offset = 6 + n * 16
dir_entries = b''
for s, data in zip(sizes, entries_data):
    w = 0 if s == 256 else s
    h = 0 if s == 256 else s
    dir_entries += struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(data), offset)
    offset += len(data)

with open(out_path, 'wb') as f:
    f.write(header + dir_entries + b''.join(entries_data))

kb = os.path.getsize(out_path) // 1024
print(f'OK: {out_path} ({kb} KB)')
