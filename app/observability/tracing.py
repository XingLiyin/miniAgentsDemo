"""Tracing 模块占位。"""


class Tracer:
    """链路追踪占位。"""

    def start_span(self, name: str):
        """开启 span（TODO：接入 OpenTelemetry）。"""
        raise NotImplementedError('Tracer.start_span 未实现')
