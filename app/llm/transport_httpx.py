"""基于 httpx 的简易 Transport 实现。"""

from __future__ import annotations

from typing import Any, Dict

import httpx

from app.llm.llm_base import Transport


class HttpxTransport(Transport):
    """使用 httpx 的同步 Transport。"""

    def __init__(self, timeout: int = 60) -> None:
        """创建 Transport，默认超时 60 秒。"""
        self._timeout = timeout

    def post(self, url: str, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        """发送 POST 请求并返回 JSON 响应。"""
        try:
            with httpx.Client(timeout=timeout or self._timeout) as client:
                resp = client.post(url, headers=headers, json=json)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f'HTTP 状态错误: {exc.response.status_code}') from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError('HTTP 请求超时') from exc
        except httpx.RequestError as exc:
            raise RuntimeError('HTTP 请求失败') from exc
