"""数据库会话抽象。"""


class DBSession:
    """数据库会话占位。"""

    def __init__(self) -> None:
        """创建数据库会话（TODO：接入 SQLAlchemy）。"""
        raise NotImplementedError('DBSession 未实现')
