from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from launart.status import ManagerStatus

from tenko.config import OneBotConfig
from tenko.connection import OneBotConnection


def test_accounts_call_the_local_api_with_a_per_process_token() -> None:
    first = OneBotConnection(OneBotConfig(listen_host="::"))
    second = OneBotConnection(OneBotConfig(listen_host="::"))

    api = first.server.network.config
    assert str(api.api_base) == "http://[::1]:8080/satori/v1"
    assert api.token == first.server.token
    assert first.server.token != second.server.token


@pytest.mark.asyncio
async def test_events_wait_until_blocking_and_then_go_through_app_post() -> None:
    connection = OneBotConnection(OneBotConfig())
    client = SimpleNamespace(post=AsyncMock())
    manager = SimpleNamespace(status=ManagerStatus())
    manager.status.stage = "preparing"
    event = SimpleNamespace(type="login-added")

    await connection.server.post(event)  # 尚未接入 Entari：丢弃
    connection.attach(client, manager)
    await connection.server.post(event)
    await asyncio.sleep(0)
    client.post.assert_not_awaited()

    manager.status.stage = "blocking"
    await asyncio.sleep(0.01)
    client.post.assert_awaited_once_with(event, connection.server.network)


def test_only_local_satori_api_access_lines_are_hidden() -> None:
    from tenko.connection import _hide_local_api_access

    def access(client: str, path: str) -> logging.LogRecord:
        return logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            "",
            0,
            '%s - "%s %s HTTP/%s" %d',
            (client, "POST", path, "1.1", 200),
            None,
        )

    assert not _hide_local_api_access(
        access("127.0.0.1:4339", "/satori/v1/message.create")
    )
    assert not _hide_local_api_access(access("::1:4339", "/satori/v1/message.create"))
    # 外部访问内部接口、以及 WebUI 等其他路径照常记录。
    assert _hide_local_api_access(access("8.8.8.8:4339", "/satori/v1/message.create"))
    assert _hide_local_api_access(access("127.0.0.1:4339", "/webui/api/overview"))


@pytest.mark.asyncio
async def test_back_to_back_events_each_reach_the_server_once() -> None:
    from tenko.connection import _ManagedConnection

    frames = [
        {"self_id": 10001, "post_type": "notice", "notice_type": "first"},
        {"self_id": 10001, "post_type": "notice", "notice_type": "second"},
    ]
    queue = [{"type": "websocket.receive", "text": json.dumps(f)} for f in frames]
    queue.append({"type": "websocket.disconnect"})

    async def receive():
        return queue.pop(0)  # 不让出事件循环，模拟同一批到达的帧

    posted = []

    async def post(event):
        posted.append(event._data["notice_type"])

    adapter = SimpleNamespace(
        logins={"10001": SimpleNamespace()}, server=SimpleNamespace(post=post)
    )
    connection = _ManagedConnection(adapter, SimpleNamespace(receive=receive), "10001")

    await connection.message_handle()
    await asyncio.sleep(0.01)

    assert sorted(posted) == ["first", "second"]
