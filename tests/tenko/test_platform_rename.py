"""QQ 账号平台名从 onebot 改为 qq-client：旧数据、旧配置和旧路由继续可用。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tenko.context import LEGACY_QQ_PLATFORM, QQ_PLATFORM
from tenko.db.repositories import (
    ChatMessageRepository,
    ChatRecord,
    ManagementRepository,
)
from tenko.host import perm
from tenko.host.accounts import account_key, account_reference


def message(platform: str, message_id: str) -> ChatRecord:
    return ChatRecord(
        platform=platform,
        account_id="10001",
        chat_type="group",
        channel_id="40001",
        channel_name="",
        message_id=message_id,
        outgoing=False,
        user_id="20001",
        user_name="",
        content="[]",
        created_at=1.0,
    )


def preference(platform: str, alias: str) -> dict:
    return {
        "platform": platform,
        "id": "10001",
        "alias": alias,
        "enabled": True,
        "capabilities": {},
    }


@pytest.mark.asyncio
async def test_legacy_rows_are_renamed_and_newer_rows_win(tenko_database):
    management = ManagementRepository()
    chat = ChatMessageRepository()
    await management.save_account(preference(LEGACY_QQ_PLATFORM, "旧"))
    await chat.add([message(LEGACY_QQ_PLATFORM, "1"), message(LEGACY_QQ_PLATFORM, "2")])
    # 回滚后再升级：同一账号、同一条消息已经有新平台名的行。
    await management.save_account(preference(QQ_PLATFORM, "新"))
    await chat.add([message(QQ_PLATFORM, "2")])

    assert await management.rename_platform(LEGACY_QQ_PLATFORM, QQ_PLATFORM) == 1
    assert await management.rename_platform(LEGACY_QQ_PLATFORM, QQ_PLATFORM) == 0

    assert [(row["platform"], row["alias"]) for row in await management.accounts()] == [
        (QQ_PLATFORM, "新")
    ]
    rows = await chat.messages(QQ_PLATFORM, "10001", "40001")
    assert sorted(row.message_id for row in rows) == ["1", "2"]


def test_legacy_config_and_references_still_resolve(monkeypatch):
    config = SimpleNamespace(basic=SimpleNamespace(superusers={"onebot": ["20001"]}))
    monkeypatch.setattr(perm.EntariConfig, "_inited", True, raising=False)
    monkeypatch.setattr(perm.EntariConfig, "instance", config, raising=False)

    assert perm.superuser_ids(QQ_PLATFORM) == ("20001",)
    assert account_key("10001") == (QQ_PLATFORM, "10001")
    assert account_key('["onebot", "10001"]') == (QQ_PLATFORM, "10001")
    assert account_reference((QQ_PLATFORM, "10001")) == "10001"
