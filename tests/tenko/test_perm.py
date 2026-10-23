"""权限模型（#234）：实时身份 + 手动授权、黑名单快照和事件入口拦截。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tenko.context import MessageContext
from tenko.db.errors import DatabaseUnavailableError
from tenko.events import MessageEventHandler
from tenko.host.accounts import AccountRegistry
from tenko.host.perm import GLOBAL_SCOPE, Permission, PermissionChecker, PermissionStore

from test_events import FakeAccount, make_group_event, make_private_event


def context(
    user_id: str = "20001", *, role: str | None = None, group: str | None = "40001"
) -> MessageContext:
    return MessageContext(
        account_id="10001",
        event_type="message-created",
        protocol_event_type=None,
        chat_type="group" if group else "private",
        channel_id=group or f"private:{user_id}",
        user_id=user_id,
        message_id="1",
        text="",
        image_urls=(),
        member_role=role,
    )


@pytest.mark.asyncio
async def test_level_is_max_of_live_role_and_manual_grant() -> None:
    store = PermissionStore()
    checker = PermissionChecker(store, masters=("90001",))
    await store.grant("qq-client", "40001", "20002")
    await store.grant("qq-client", GLOBAL_SCOPE, "20003")

    assert checker.level(context(role="owner")) == Permission.GroupOwner
    assert checker.level(context(role="admin")) == Permission.GroupAdmin
    # QQ 里撤了管理立即生效；认不出的角色算成员。
    assert checker.level(context(role="member")) == Permission.User
    assert checker.level(context(role="版主")) == Permission.User
    # 本群授权只在本群有效，群主不会被授权“降级”。
    assert checker.level(context("20002")) == Permission.GroupAdmin
    assert checker.level(context("20002", group="40009")) == Permission.User
    assert checker.level(context("20002", role="owner")) == Permission.GroupOwner
    # 私聊不看群身份；BotAdmin、Master 是全局的。
    assert checker.level(context("20003", group=None)) == Permission.BotAdmin
    assert checker.level(context("90001", group=None)) == Permission.Master


@pytest.mark.asyncio
async def test_store_round_trips_through_the_database(tenko_database) -> None:
    del tenko_database
    from tenko.db.repositories import permission_repository

    store = PermissionStore(permission_repository)
    await store.initialize()
    await store.add_blacklist("user", "onebot", "20001", reason="刷屏", operator_id="9")
    await store.add_blacklist("group", "qq-client", "40001")
    await store.grant("qq-client", GLOBAL_SCOPE, "20003")
    assert not await store.add_blacklist("user", "qq-client", "20001")

    reloaded = PermissionStore(permission_repository)
    await reloaded.initialize()
    # 旧平台名 onebot 归一为 qq-client。
    assert reloaded.blacklisted("user", "qq-client", "20001")
    assert reloaded.blacklist("user")[0].reason == "刷屏"
    assert reloaded.blacklisted("group", "qq-client", "40001")
    assert not reloaded.blacklisted("group", "discord", "40001")
    assert reloaded.grants("qq-client", GLOBAL_SCOPE) == ("20003",)

    assert await reloaded.remove_blacklist("user", "qq-client", "20001")
    assert await reloaded.revoke("qq-client", GLOBAL_SCOPE, "20003")
    again = PermissionStore(permission_repository)
    await again.initialize()
    assert not again.blacklisted("user", "qq-client", "20001")
    assert again.grants("qq-client", GLOBAL_SCOPE) == ()


@pytest.mark.asyncio
async def test_failed_write_leaves_memory_unchanged() -> None:
    repository = AsyncMock()
    repository.add_blacklist.side_effect = DatabaseUnavailableError("down")
    store = PermissionStore(repository)
    store._ready = True

    with pytest.raises(DatabaseUnavailableError):
        await store.add_blacklist("user", "qq-client", "20001")
    assert not store.blacklisted("user", "qq-client", "20001")


@pytest.mark.asyncio
async def test_blacklisted_user_and_group_never_reach_plugins() -> None:
    store = PermissionStore()
    await store.add_blacklist("user", "qq-client", "20009")
    await store.add_blacklist("group", "qq-client", "40009")
    registry = AccountRegistry()
    account = FakeAccount()
    registry.register(account, groups=["40001", "40009"])
    callback = AsyncMock()
    guarded = MessageEventHandler(account_registry=registry, permissions=store).guard(
        callback
    )

    await guarded(account, make_private_event(user_id="20009"))
    await guarded(account, make_group_event(user_id="20009"))
    await guarded(account, make_group_event(group_id="40009"))
    callback.assert_not_awaited()

    await guarded(account, make_group_event())
    callback.assert_awaited_once()
