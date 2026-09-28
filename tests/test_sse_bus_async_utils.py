"""Tests for app/common/sse_bus.py and app/common/async_utils.py.

SseBus batches streaming deltas inside a 0.05s window and drops/evicts on a full
queue, so the tests drive it from a real running loop and advance it with sleeps.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from app.common import async_utils
from app.common.async_utils import (
    get_main_loop, run_async_gen_sync, run_awaitable_sync, set_main_loop,
)
from app.common.sse_bus import (
    _DROPPABLE_STREAM_TYPES, _STREAM_BATCH_WINDOW_SEC, SseBus, get_sse_bus,
)


@pytest.fixture(autouse=True)
def _no_event_store():
    """Keep push() from touching the on-disk event log."""
    with patch("app.storage.file.event_store.get_event_store", return_value=MagicMock()):
        yield


@pytest.fixture
def restore_main_loop():
    saved = async_utils._main_loop
    yield
    async_utils._main_loop = saved


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return out


# ── subscriptions ─────────────────────────────────────────────────────────────

class TestSubscriptions:
    def test_create_registers_a_queue(self):
        async def main():
            bus = SseBus()
            q = bus.create_subscription("s1")
            assert bus._queues["s1"] == [q]
            assert q.maxsize == 500

        asyncio.run(main())

    def test_several_subscribers_per_session(self):
        async def main():
            bus = SseBus()
            a, b = bus.create_subscription("s1"), bus.create_subscription("s1")
            assert bus._queues["s1"] == [a, b]

        asyncio.run(main())

    def test_remove_drops_the_queue_and_the_session_entry(self):
        async def main():
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus.remove_subscription("s1", q)
            assert "s1" not in bus._queues

        asyncio.run(main())

    def test_remove_keeps_the_session_while_others_remain(self):
        async def main():
            bus = SseBus()
            a, b = bus.create_subscription("s1"), bus.create_subscription("s1")
            bus.remove_subscription("s1", a)
            assert bus._queues["s1"] == [b]

        asyncio.run(main())

    def test_remove_unknown_queue_is_safe(self):
        async def main():
            bus = SseBus()
            bus.remove_subscription("s1", asyncio.Queue())
            bus.create_subscription("s1")
            bus.remove_subscription("s1", asyncio.Queue())

        asyncio.run(main())

    def test_remove_cancels_a_pending_flush(self):
        async def main():
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus._buffer_stream_event(q, "s1", {"type": "text_delta", "delta": "x"})
            assert q in bus._pending_flush_handles
            bus.remove_subscription("s1", q)
            assert q not in bus._pending_flush_handles
            assert q not in bus._pending_stream_events

        asyncio.run(main())

    def test_get_sse_bus_is_a_singleton(self):
        assert get_sse_bus() is get_sse_bus()


# ── push ──────────────────────────────────────────────────────────────────────

class TestPush:
    def test_delivers_a_normal_event(self, restore_main_loop):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus.push("s1", {"type": "message", "content": "hi"})
            await asyncio.sleep(0)
            assert _drain(q) == [{"type": "message", "content": "hi"}]

        asyncio.run(main())

    def test_broadcasts_to_every_subscriber(self, restore_main_loop):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            bus = SseBus()
            a, b = bus.create_subscription("s1"), bus.create_subscription("s1")
            bus.push("s1", {"type": "message"})
            await asyncio.sleep(0)
            assert len(_drain(a)) == 1 and len(_drain(b)) == 1

        asyncio.run(main())

    def test_no_subscribers_is_a_noop(self, restore_main_loop):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            SseBus().push("s1", {"type": "message"})

        asyncio.run(main())

    def test_other_sessions_are_not_touched(self, restore_main_loop):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus.push("s2", {"type": "message"})
            await asyncio.sleep(0)
            assert _drain(q) == []

        asyncio.run(main())

    def test_without_a_main_loop_nothing_is_delivered(self, restore_main_loop):
        async def main():
            async_utils._main_loop = None
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus.push("s1", {"type": "message"})
            await asyncio.sleep(0)
            assert _drain(q) == []

        asyncio.run(main())

    def test_event_store_failure_does_not_block_delivery(self, restore_main_loop):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            bus = SseBus()
            q = bus.create_subscription("s1")
            store = MagicMock()
            store.append.side_effect = RuntimeError("disk full")
            with patch("app.storage.file.event_store.get_event_store", return_value=store):
                bus.push("s1", {"type": "message"})
            await asyncio.sleep(0)
            assert len(_drain(q)) == 1

        asyncio.run(main())

    def test_call_soon_failure_is_swallowed(self, restore_main_loop):
        async def main():
            loop = asyncio.get_running_loop()
            set_main_loop(loop)
            bus = SseBus()
            bus.create_subscription("s1")
            with patch.object(loop, "call_soon_threadsafe",
                              side_effect=RuntimeError("loop closed")):
                bus.push("s1", {"type": "message"})

        asyncio.run(main())

    def test_push_works_from_a_worker_thread(self, restore_main_loop):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            bus = SseBus()
            q = bus.create_subscription("s1")
            t = threading.Thread(target=lambda: bus.push("s1", {"type": "message"}))
            t.start()
            t.join()
            await asyncio.sleep(0.01)
            assert len(_drain(q)) == 1

        asyncio.run(main())


# ── stream-delta batching ─────────────────────────────────────────────────────

class TestStreamBatching:
    @pytest.mark.parametrize("etype", sorted(_DROPPABLE_STREAM_TYPES))
    def test_deltas_are_buffered_then_merged(self, restore_main_loop, etype):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus.push("s1", {"type": etype, "delta": "he"})
            bus.push("s1", {"type": etype, "delta": "llo"})
            await asyncio.sleep(0)
            assert _drain(q) == []                     # still buffered
            await asyncio.sleep(_STREAM_BATCH_WINDOW_SEC * 3)
            assert _drain(q) == [{"type": etype, "delta": "hello"}]

        asyncio.run(main())

    def test_different_metadata_is_not_merged(self, restore_main_loop):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus.push("s1", {"type": "text_delta", "delta": "a", "round": 0})
            bus.push("s1", {"type": "text_delta", "delta": "b", "round": 1})
            await asyncio.sleep(_STREAM_BATCH_WINDOW_SEC * 3)
            assert [e["delta"] for e in _drain(q)] == ["a", "b"]

        asyncio.run(main())

    def test_different_types_are_not_merged(self, restore_main_loop):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus.push("s1", {"type": "text_delta", "delta": "a"})
            bus.push("s1", {"type": "reasoning_delta", "delta": "b"})
            await asyncio.sleep(_STREAM_BATCH_WINDOW_SEC * 3)
            assert len(_drain(q)) == 2

        asyncio.run(main())

    def test_a_normal_event_flushes_the_buffer_first(self, restore_main_loop):
        async def main():
            set_main_loop(asyncio.get_running_loop())
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus.push("s1", {"type": "text_delta", "delta": "partial"})
            bus.push("s1", {"type": "text_done", "text": "partial"})
            await asyncio.sleep(0)
            types = [e["type"] for e in _drain(q)]
            assert types == ["text_delta", "text_done"]

        asyncio.run(main())

    def test_can_merge_requires_a_delta_on_both(self):
        bus = SseBus()
        assert not bus._can_merge_stream_event({"type": "t"}, {"type": "t", "delta": "x"})
        assert not bus._can_merge_stream_event({"type": "t", "delta": "x"}, {"type": "t"})

    def test_can_merge_requires_the_same_type(self):
        bus = SseBus()
        assert not bus._can_merge_stream_event(
            {"type": "a", "delta": "x"}, {"type": "b", "delta": "y"})

    def test_can_merge_matching_events(self):
        bus = SseBus()
        assert bus._can_merge_stream_event(
            {"type": "a", "delta": "x", "round": 1}, {"type": "a", "delta": "y", "round": 1})

    def test_flush_with_nothing_buffered_is_safe(self):
        async def main():
            bus = SseBus()
            q = bus.create_subscription("s1")
            bus._flush_pending_stream_events(q, "s1")

        asyncio.run(main())

    def test_buffered_event_is_copied_not_aliased(self):
        async def main():
            bus = SseBus()
            q = bus.create_subscription("s1")
            event = {"type": "text_delta", "delta": "a"}
            bus._buffer_stream_event(q, "s1", event)
            event["delta"] = "mutated"
            assert bus._pending_stream_events[q][0]["delta"] == "a"

        asyncio.run(main())


# ── queue-full behaviour ──────────────────────────────────────────────────────

class TestQueueFull:
    def _full_queue(self, bus, maxsize=2):
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        for i in range(maxsize):
            q.put_nowait({"type": "message", "n": i})
        return q

    def test_droppable_stream_event_is_dropped(self):
        async def main():
            bus = SseBus()
            q = self._full_queue(bus)
            bus._put_event(q, "s1", {"type": "text_delta", "delta": "x"})
            assert [e["n"] for e in _drain(q)] == [0, 1]

        asyncio.run(main())

    def test_important_event_evicts_the_oldest(self):
        async def main():
            bus = SseBus()
            q = self._full_queue(bus)
            bus._put_event(q, "s1", {"type": "message", "n": "new"})
            assert [e["n"] for e in _drain(q)] == [1, "new"]

        asyncio.run(main())

    def test_room_available_just_enqueues(self):
        async def main():
            bus = SseBus()
            q: asyncio.Queue = asyncio.Queue(maxsize=5)
            bus._put_event(q, "s1", {"type": "message"})
            assert len(_drain(q)) == 1

        asyncio.run(main())

    def test_empty_after_queue_full_is_handled(self):
        async def main():
            bus = SseBus()
            q = MagicMock()
            q.put_nowait.side_effect = asyncio.QueueFull
            q.get_nowait.side_effect = asyncio.QueueEmpty
            bus._put_event(q, "s1", {"type": "message"})

        asyncio.run(main())

    def test_still_full_after_eviction_is_logged(self):
        async def main():
            bus = SseBus()
            q = MagicMock()
            q.put_nowait.side_effect = asyncio.QueueFull
            q.get_nowait.return_value = {"type": "message"}
            bus._put_event(q, "s1", {"type": "message", "n": "new"})

        asyncio.run(main())

    def test_evicted_none_event_is_tolerated(self):
        async def main():
            bus = SseBus()
            q = MagicMock()
            q.put_nowait.side_effect = [asyncio.QueueFull, None]
            q.get_nowait.return_value = None
            bus._put_event(q, "s1", {"type": "message"})

        asyncio.run(main())

    def test_event_without_a_type(self):
        async def main():
            bus = SseBus()
            q: asyncio.Queue = asyncio.Queue(maxsize=5)
            bus._enqueue_event(q, "s1", {"content": "no type"})
            assert len(_drain(q)) == 1

        asyncio.run(main())


# ── async_utils ───────────────────────────────────────────────────────────────

class TestMainLoopAccessors:
    def test_set_and_get(self, restore_main_loop):
        loop = asyncio.new_event_loop()
        try:
            set_main_loop(loop)
            assert get_main_loop() is loop
        finally:
            loop.close()

    def test_unset_is_none(self, restore_main_loop):
        async_utils._main_loop = None
        assert get_main_loop() is None


class TestRunAwaitableSync:
    def test_from_a_plain_thread_without_a_main_loop(self, restore_main_loop):
        async_utils._main_loop = None

        async def coro():
            return 42

        assert run_awaitable_sync(coro()) == 42

    def test_uses_the_main_loop_when_one_is_running(self, restore_main_loop):
        results = {}
        ready = threading.Event()
        loop = asyncio.new_event_loop()

        def run_loop():
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()

        t = threading.Thread(target=run_loop, daemon=True)
        t.start()
        ready.wait(5)
        set_main_loop(loop)
        try:
            async def coro():
                return "from main loop"

            results["value"] = run_awaitable_sync(coro())
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=5)
            loop.close()
        assert results["value"] == "from main loop"

    def test_from_inside_a_running_loop_uses_a_thread(self, restore_main_loop):
        async_utils._main_loop = None

        async def inner():
            return "nested ok"

        async def main():
            return run_awaitable_sync(inner())

        assert asyncio.run(main()) == "nested ok"

    def test_exception_from_a_nested_run_propagates(self, restore_main_loop):
        async_utils._main_loop = None

        async def boom():
            raise ValueError("inner failure")

        async def main():
            return run_awaitable_sync(boom())

        with pytest.raises(ValueError, match="inner failure"):
            asyncio.run(main())

    def test_exception_from_a_plain_call_propagates(self, restore_main_loop):
        async_utils._main_loop = None

        async def boom():
            raise ValueError("plain failure")

        with pytest.raises(ValueError, match="plain failure"):
            run_awaitable_sync(boom())


class TestRunAsyncGenSync:
    def test_yields_every_item(self):
        async def gen():
            for i in range(3):
                yield i

        assert list(run_async_gen_sync(gen())) == [0, 1, 2]

    def test_empty_generator(self):
        async def gen():
            if False:
                yield 1

        assert list(run_async_gen_sync(gen())) == []

    def test_error_is_reraised(self):
        async def gen():
            yield 1
            raise RuntimeError("gen exploded")

        it = run_async_gen_sync(gen())
        assert next(it) == 1
        with pytest.raises(RuntimeError, match="gen exploded"):
            next(it)

    def test_error_before_the_first_item(self):
        async def gen():
            raise RuntimeError("immediate")
            yield 1

        with pytest.raises(RuntimeError, match="immediate"):
            list(run_async_gen_sync(gen()))

    def test_items_are_awaited_in_order(self):
        async def gen():
            for word in ("a", "b", "c"):
                await asyncio.sleep(0)
                yield word

        assert list(run_async_gen_sync(gen())) == ["a", "b", "c"]
