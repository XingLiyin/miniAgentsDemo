"""产物存储抽象。"""


class ArtifactStore:
    """产物存储占位。"""

    def put(self, key: str, content: bytes) -> str:
        """保存产物并返回 key（TODO：接入对象存储）。"""
        raise NotImplementedError('ArtifactStore.put 未实现')
