"""指标模块占位。"""


class Metrics:
    """指标上报占位。"""

    def inc(self, name: str) -> None:
        """指标计数（TODO：接入 Prometheus）。"""
        raise NotImplementedError('Metrics.inc 未实现')
