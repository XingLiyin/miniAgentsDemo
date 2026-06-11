"""基于 httpx 的 Transport 实现（同步 + SSE 流式）。"""

from __future__ import annotations

from typing import Any, Dict, Iterator

import httpx

from app.llm.base import Transport, StreamTransport
from app.common.ssl_verify import make_ssl_verify


class HttpxTransport(Transport, StreamTransport):
    """使用 httpx 的同步 Transport，同时支持 SSE 流式。"""

    def __init__(self, timeout: int = 60) -> None:
        self._timeout = timeout

    def post(self, url: str, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        """发送 POST 请求并返回 JSON 响应（非流式）。"""
        from app.common.ssl_verify import with_ssl_retry

        def _do(verify):
            with httpx.Client(timeout=timeout or self._timeout, trust_env=False, verify=verify) as client:
                resp = client.post(url, headers=headers, json=json)
                resp.raise_for_status()
                return resp.json()

        try:
            return with_ssl_retry(_do, url)
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f'HTTP 状态错误: {exc.response.status_code} {exc.response.text}') from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError('HTTP 请求超时') from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f'HTTP 请求失败: {exc}') from exc

    def stream_post(
        self,
        url: str,
        headers: Dict[str, str],
        json: Dict[str, Any],
        timeout: int,
    ) -> Iterator[str]:
        """发送 POST 请求，以迭代器逐行产出 SSE data 行内容（已去除 'data: ' 前缀）。

        调用方负责在 for 循环中消费，本方法在整个迭代完成前保持连接开启。
        """
        try:
            with httpx.Client(timeout=timeout or self._timeout, trust_env=False, verify=make_ssl_verify()) as client:
                with client.stream('POST', url, headers=headers, json=json) as resp:
                    if resp.status_code >= 400:
                        resp.read()   # 必须先读 body，否则流式上下文下 text 为空
                        raise RuntimeError(
                            f'HTTP 状态错误: {resp.status_code} {resp.text}'
                        )
                    for raw_line in resp.iter_lines():
                        line = raw_line.strip()
                        if not line or not line.startswith('data:'):
                            continue
                        payload = line[len('data:'):].strip()
                        if payload == '[DONE]':
                            return
                        yield payload
        except RuntimeError:
            raise
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f'HTTP 状态错误: {exc.response.status_code}') from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError('HTTP 流式请求超时') from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f'HTTP 流式请求失败: {exc}') from exc
