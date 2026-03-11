"""Redis 客户端抽象。"""


class RedisClient:
    """Redis 客户端占位。"""

    def get(self, key: str) -> str:
        """获取缓存（TODO：接入 redis）。"""
        raise NotImplementedError('RedisClient.get 未实现')
