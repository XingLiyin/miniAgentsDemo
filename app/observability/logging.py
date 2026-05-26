"""日志初始化：统一使用 uvicorn 默认格式。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def init_logging(level: str = "INFO", log_dir: str = "") -> None:
    """初始化日志，使用 uvicorn DefaultFormatter 统一格式。

    若 log_dir 非空，同时写入按天滚动的日志文件。
    """
    from uvicorn.logging import DefaultFormatter

    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter = DefaultFormatter("%(asctime)s %(levelprefix)s %(message)s", use_colors=False, datefmt="%Y-%m-%d %H:%M:%S")

    root = logging.getLogger()
    root.setLevel(log_level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)

    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=log_path / "netlive-cowork.log",
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
        plain_formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s")
        file_handler.setFormatter(plain_formatter)
        file_handler.setLevel(log_level)
        root.addHandler(file_handler)

    logging.getLogger(__name__).debug("Log level set to %s", level.upper())

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
