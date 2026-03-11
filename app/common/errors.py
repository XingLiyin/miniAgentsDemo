"""错误类型定义。"""


class AppError(Exception):
    """应用基础异常，携带稳定错误码。"""

    def __init__(self, code: str, message: str) -> None:
        """创建异常并记录错误码与消息。"""
        super().__init__(message)
        self.code = code
        self.message = message
