"""日志初始化：统一使用 uvicorn 默认格式。"""

from __future__ import annotations

import logging
import sys


def init_logging(level: str = "INFO") -> None:
    """初始化日志，使用 uvicorn DefaultFormatter 统一格式。"""
    from uvicorn.logging import DefaultFormatter

    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter = DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False)

    root = logging.getLogger()
    root.setLevel(log_level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
