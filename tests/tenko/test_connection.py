from __future__ import annotations

import asyncio
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
