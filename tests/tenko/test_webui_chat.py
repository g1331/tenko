"""面板聊天接口：会话列表、消息分页和以 bot 身份发送。"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from satori.element import Quote, Text
from test_chat_log import group_event
from test_webui import _service, _session_client

from tenko.config import WebUIConfig
from tenko.db.repositories import ChatMessageRepository
from tenko.host.accounts import AccountRegistry
from tenko.host.actions import ActionService
from tenko.host.chat_log import chat_log


class Protocol:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_message(self, *args):
        self.sent.append(args)
        return [{"id": "777"}]


@pytest.mark.asyncio
async def test_chat_lists_messages_and_sends_as_the_bot(tenko_database):
    protocol = Protocol()
    bot = SimpleNamespace(self_id="10001", platform="onebot", protocol=protocol)
    accounts = AccountRegistry()
    accounts.register(bot, groups=["40001", "40002"])
    accounts.set_muted(bot, "40002", True)
    chat_log.configure(
        ChatMessageRepository(), enabled=True, retention_days=7, max_messages=100
    )
    chat_log.record_event(bot, group_event("10001", "501", "早"))
    # bot 发出的回复只带引用 ID，列表里要补上被引用的人和内容。
    chat_log.record_send(
        SimpleNamespace(
            account=bot,
            channel="40001",
            message=[Quote("501"), Text("早上好")],
            result=[SimpleNamespace(id="502")],
            session=None,
        )
    )
    await chat_log.flush()

    server, service = _service(WebUIConfig(enabled=True), accounts=accounts)
    service.chat.actions = ActionService(accounts)
    query = "platform=onebot&account=10001"
    image = "data:image/png;base64," + base64.b64encode(b"x" * 10).decode()

    async with _session_client(server.app) as client:
        listed = (
            await client.get(f"/webui/api/manage/chat/conversations?{query}")
        ).json()
        messages = (
            await client.get(f"/webui/api/manage/chat/messages?{query}&channel=40001")
        ).json()
        sent = await client.post(
            "/webui/api/manage/chat/send",
            json={
                "platform": "onebot",
                "account": "10001",
                "channel": "40001",
                "content": [
                    {"type": "quote", "id": "501"},
                    {"type": "at", "id": "20001"},
                    {"type": "text", "text": " <你好>"},
                    {"type": "image", "src": image},
                ],
            },
        )
        muted = await client.post(
            "/webui/api/manage/chat/send",
            json={
                "platform": "onebot",
                "account": "10001",
                "channel": "40002",
                "content": [{"type": "text", "text": "在吗"}],
            },
        )
        script = await client.post(
            "/webui/api/manage/chat/send",
            json={
                "platform": "onebot",
                "account": "10001",
                "channel": "40001",
                "content": [{"type": "image", "src": "javascript:alert(1)"}],
            },
        )

    conversations = {row["channel_id"]: row for row in listed["data"]["conversations"]}
    assert conversations["40001"]["name"] == "天界茶话会"
    assert conversations["40001"]["last"]["preview"] == "早上好"
    # 绑定了但还没有消息的群也出现在列表里，并标出禁言。
    assert conversations["40002"]["last"] is None and conversations["40002"]["muted"]
    message, answer = messages["data"]["messages"]
    assert message["content"] == [{"type": "text", "text": "早"}]
    assert message["user_name"] == "群友甲" and not message["outgoing"]
    assert answer["outgoing"] and answer["content"][0] == {
        "type": "quote",
        "id": "501",
        "user_id": "20001",
        "name": "群友甲",
        "text": "早",
    }

    assert sent.status_code == 200, sent.text
    [(channel, content)] = protocol.sent
    assert channel == "40001"
    # 必须传元素：字符串在 str_as_message 关闭时会被当作纯文本发出去。
    assert isinstance(content, list)
    xml = "".join(map(str, content))
    assert xml.startswith('<quote id="501"/><at id="20001"/> &lt;你好&gt;<img ')
    assert muted.status_code == 409 and "禁言" in muted.json()["error"]["message"]
    assert script.status_code == 400
