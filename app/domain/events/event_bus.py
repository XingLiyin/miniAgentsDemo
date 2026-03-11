"""事件总线抽象。"""


class EventBus:
    """事件总线接口。"""

    def publish(self, event_type: str, payload: dict) -> None:
        """发布事件（TODO：接入事件持久化/消息队列）。"""
        # TODO: 写入 session_events 或发到消息队列。
        raise NotImplementedError('EventBus.publish 未实现')
