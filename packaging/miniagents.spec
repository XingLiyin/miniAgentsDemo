# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for miniAgents.
Usage: pyinstaller miniagents.spec --noconfirm
"""
import os
from pathlib import Path

# ── GTK3 DLL 收集 ─────────────────────────────────────────────────────────────
GTK3_BIN = os.environ.get("GTK3_BIN", r"C:\Program Files\GTK3-Runtime Win64\bin")
gtk3_binaries = []
if os.path.isdir(GTK3_BIN):
    for dll in Path(GTK3_BIN).glob("*.dll"):
        gtk3_binaries.append((str(dll), "gtk3_bin"))
else:
    print(f"[WARN] GTK3_BIN not found: {GTK3_BIN}  (cairosvg may not work)")

# ── 分析 ──────────────────────────────────────────────────────────────────────
a = Analysis(
    ["_run.py"],
    pathex=["."],
    binaries=gtk3_binaries,
    datas=[
        # 已构建的前端（需先 npm run build）
        ("frontend/dist", "frontend_dist"),
        # resources/ 不打进 _internal，由 build_exe.ps1 复制到 exe 同级目录
    ],
    hiddenimports=[
        # uvicorn 内部动态导入
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.loops.uvloop",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        # anyio 后端
        "anyio._backends._asyncio",
        # starlette
        "starlette.staticfiles",
        "starlette.middleware.cors",
        # pydantic v2
        "pydantic.deprecated.class_validators",
        "pydantic.deprecated.config",
        "pydantic.deprecated.decorator",
        "pydantic.deprecated.tools",
        "pydantic_settings",
        # cairosvg & cairo
        "cairosvg",
        "cairosvg.css",
        "cairosvg.defs",
        "cairosvg.image",
        "cairosvg.path",
        "cairosvg.shapes",
        "cairosvg.structure",
        "cairosvg.svg2pdf",
        "cairosvg.svg2png",
        "cairosvg.svg2ps",
        "cairosvg.surface",
        "cairosvg.text",
        "cairosvg.url",
        # mcp
        "mcp",
        "mcp.client",
        "mcp.server",
        "mcp.types",
        # httpx
        "httpx",
        "httpx._transports.default",
        "httpx._transports.asyncio",
        # 标准库补充
        "email.mime.multipart",
        "email.mime.text",
        "multipart",
        "python_multipart",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "PIL"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="miniagents",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 与某些 DLL 不兼容，保持 False 更稳定
    console=True,       # 显示控制台，方便查看日志
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="miniagents",
)
