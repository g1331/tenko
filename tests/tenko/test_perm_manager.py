from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from arclet.entari.command import Match, Query
from satori import (
    Channel,
    ChannelType,
    EventType,
    Guild,
    Login,
    MessageObject,
    Role,
    User,
)
from satori.model import Event, Member

from perm_support import make_checker
from tenko.host import notify


def make_session(user_id: str, role: str = "member", *, group: str | None = "40001"):
    event = Event(
        type=EventType.MESSAGE_CREATED,
        timestamp=datetime.now(),
        login=Login(platform="onebot", user=User("10001")),
        channel=Channel(group, ChannelType.TEXT)
        if group
        else Channel(f"private:{user_id}", ChannelType.DIRECT),
        guild=Guild(group, "Tenko") if group else None,
        member=Member(user=User(user_id), roles=[Role(role)]) if group else None,
        user=User(user_id),
        message=MessageObject.from_elements("50001", []),
    )
    return SimpleNamespace(
        event=SimpleNamespace(
            _origin=event, channel=event.channel, guild=event.guild, user=event.user
        ),
    )


def make_private_session(user_id: str):
    return make_session(user_id, group=None)


def match(value) -> Match:
    return Match(value, True)


def query(path: str, value=None) -> Query:
    result = Query(path, value)
    result.available = value is not None
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["perm_manager"], indirect=True)
async def test_group_admin_grants_members_but_not_across_groups(loaded_plugin) -> None:
    checker = loaded_plugin.permission_checker = make_checker()
    loaded_plugin._in_group = AsyncMock(
        side_effect=lambda _s, _g, user: user != "39999"
    )
    admin = make_session("20001", "admin")

    result = await loaded_plugin.grant.callable_target(
        admin, match(("30001", "39999")), query("group.group_id")
    )

    assert "1个执行成功,1个失败" in result and "39999-没有在群40001" in result
    assert checker.store.grants("qq-client", "40001") == ("30001",)
    denied = await loaded_plugin.grant.callable_target(
        admin, match(("30002",)), query("group.group_id", "40002")
    )
    assert denied == "跨群授权需要 BOT 管理员"


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["perm_manager"], indirect=True)
async def test_blacklist_protects_master_and_bot_admins(loaded_plugin) -> None:
    checker = loaded_plugin.permission_checker = make_checker(
        "90001", bot_admins=["20003"]
    )
    loaded_plugin.superuser_ids = lambda _platform: ("90001",)

    result = await loaded_plugin.add_user_blacklist.callable_target(
        make_session("20003", group=None),
        match(("90001", "20003", "30001")),
        query("reason.reason", "刷屏"),
    )

    assert "1个执行成功,2个失败" in result
    assert "90001-无法拉黑 Master" in result and "20003-无法拉黑BOT管理" in result
    (record,) = checker.store.blacklist("user")
    assert (record.target_id, record.reason, record.operator_id) == (
        "30001",
        "刷屏",
        "20003",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["perm_manager"], indirect=True)
async def test_group_blacklist_needs_a_group_and_spares_the_notify_group(
    loaded_plugin, monkeypatch
) -> None:
    checker = loaded_plugin.permission_checker = make_checker(bot_admins=["20003"])
    monkeypatch.setattr(
        loaded_plugin,
        "load_settings",
        AsyncMock(return_value=notify.NotifySettings("group", "40002")),
    )
    add = loaded_plugin.add_group_blacklist.callable_target
    no_group = Match(None, False)

    private = make_session("20003", group=None)
    assert await add(private, no_group, query("reason.reason")) == "私聊中请提供群号"
    assert "通知群" in await add(private, match("40002"), query("reason.reason"))
    await add(make_session("20003"), no_group, query("reason.reason"))

    assert checker.store.blacklisted("group", "qq-client", "40001")
    assert not checker.store.blacklisted("group", "qq-client", "40002")


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["perm_manager"], indirect=True)
async def test_leaving_the_group_revokes_its_grant(loaded_plugin) -> None:
    checker = loaded_plugin.permission_checker = make_checker()
    await checker.store.grant("qq-client", "40001", "30001")
    await checker.store.grant("qq-client", "40002", "30001")

    await loaded_plugin.revoke_on_leave(
        SimpleNamespace(
            login=SimpleNamespace(platform="qq-client"),
            guild=SimpleNamespace(id="40001"),
            user=SimpleNamespace(id="30001"),
        )
    )

    assert checker.store.grants("qq-client", "40001") == ()
    assert checker.store.grants("qq-client", "40002") == ("30001",)
