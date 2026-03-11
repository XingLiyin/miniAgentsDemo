"""幂等键处理模块。"""

from typing import Optional


class IdempotencyKey:
    """幂等键封装。"""

    def __init__(self, key: Optional[str]) -> None:
        """构造幂等键封装对象。"""
        self.key = key

    def is_present(self) -> bool:
        """判断幂等键是否存在。"""
        return bool(self.key)
