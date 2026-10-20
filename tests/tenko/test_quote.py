"""群聊回复默认引用触发消息：经真实 Entari 分发和 ``Session.send`` 验证。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace

import arclet.letoderea as le
import pytest
from arclet.entari import SendRequest
from arclet.entari.plugin import load_plugin
from arclet.entari.session import EntariProtocol
from arclet.letoderea.utils import _EventSystem

from command_dispatch_support import (
    FakeAccount,
    dispatch,
    entari_command_dispatch,
    message_event,
)
from external_plugin_support import drop_module, write_plugin
from tenko.host.quote import quote_group_reply

PLUGIN = """
from arclet.alconna import Alconna
from arclet.entari import MessageChain, Session, command, plugin
from satori import Quote

plugin.metadata("引用", author=["t"], version="0.0.1")

@command.on(Alconna("引用测试"))
async def handler(session: Session):
    await session.send("回复")
    await session.send(MessageChain([Quote("9"), "已带引用"]))
    # 不带 session 的主动发送（公告、通知等走的路径）。
    await session.account.protocol.send_message(session.event.channel.id, "主动")
"""


class RealSendProtocol(EntariProtocol):
    """走 Entari 真实的 ``message_create``（含 ``SendRequest``），只替换网络层。"""

    def __init__(self, account) -> None:
        self.sent: list[str] = []
        super().__init__(account)

    async def call_api(self, action, params=None, *args, **kwargs):
        self.sent.append(params["content"])
        return [{"id": f"sent-{len(self.sent)}", "content": params["content"]}]


@pytest.mark.parametrize(
    ("group", "reply"),
    [("40001", '<quote id="{id}"/>回复'), (None, "回复")],
    ids=["group_quoted", "private_plain"],
)
@pytest.mark.asyncio
async def test_group_reply_quotes_trigger_message(
    entari_plugin_host, monkeypatch, tmp_path, group, reply
) -> None:
    monkeypatch.setattr(_EventSystem, "loop", asyncio.get_running_loop())
    write_plugin(tmp_path, monkeypatch, "tenko_t_quote", PLUGIN)
    account = FakeAccount()
    event = message_event("/引用测试", group=group)
    account.config = SimpleNamespace(timeout=None)
    account.channel_get = AsyncMock(return_value=event.channel)
    account.protocol = RealSendProtocol(account)
    with entari_command_dispatch():
        subscriber = le.on(SendRequest, quote_group_reply)
        try:
            assert load_plugin("tenko_t_quote")
            await dispatch(entari_plugin_host, account, event)
        finally:
            subscriber.dispose()
            drop_module("tenko_t_quote")
            await account.protocol.session.close()

    assert account.sent == [
        reply.format(id=event.message.id),
        '<quote id="9"/>已带引用',  # 已有引用不重复
        "主动",  # 没有触发会话的发送不引用
    ]
