"""面板实时事件：一条 SSE 连接推送总览指标、日志和“某类数据变了”的通知。

数据仍以 REST 接口为准；``changed`` 只告诉前端该重新拉哪一类。服务端每秒检查一次
内存状态，没有发布订阅和跨线程队列，客户端读得慢时生成器自然停在 ``yield`` 上。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from loguru import logger

TOPICS = frozenset({"overview", "logs"})
TICK = 1.0
OVERVIEW_INTERVAL = 5.0
HEARTBEAT = 15.0
MAX_STREAMS = 8


def sse(event: str, data: object, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"{head}event: {event}\ndata: {body}\n\n"


class LiveStreams:
    def __init__(
        self,
        *,
        overview: Callable[[], Mapping[str, Any]],
        logs: Any,
        marks: Callable[[], dict[str, int]],
        session_valid: Callable[[Any], Any],
        stopping: Callable[[], bool] = lambda: False,
    ) -> None:
        self.overview = overview
        self.logs = logs
        self.marks = marks
        self.session_valid = session_valid
        self.stopping = stopping
        self.count = 0
        self.closing = asyncio.Event()

    async def stream(
        self, request: Any, topics: frozenset[str], cursor: int
    ) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        last_auth = last_beat = loop.time()
        last_overview = last_auth - OVERVIEW_INTERVAL  # 连上就先推一次
        marks = self.marks()
        self.count += 1
        try:
            yield "retry: 3000\n\n"
            while not self.closing.is_set() and not self.stopping():
                now = loop.time()
                if now - last_auth >= HEARTBEAT:
                    last_auth = now
                    # 注销、改密或被踢下线后，已建立的长连接也要断开。
                    if not await self.session_valid(request):
                        yield sse("unauthorized", {})
                        return
                if "logs" in topics:
                    data = self.logs.live(cursor, "", "")
                    if data["records"] or data["reset"]:
                        cursor = data["cursor"]
                        yield sse("logs", data, cursor)
                if "overview" in topics and now - last_overview >= OVERVIEW_INTERVAL:
                    last_overview = now
                    try:
                        payload = self.overview()
                    except Exception:
                        logger.exception("WebUI live overview failed")
                    else:
                        yield sse("overview", payload)
                current = self.marks()
                if changed := [
                    key for key in current if current[key] != marks.get(key)
                ]:
                    marks = current
                    yield sse("changed", {"resources": changed})
                if now - last_beat >= HEARTBEAT:
                    last_beat = now
                    yield ": ping\n\n"
                try:
                    await asyncio.wait_for(self.closing.wait(), TICK)
                except TimeoutError:
                    pass
        finally:
            self.count -= 1


__all__ = ["HEARTBEAT", "MAX_STREAMS", "TOPICS", "LiveStreams", "sse"]
