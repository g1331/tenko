"""面板 SSE：推送内容、会话失效断开、接口鉴权。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from test_webui import _get, _service, _session_client

from tenko.config import WebUIConfig
from tenko.webui import live


class FakeLogs:
    def __init__(self) -> None:
        self.rows = [{"id": 1, "message": "旧"}, {"id": 2, "message": "新"}]

    def live(self, after, query, level):
        rows = [row for row in self.rows if row["id"] > after]
        return {"records": rows, "cursor": self.rows[-1]["id"], "reset": False}


@pytest.mark.asyncio
async def test_stream_pushes_overview_logs_and_changes(monkeypatch) -> None:
    monkeypatch.setattr(live, "TICK", 0.01)
    marks = {"accounts": 1}
    streams = live.LiveStreams(
        overview=lambda: {"online_accounts": 1},
        logs=FakeLogs(),
        marks=lambda: dict(marks),
        session_valid=lambda _request: asyncio.sleep(0, True),
    )
    stream = streams.stream(None, frozenset({"overview", "logs"}), cursor=1)
    chunks = [await anext(stream) for _ in range(3)]
    marks["accounts"] = 2
    chunks.append(await anext(stream))
    streams.closing.set()
    chunks.extend([chunk async for chunk in stream])

    assert chunks[0].startswith("retry:")
    assert (
        chunks[1]
        == 'id: 2\nevent: logs\ndata: {"records":[{"id":2,"message":"新"}],"cursor":2,"reset":false}\n\n'
    )
    assert chunks[2].startswith('event: overview\ndata: {"online_accounts":1}')
    assert 'event: changed\ndata: {"resources":["accounts"]}' in "".join(chunks[3:])
    assert streams.count == 0


@pytest.mark.asyncio
async def test_stream_ends_when_session_is_revoked(monkeypatch) -> None:
    monkeypatch.setattr(live, "HEARTBEAT", 0)
    streams = live.LiveStreams(
        overview=dict,
        logs=FakeLogs(),
        marks=dict,
        session_valid=lambda _request: asyncio.sleep(0, False),
    )
    chunks = [chunk async for chunk in streams.stream(None, frozenset(), cursor=0)]

    assert chunks[-1] == "event: unauthorized\ndata: {}\n\n"


@pytest.mark.asyncio
async def test_events_endpoint_requires_login_and_known_topics(tenko_database) -> None:
    server, service = _service(
        WebUIConfig(enabled=True), plugin_runtime=SimpleNamespace(loaded_plugins={})
    )
    service.live.closing.set()  # 让流立即结束，ASGITransport 会缓冲整个响应
    url = "/webui/api/events?topics=overview"
    assert (await _get(server.app, url)).status_code == 401
    async with _session_client(server.app) as client:
        assert (await client.get("/webui/api/events?topics=secrets")).status_code == 400
        response = await client.get(url)

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
