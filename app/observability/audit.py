"""审计日志模块占位。"""


class AuditLogger:
    """审计日志占位。"""

    def record(self, event_type: str, payload: dict) -> None:
        """记录审计事件（TODO：写入审计日志/表）。"""
        raise NotImplementedError('AuditLogger.record 未实现')
