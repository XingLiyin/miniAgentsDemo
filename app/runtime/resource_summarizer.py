"""资源描述总结器。

长描述（tool / skill / sub-agent）在进入 system prompt 前压缩，缓解上下文膨胀，
尤其是 MCP 工具动辄上千 token 的描述。

两种入口共享同一份后台任务（按缓存键去重）：
- ``prepare(name, raw)``：读路径（Reasoner 构建资源时）。命中缓存直接返回；
  未命中则**阻塞等待**后台总结完成（预热未跑完时即在此等待），超时/失败回落截断。
- ``warm(name, raw)``：预热路径（MCP 连接成功 / skill 加载时）。仅排队，不阻塞。

设计契约：总结的 LLM 调用全部发生在后台线程池，Reasoner 自身不直接调 LLM。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from app.common.utils import estimate_tokens
from app.llm.registry import get_llm_registry
from app.llm.types import InputSchema, LLMMessage, LLMTool
from app.runtime.resource_summary_cache import DescriptionSummaryCache

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You compress tool/skill/agent descriptions for an LLM agent's context window. "
    "Preserve faithfully: what it does, WHEN to use it, and key inputs/constraints. "
    "Drop examples, boilerplate, and repetition. Do not invent capabilities. "
    "Output only the compressed description text, imperative voice, no preamble."
)

_USER_PROMPT = (
    "Compress the following description of '{name}' to at most ~{target} tokens "
    "while keeping enough signal for the agent to decide when and how to use it.\n\n"
    "Description:\n{raw}"
)

# ── 批量压缩（读路径一次调用压多条）─────────────────────────────────────────────
_BATCH_SYSTEM_PROMPT = (
    "You compress tool/skill/agent descriptions for an LLM agent's context window. "
    "For EACH item, preserve faithfully what it does, WHEN to use it, and key inputs/constraints; "
    "drop examples, boilerplate, and repetition; do not invent capabilities. "
    "Return one compressed description per item via the submit_summaries tool, keyed by the item's integer id."
)

_BATCH_USER_PROMPT = (
    "Compress each of the following {count} descriptions to at most ~{target} tokens each, "
    "keeping enough signal for the agent to decide when and how to use each one. "
    "Return results via submit_summaries, one entry per item id.\n\n{items}"
)

_SUMMARIES_TOOL = LLMTool(
    name="submit_summaries",
    description="Return the compressed description for each item, keyed by its integer id.",
    input_schema=InputSchema(
        properties={
            "summaries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "The item id from the prompt"},
                        "summary": {"type": "string", "description": "The compressed description"},
                    },
                    "required": ["id", "summary"],
                },
            }
        },
        require=["summaries"],
    ),
)


class ResourceSummarizer:
    """长描述总结器：后台总结 + 持久化缓存 + 读路径阻塞等待。"""

    def __init__(
        self,
        cache: DescriptionSummaryCache,
        llm_provider: str,
        llm_model: str = "",
        *,
        threshold_tokens: int = 200,
        target_tokens: int = 60,
        wait_timeout_sec: float = 30.0,
        cooldown_sec: float = 60.0,
        max_workers: int = 2,
        batch_token_limit: int = 4000,
        batch_max_items: int = 16,
    ) -> None:
        self._cache = cache
        self._provider = llm_provider
        self._model = llm_model or None
        self._threshold = threshold_tokens
        self._target = target_tokens
        self._wait_timeout = wait_timeout_sec
        self._cooldown_sec = cooldown_sec
        self._batch_token_limit = batch_token_limit
        self._batch_max_items = batch_max_items
        self._lock = threading.Lock()
        self._futures: dict[str, Future[str]] = {}   # key → 进行中的总结任务
        self._cooldown: dict[str, float] = {}         # key → monotonic 截止时间
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="res-summ")

    # ── 公开 API ──────────────────────────────────────────────────────────────

    def prepare(self, name: str, raw: str, *, provider: str = "", model: str = "") -> str:
        """读路径：返回用于 prompt 的描述。

        ``provider`` / ``model`` 由调用方（Reasoner）按 session 解析后传入，
        缺省时回落到构造时的默认 provider。短描述原样返回；命中缓存返回 summary；
        无可用 provider 时原样返回；否则阻塞等待后台总结，
        超时 / 冷却 / 失败时回落到截断版（后台任务仍会继续，下轮即可命中）。
        """
        if not raw or estimate_tokens(raw) <= self._threshold:
            return raw
        prov = provider or self._provider
        if not prov:  # 无 provider 可用：不总结，原样返回（避免无谓截断）
            return raw
        key = self._cache.key(name, raw)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        fut = self._ensure_job(key, name, raw, prov, model or self._model)
        if fut is None:  # 处于失败冷却期，不再阻塞
            return self._truncate(raw)
        try:
            summary = fut.result(timeout=self._wait_timeout)
        except Exception:
            # 超时或任务异常：本轮回落截断，后台任务不取消
            logger.debug("ResourceSummarizer: wait failed for '%s', using truncation", name)
            return self._truncate(raw)
        return summary or self._truncate(raw)

    def warm(self, name: str, raw: str, *, provider: str = "", model: str = "") -> None:
        """预热路径：长描述未命中缓存时排队后台总结，不阻塞。

        无 session 上下文（MCP 连接 / skill 加载）时 ``provider`` 为空，
        回落到默认 provider；默认 provider 也为空则跳过，由读路径首用时再总结。
        """
        if not raw or estimate_tokens(raw) <= self._threshold:
            return
        prov = provider or self._provider
        if not prov:
            return
        key = self._cache.key(name, raw)
        if self._cache.get(key) is not None:
            return
        self._ensure_job(key, name, raw, prov, model or self._model)

    def warm_many(self, items: list[tuple[str, str]]) -> None:
        """批量预热（供 MCP / skill 注册回调使用），使用默认 provider。"""
        for name, raw in items:
            try:
                self.warm(name, raw)
            except Exception:
                logger.debug("ResourceSummarizer: warm failed for '%s'", name, exc_info=True)

    def compress_many(
        self, items: list[tuple[str, str]], *, provider: str = "", model: str = ""
    ) -> dict[tuple[str, str], str]:
        """读路径批量压缩：一次 LLM 调用压一批未命中的长描述，返回 ``(name, raw) → 描述``。

        逐条分流：短描述 / 命中缓存 / 无 provider 直接定稿；其余凑批，按 token 与条数
        切分后每批一次 tool-calling 调用，结果逐条写缓存。同步阻塞（调用方为 Reasoner，
        读路径本就需等待结果）；批内某条缺失或整批失败时回落到截断版。
        """
        prov = provider or self._provider
        mdl = model or self._model
        result: dict[tuple[str, str], str] = {}
        pending: list[tuple[str, str]] = []
        for name, raw in items:
            k = (name, raw)
            if k in result or k in pending:
                continue
            if not raw or estimate_tokens(raw) <= self._threshold:
                result[k] = raw
                continue
            cached = self._cache.get(self._cache.key(name, raw))
            if cached is not None:
                result[k] = cached
                continue
            if not prov:  # 无 provider：原样返回，不截断
                result[k] = raw
                continue
            pending.append(k)

        for batch in self._split_batches(pending):
            try:
                summaries = self._summarize_batch(batch, prov, mdl)
            except Exception:
                logger.warning("ResourceSummarizer: batch summarize failed (%d items)", len(batch), exc_info=True)
                summaries = {}
            for i, (name, raw) in enumerate(batch):
                s = summaries.get(i)
                if s:
                    self._cache.put(self._cache.key(name, raw), s)
                    result[(name, raw)] = s
                else:
                    result[(name, raw)] = self._truncate(raw)
        return result

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ── 内部 ──────────────────────────────────────────────────────────────────

    def _ensure_job(self, key: str, name: str, raw: str, provider: str, model: str) -> "Future[str] | None":
        """返回该 key 对应的总结 Future；冷却期内返回 None。

        按 cache key 去重：同一描述只跑一个后台任务，provider 取首个发起者的取值。
        """
        with self._lock:
            fut = self._futures.get(key)
            if fut is not None:
                return fut
            if time.monotonic() < self._cooldown.get(key, 0.0):
                return None
            fut = self._pool.submit(self._run, key, name, raw, provider, model)
            self._futures[key] = fut
            return fut

    def _run(self, key: str, name: str, raw: str, provider: str, model: str) -> str:
        try:
            client = get_llm_registry().get_client(provider, model or None)
            resp = client.send_message(
                messages=[LLMMessage(role="user", content=_USER_PROMPT.format(
                    name=name, target=self._target, raw=raw,
                ))],
                system_prompt=_SYSTEM_PROMPT,
                max_tokens=self._target * 3,
            )
            summary = (resp.text or "").strip()
            if summary:
                self._cache.put(key, summary)
                return summary
            logger.warning("ResourceSummarizer: empty summary for '%s'", name)
        except Exception:
            logger.warning("ResourceSummarizer: summarize failed for '%s'", name, exc_info=True)
            with self._lock:
                self._cooldown[key] = time.monotonic() + self._cooldown_sec
        finally:
            with self._lock:
                self._futures.pop(key, None)
        return ""

    # ── 批量压缩内部实现 ───────────────────────────────────────────────────────

    def _split_batches(self, pending: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
        """按累计 token 与条数上限切批，控制单次调用的输入/输出规模。"""
        batches: list[list[tuple[str, str]]] = []
        cur: list[tuple[str, str]] = []
        cur_tok = 0
        for name, raw in pending:
            t = estimate_tokens(raw)
            if cur and (cur_tok + t > self._batch_token_limit or len(cur) >= self._batch_max_items):
                batches.append(cur)
                cur, cur_tok = [], 0
            cur.append((name, raw))
            cur_tok += t
        if cur:
            batches.append(cur)
        return batches

    def _summarize_batch(self, batch: list[tuple[str, str]], provider: str, model: str) -> dict[int, str]:
        """一次 tool-calling 调用压一批，返回 ``索引 → summary``（缺失项由调用方回落）。"""
        client = get_llm_registry().get_client(provider, model or None)
        items_text = "\n\n".join(f"[{i}] {name}\n{raw}" for i, (name, raw) in enumerate(batch))
        user = _BATCH_USER_PROMPT.format(count=len(batch), target=self._target, items=items_text)
        max_out = min(self._target * 3 * len(batch) + 256, getattr(client, "max_output_tokens", 8192) or 8192)
        resp = client.send_message(
            messages=[LLMMessage(role="user", content=user)],
            system_prompt=_BATCH_SYSTEM_PROMPT,
            tools=[_SUMMARIES_TOOL],
            max_tokens=max_out,
        )
        parsed = client.parse_response(resp)
        raw_list = None
        if parsed.tool_calls:
            raw_list = (parsed.tool_calls[0].input or {}).get("summaries")
        if raw_list is None:  # 模型未走 tool：回落解析文本中的 JSON
            raw_list = self._extract_summaries_text(parsed.text or resp.text or "")
        out: dict[int, str] = {}
        for entry in (raw_list or []):
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            s = str(entry.get("summary", "")).strip()
            if s:
                out[idx] = s
        return out

    @staticmethod
    def _extract_summaries_text(text: str) -> "list | None":
        """从自由文本中尽力抽出 summaries 列表（tool-calling 失败时的兜底）。"""
        if not text:
            return None
        try:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("summaries"), list):
                return data["summaries"]
            if isinstance(data, list):
                return data
        except Exception:
            pass
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return None

    def _truncate(self, raw: str) -> str:
        """按 token 阈值截断（粗略按 4 字符/token 折算），附标记。"""
        limit_chars = max(self._threshold * 4, 200)
        if len(raw) <= limit_chars:
            return raw
        return raw[:limit_chars].rstrip() + " … [auto-summarizing]"
