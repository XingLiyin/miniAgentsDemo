"""Async/sync bridging utilities."""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterable, Awaitable, Iterator
from typing import Any, TypeVar, cast

_T = TypeVar("_T")

# Reference to the main asyncio event loop, set at application startup.
# Worker threads use run_coroutine_threadsafe to schedule on this loop instead
# of creating a new one, so that AF's httpx AsyncClient (bound to the main
# loop) works correctly from inside run_in_executor threads.
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Call once at startup from an async context to capture the main loop."""
    global _main_loop
    _main_loop = loop


def run_awaitable_sync(awaitable: Awaitable[_T]) -> _T:
    """Run an awaitable synchronously, even if a loop is already running.

    - If called from within a running event loop (async context): spawns a
      daemon thread with its own loop to avoid nesting.
    - If called from a worker thread (no running loop): schedules on the
      captured main loop via run_coroutine_threadsafe, falling back to
      asyncio.run() when no main loop is available.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running in this thread (e.g. run_in_executor worker).
        # Prefer scheduling on the main loop so AF's async clients work.
        if _main_loop is not None and _main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(awaitable, _main_loop)
            return future.result()
        return asyncio.run(awaitable)

    # Already in an async context — run in a fresh thread to avoid nesting.
    result: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(awaitable)
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in result:
        raise cast(BaseException, result["error"])
    return cast(_T, result["value"])


_SENTINEL = object()

_T2 = TypeVar("_T2")


def run_async_gen_sync(async_iterable: AsyncIterable[_T2]) -> Iterator[_T2]:
    """Consume an async iterable synchronously via a background thread + queue.

    The async iterable is driven in a daemon thread with its own event loop.
    Items are passed to the calling thread through a queue.
    """
    q: queue.Queue[tuple[str, Any]] = queue.Queue()

    async def _consume() -> None:
        try:
            async for item in async_iterable:
                q.put(("item", item))
        except BaseException as exc:
            q.put(("error", exc))
        else:
            q.put(("done", None))

    thread = threading.Thread(target=lambda: asyncio.run(_consume()), daemon=True)
    thread.start()

    while True:
        kind, value = q.get()
        if kind == "done":
            thread.join()
            return
        if kind == "error":
            thread.join()
            raise cast(BaseException, value)
        yield value
