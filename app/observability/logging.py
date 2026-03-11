"""日志初始化模块。"""

import logging


def init_logging(level: str) -> None:
    """初始化日志基础配置。"""
    logging.basicConfig(level=level)
