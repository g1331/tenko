"""命令沙盒：面板消息变成标准事件，bot 的回复回到沙盒，沙盒账号不进路由。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from satori.element import Image, Quote, Text

from tenko.config import DebugConfig
from tenko.context import MessageContext
from tenko.events import MessageEventHandler
from tenko.host.accounts import AccountRegistry
from tenko.host.metrics import MessageMetrics
from tenko.host.perm import Permission, PermissionChecker, PermissionRegistry
from tenko.host.sandbox import SANDBOX_BOT_ID, SANDBOX_GROUP_ID, SandboxAdapter


class Server:
    def __init__(self) -> None:
        self.events = []

    async def post(self, event) -> None:
        self.events.append(event)


def adapter() -> tuple[SandboxAdapter, Server]:
    sandbox = SandboxAdapter()
    server = Server()
    sandbox.ensure_server(server)
    sandbox.ready = True
    return sandbox, server


async def reply(sandbox: SandboxAdapter, channel: str, content: str) -> str:
    request = SimpleNamespace(params={"channel_id": channel, "content": content})
    [sent] = await sandbox.routes["message.create"](request)
    return sent.id


@pytest.mark.asyncio
async def test_sandbox_message_becomes_an_event_with_the_chosen_identity():
    sandbox, server = adapter()
    checker = PermissionChecker(PermissionRegistry())

    await sandbox.send("group", "admin", [Text("/状态")])
    await sandbox.send("private", "master", [Text("/面板账号")])

    group, private = (MessageContext.from_event(event) for event in server.events)
    assert (group.chat_type, group.channel_id, group.member_role) == (
        "group",
        SANDBOX_GROUP_ID,
        "admin",
    )
    assert await checker.get_user_perm(group) == Permission.GroupAdmin
    assert private.chat_type == "private"
    assert await checker.get_user_perm(private) == Permission.Master


@pytest.mark.asyncio
async def test_bot_replies_are_kept_and_quotes_carry_the_original():
    sandbox, server = adapter()
    await sandbox.send("group", "member", [Text("早")])
    answer = await reply(
        sandbox,
        SANDBOX_GROUP_ID,
        '<quote id="1"/>早上好<img src="data:image/png;base64,AAAA"/>',
    )

    await sandbox.send("group", "member", [Quote(answer), Text("谢谢")])

    messages = sandbox.messages(SANDBOX_GROUP_ID)
    assert [item["outgoing"] for item in messages] == [False, True, False]
    # 沙盒只在内存里，bot 渲染的图片保留下来给面板看。
    # bot 的引用只带 ID，记录时补上被引用的人和内容。
    assert messages[1]["content"][0]["name"] == "测试成员"
    assert messages[1]["content"][0]["text"] == "早"
    assert messages[1]["content"][2] == {
        "type": "image",
        "src": "data:image/png;base64,AAAA",
    }
    assert messages[2]["content"][0]["text"] == "早上好[图片]"
    quote = server.events[-1].message.message[0]
    assert quote.children[0].id == SANDBOX_BOT_ID
    assert isinstance(quote.children[-1], Image)


@pytest.mark.asyncio
async def test_sandbox_account_skips_routing_and_metrics():
    sandbox, server = adapter()
    registry = AccountRegistry()
    metrics = MessageMetrics()
    handler = MessageEventHandler(
        account_registry=registry,
        debug_config=DebugConfig(enabled=True, masters=["20001"]),
        metrics=metrics,
    )
    await sandbox.send("group", "member", [Text("/帮助")])
    account = SimpleNamespace(platform="sandbox", self_id=SANDBOX_BOT_ID)

    # 调试白名单和账号选路都不拦沙盒；也不会把沙盒账号登记进路由。
    assert handler.should_skip(account, server.events[0]) is False
    await handler.handle(account, server.events[0])
    assert registry.get(account) is None
    assert metrics.received_count == 0
