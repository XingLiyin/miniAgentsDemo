"""原子文件读写工具函数。

所有 JSON 写入先写 .tmp 再 os.replace()，防止写入中途崩溃导致文件损坏。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """原子写入 JSON 文件（先写 .tmp，再 rename）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, suffix=".tmp",
        delete=False, encoding="utf-8",
    ) as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=None))
        tmp = Path(f.name)
    # Windows: os.replace can fail with PermissionError if the destination file
    # is momentarily locked by another reader (SSE poll, antivirus, etc.).
    # Retry with exponential backoff before giving up.
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (2 ** attempt))  # 20ms, 40ms, 80ms, 160ms


def read_json(path: Path) -> dict[str, Any] | None:
    """读取 JSON 文件，文件不存在返回 None。"""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    """原子写入 .jsonl 文件（先写 .tmp，再 rename；与 write_json_atomic 同策略）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    if content:
        content += "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, suffix=".tmp",
        delete=False, encoding="utf-8",
    ) as f:
        f.write(content)
        tmp = Path(f.name)
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (2 ** attempt))


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """追加一行 JSON 到 .jsonl 文件（append 模式，文件不存在自动创建）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 .jsonl 文件，返回所有记录列表；文件不存在返回空列表。"""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def list_json_ids(directory: Path) -> list[str]:
    """列出目录下所有 .json 文件的 stem（不含扩展名），即各实体 ID。"""
    if not directory.exists():
        return []
    return [p.stem for p in directory.glob("*.json")]
