"""Workspace 文件浏览器路由（桌面版前端使用）。"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter()


def _workspace_root() -> Path:
    from app.config.settings import get_settings
    base = get_settings().workspace_base_dir
    return Path(base).resolve() if base else Path.cwd().resolve()


def _safe_resolve(root: Path, rel: str) -> Path:
    target = (root / rel).resolve() if rel else root
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path outside workspace")
    return target


@router.get("/files")
def list_files(path: str = Query("", description="Relative path from workspace root, or absolute path used as root")):
    abs_path = Path(path) if path else None
    use_absolute = bool(abs_path and abs_path.is_absolute())
    if use_absolute:
        root = abs_path.resolve()
        target = root
    else:
        root = _workspace_root()
        target = _safe_resolve(root, path)

    if not target.exists():
        return {"root": str(root), "path": path, "parent": "", "entries": []}
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    def _entry_path(item: Path) -> str:
        # 用绝对路径模式时，返回子项的绝对路径，保持后续导航一致
        return str(item) if use_absolute else item.relative_to(root).as_posix()

    entries = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.name.startswith('.'):
                continue
            try:
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "path": _entry_path(item),
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if item.is_file() else None,
                })
            except (PermissionError, OSError):
                pass
    except PermissionError:
        pass

    current_rel = target.relative_to(root).as_posix() if target != root else ""
    parent_rel = ""
    if target != root:
        try:
            parent = target.parent
            parent_rel = str(parent) if use_absolute else (parent.relative_to(root).as_posix() if parent != root else "")
        except ValueError:
            parent_rel = ""

    return {
        "root": str(root),
        "path": current_rel,
        "parent": parent_rel,
        "entries": entries,
    }


@router.get("/file")
def read_file(path: str = Query(...)):
    abs_path = Path(path)
    if abs_path.is_absolute():
        root = abs_path.parent.resolve()
        target = abs_path.resolve()
    else:
        root = _workspace_root()
        target = _safe_resolve(root, path)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target.stat().st_size > 1_048_576:
        raise HTTPException(status_code=413, detail="File too large (>1 MB)")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"path": path, "content": content}


@router.get("/file/raw")
def read_file_raw(path: str = Query(...)):
    abs_path = Path(path)
    if abs_path.is_absolute():
        target = abs_path.resolve()
    else:
        root = _workspace_root()
        target = _safe_resolve(root, path)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # Cap at 50 MB: FileResponse streams (zero backend memory pressure), but the
    # browser still has to load + parse the bytes (mammoth/xlsx are in-memory).
    # 50 MB covers image-heavy office docs while protecting against accidentally
    # opening huge binaries (videos, archives) in a preview pane.
    if target.stat().st_size > 52_428_800:
        raise HTTPException(status_code=413, detail="File too large for preview (>50 MB)")

    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(str(target), media_type=mime or "application/octet-stream", filename=target.name)
