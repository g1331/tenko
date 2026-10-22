"""面板聊天记录：每个账号各存一份、重复的消息只存一次、发出的消息和清理。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from satori import Channel, ChannelType, EventType, Guild, Login, MessageObject, User
from satori.element import Custom, Image, Text
from satori.model import Event

from tenko.db.repositories import ChatMessageRepository, ChatRecord
from tenko.host.chat_log import ChatLog, segments


def group_event(account_id: str, message_id: str, text: str) -> Event:
    return Event(
        type=EventType.MESSAGE_CREATED,
        timestamp=datetime.now(),
        login=Login(platform="onebot", user=User(account_id)),
        channel=Channel("40001", ChannelType.TEXT),
        guild=Guild("40001", "天界茶话会"),
        user=User("20001", nick="群友甲"),
        message=MessageObject(message_id, text),
    )


def account(self_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        self_id=self_id,
        platform="onebot",
        self_info=SimpleNamespace(user=User(self_id, name=f"bot{self_id}")),
    )


def make_log() -> tuple[ChatLog, ChatMessageRepository]:
    repository = ChatMessageRepository()
    log = ChatLog()
    log.configure(repository, enabled=True, retention_days=7, max_messages=100)
    return log, repository


@pytest.mark.asyncio
async def test_messages_are_kept_per_account_and_stored_once(tenko_database):
    log, repository = make_log()
    log.record_event(account("10001"), group_event("10001", "501", "早"))
    # 协议端回显同一条消息（同账号、同 message_id）不会重复入库。
    log.record_event(account("10001"), group_event("10001", "501", "早"))
    log.record_event(account("10002"), group_event("10002", "9001", "早"))
    log.record_send(
        SimpleNamespace(
            account=account("10001"),
            channel="40001",
            message=[Text("早上好")],
            result=[MessageObject("502", "早上好")],
            session=None,
        )
    )
    await log.flush()

    rows = await repository.messages("onebot", "10001", "40001")
    assert [(row.message_id, row.outgoing) for row in rows] == [
        ("501", False),
        ("502", True),
    ]
    assert rows[0].user_name == "群友甲"
    assert rows[0].channel_name == "天界茶话会"
    assert json.loads(rows[1].content) == [{"type": "text", "text": "早上好"}]
    assert [
        row.message_id for row in await repository.messages("onebot", "10002", "40001")
    ] == ["9001"]
    # 增量读取只返回游标之后的消息。
    assert [
        row.message_id
        for row in await repository.messages(
            "onebot", "10001", "40001", after=rows[0].id
        )
    ] == ["502"]
    conversations = await repository.conversations("onebot", "10001")
    assert [row.message_id for row in conversations] == ["502"]


@pytest.mark.asyncio
async def test_prune_removes_expired_and_excess_messages(tenko_database):
    repository = ChatMessageRepository()
    now = time.time()

    def record(index: int, created_at: float) -> ChatRecord:
        return ChatRecord(
            platform="onebot",
            account_id="10001",
            chat_type="group",
            channel_id="40001",
            channel_name="",
            message_id=str(index),
            outgoing=False,
            user_id="20001",
            user_name="",
            content="[]",
            created_at=created_at,
        )

    await repository.add(
        [record(0, now - 10 * 86400), *(record(i, now) for i in range(1, 6))]
    )
    assert await repository.prune(now - 7 * 86400, keep=3) == 3
    rows = await repository.messages("onebot", "10001", "40001")
    assert [row.message_id for row in rows] == ["3", "4", "5"]


def test_segments_drop_inline_images_and_unknown_elements():
    assert segments(
        [
            Image(src="data:image/png;base64,AAAA"),
            Custom("onebot:json", {"data": "{}"}),
            Custom("onebot:unknown"),
        ]
    ) == [
        {"type": "image", "src": ""},
        {"type": "other", "name": "卡片消息"},
        {"type": "other", "name": "不支持的消息"},
    ]


@pytest.mark.asyncio
async def test_sent_inline_images_are_resolved_through_the_protocol(tenko_database):
    log, repository = make_log()
    bot = account("10001")

    async def message_get(channel_id, message_id):
        assert (channel_id, message_id) == ("40001", "900")
        return MessageObject.from_elements(
            message_id, [Image(src="https://img.example/status.png")]
        )

    bot.protocol = SimpleNamespace(message_get=message_get)
    log.record_send(
        SimpleNamespace(
            account=bot,
            channel="40001",
            message=[Text("状态"), Image(src="data:image/png;base64,AAAA")],
            result=[SimpleNamespace(id="900")],
            session=None,
        )
    )
    await log.flush()

    [record] = await repository.messages("onebot", "10001", "40001")
    assert json.loads(record.content) == [
        {"type": "text", "text": "状态"},
        {"type": "image", "src": "https://img.example/status.png"},
    ]
