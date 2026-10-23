from __future__ import annotations

from datetime import datetime

import pytest
from arclet.entari.event.base import FriendRequestEvent
from arclet.entari.command import Query
from satori import Channel, ChannelType, EventType, Login, MessageObject, User
from satori.model import Event

from test_perm_manager import make_private_session
from perm_support import make_checker
from tenko.host.accounts import AccountRegistry
from tenko.host.actions import ActionService
from tenko.host.guards import REQUIRES_ATTR
from tenko.host.inbox import inbox
from tenko.host.perm import Permission

MASTER, ADMIN, STRANGER = "90001", "90002", "20001"


class FriendProtocol:
    def __init__(self) -> None:
        self.approvals: list[tuple[str, bool]] = []
        self.private_messages: list[tuple[str, str]] = []

    async def friend_approve(self, request_id, approved, comment):
        self.approvals.append((request_id, approved))

    async def send_private_message(self, user_id, content):
        self.private_messages.append((str(user_id), str(content)))
        return [MessageObject("notice-1", str(content))]


class FriendAccount:
    self_id = "10001"
    platform = "onebot"

    def __init__(self) -> None:
        self.protocol = FriendProtocol()


def install(plugin):
    account = FriendAccount()
    accounts = AccountRegistry()
    accounts.register(account)
    registry = make_checker(MASTER, bot_admins=[ADMIN])
    checker = registry
    plugin.permission_checker = checker
    plugin.action_service = ActionService(accounts, checker)
    return account


def friend_event(account, user_id: str) -> FriendRequestEvent:
    origin = Event(
        type=EventType.FRIEND_REQUEST,
        timestamp=datetime.now(),
        login=Login(platform="onebot", user=User(account.self_id)),
        channel=Channel(f"private:{user_id}", ChannelType.DIRECT),
        user=User(user_id, "申请人"),
        message=MessageObject("flag-1", "我是群友"),
    )
    return FriendRequestEvent(account, origin)


async def set_stored_mode(mode: str | None) -> None:
    from tenko.host.friend_requests import default_repository, set_mode

    if mode is not None:
        await set_mode(default_repository(), mode)


# None 表示没存过设置（默认仅管理员）；db-down 表示数据库不可用，必须失败关闭：
# 即使申请人是 Master 也不自动同意，改为通知 Master。
@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["friend_request"], indirect=True)
@pytest.mark.parametrize(
    ("mode", "applicant", "approved", "notified"),
    [
        (None, MASTER, True, False),
        (None, ADMIN, True, False),
        (None, STRANGER, False, True),
        ("admins", ADMIN, True, False),
        ("admins", STRANGER, False, True),
        ("all", STRANGER, True, False),
        ("off", MASTER, False, False),
        ("off", STRANGER, False, False),
        ("db-down", MASTER, False, True),
    ],
)
async def test_friend_request_decision_per_mode_and_role(
    loaded_plugin, tenko_database, mode, applicant, approved, notified
):
    if mode == "db-down":
        from tenko.db.repositories import configure_session_factory

        configure_session_factory(None)
    else:
        await set_stored_mode(mode)
    account = install(loaded_plugin)

    await loaded_plugin.friend_request_event(friend_event(account, applicant))

    assert account.protocol.approvals == ([("flag-1", True)] if approved else [])
    messages = account.protocol.private_messages
    assert [user for user, _ in messages] == ([MASTER] if notified else [])
    # 留给 Master 处理的申请同时出现在面板的待处理列表里。
    assert len(inbox) == int(notified)
    if notified:
        text = messages[0][1]
        assert applicant in text and "我是群友" in text and "flag-1" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["friend_request"], indirect=True)
async def test_friend_request_command_persists_mode_and_takes_effect(
    loaded_plugin, tenko_database
):
    command = loaded_plugin.friend_request_policy.callable_target
    assert getattr(command, REQUIRES_ATTR) == {
        "chat": "private",
        "user": Permission.Master,
    }
    session = make_private_session(MASTER)

    assert "仅管理员" in str(await command(session, Query("mode", None)))
    query = Query("mode", "所有人")
    query.available = True
    assert "所有人" in str(await command(session, query))
    assert "所有人" in str(await command(session, Query("mode", None)))

    account = install(loaded_plugin)
    await loaded_plugin.friend_request_event(friend_event(account, STRANGER))
    assert account.protocol.approvals == [("flag-1", True)]
