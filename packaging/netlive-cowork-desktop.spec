# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for NetLIVE-CoWork desktop build.
Uses frontend-desktop/dist instead of frontend/dist.
Usage: pyinstaller netlive-cowork-desktop.spec --noconfirm
"""
import os
from pathlib import Path

# SPECPATH is the directory containing this spec file (packaging/).
# Project root is one level up.
_ROOT = str(Path(SPECPATH).parent)

GTK3_BIN = os.environ.get("GTK3_BIN", r"C:\Program Files\GTK3-Runtime Win64\bin")
gtk3_binaries = []
if os.path.isdir(GTK3_BIN):
    for dll in Path(GTK3_BIN).glob("*.dll"):
        gtk3_binaries.append((str(dll), "gtk3_bin"))
else:
    print(f"[WARN] GTK3_BIN not found: {GTK3_BIN}  (cairosvg may not work)")

a = Analysis(
    [os.path.join(_ROOT, "_run.py")],
    pathex=[_ROOT],
    binaries=gtk3_binaries,
    datas=[
        # Desktop frontend build output
        (os.path.join(_ROOT, "frontend-desktop", "dist"), "frontend_dist"),
        # resources/ is copied by the build script to dist dir
    ],
    hiddenimports=[
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
        "anyio._backends._asyncio",
        "starlette.staticfiles",
        "starlette.middleware.cors",
        "pydantic.deprecated.class_validators",
        "pydantic.deprecated.config",
        "pydantic.deprecated.decorator",
        "pydantic.deprecated.tools",
        "pydantic_settings",
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
        "mcp",
        "mcp.client",
        "mcp.server",
        "mcp.types",
        "httpx",
        "httpx._transports.default",
        "httpx._transports.asyncio",
        "email.mime.multipart",
        "email.mime.text",
        "multipart",
        "python_multipart",
        "pkg_resources",
        "setuptools",
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
    name="netlive-cowork",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # windowless for desktop
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="netlive-cowork",
)
