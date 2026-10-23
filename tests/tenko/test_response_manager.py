from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from arclet.entari.command import Query
from satori import (
    Channel,
    ChannelType,
    EventType,
    Guild,
    Image,
    Login,
    MessageObject,
    Role,
    User,
)
from satori.model import Event, Member

from tenko.host.accounts import AccountRegistry


@dataclass
class FakeAccount:
    self_id: str


def make_registry() -> AccountRegistry:
    registry = AccountRegistry()
    first = FakeAccount("10001")
    second = FakeAccount("10002")
    registry.register(first, groups=[40001])
    registry.register(second, available=False, groups=[40001])
    registry.set_response_type(40001, "deterministic")
    registry.set_deterministic_account(40001, first)
    registry.set_muted(
        first,
        40001,
        True,
        until=datetime.now(UTC) + timedelta(minutes=5),
    )
    return registry


def make_session(role: str = "member"):
    event = Event(
        type=EventType.MESSAGE_CREATED,
        timestamp=datetime.now(),
        login=Login(platform="onebot", user=User("10001")),
        channel=Channel("40001", ChannelType.TEXT),
        guild=Guild("40001", "Tenko"),
        member=Member(user=User("20001"), roles=[Role(role)]),
        user=User("20001"),
        message=MessageObject.from_elements("50001", []),
    )
    return SimpleNamespace(
        event=SimpleNamespace(
            _origin=event,
            channel=event.channel,
            guild=event.guild,
            user=event.user,
        )
    )


def make_private_session(user_id: str):
    event = Event(
        type=EventType.MESSAGE_CREATED,
        timestamp=datetime.now(),
        login=Login(platform="onebot", user=User("10001")),
        channel=Channel(f"private:{user_id}", ChannelType.DIRECT),
        guild=None,
        member=None,
        user=User(user_id),
        message=MessageObject.from_elements("50001", []),
    )
    return SimpleNamespace(
        event=SimpleNamespace(
            _origin=event,
            channel=event.channel,
            guild=event.guild,
            user=event.user,
        )
    )


def make_query(path: str, result):
    query = Query(path, result)
    query.available = True
    return query


@pytest.mark.parametrize("loaded_plugin", ["response_manager"], indirect=True)
def test_query_commands_use_prefix_and_reject_bare_words(loaded_plugin) -> None:
    commands = (
        (loaded_plugin.bot_list_command, "/BOT列表", "/BOT列表 40001"),
        (loaded_plugin.bot_group_list_command, "/BOT群列表", "/BOT群列表 10001"),
    )
    for command, first, second in commands:
        assert command.prefixes == ["/"]
        assert command.parse(first).matched
        if command is loaded_plugin.bot_group_list_command:
            assert command.parse(second).matched
        else:
            assert not command.parse(second).matched
        assert not command.parse(first.removeprefix("/")).matched
        assert not command.parse(f"{first}x").matched
        assert not command.parse(f"/ {first.removeprefix('/')}").matched


@pytest.mark.parametrize("loaded_plugin", ["response_manager"], indirect=True)
def test_response_strategy_command_accepts_only_supported_values(loaded_plugin) -> None:
    command = loaded_plugin.response_strategy_command

    assert command.prefixes == ["/"]
    assert command.parse("/设定响应").matched
    assert command.parse("/设定响应 random").matched
    assert command.parse("/设定响应 deterministic").matched
    assert not command.parse("/设定响应 invalid").matched
    assert not command.parse("/设定响应 random extra").matched
    assert not command.parse("设定响应 random").matched


@pytest.mark.parametrize("loaded_plugin", ["response_manager"], indirect=True)
def test_specified_bot_command_accepts_numeric_id_or_clear(loaded_plugin) -> None:
    command = loaded_plugin.specified_bot_command

    assert command.prefixes == ["/"]
    assert command.parse("/指定BOT 10001").matched
    assert command.parse("/指定BOT 清除").matched
    assert not command.parse("/指定BOT").matched
    assert not command.parse("/指定BOT abc").matched
    assert not command.parse("/指定BOT 10001abc").matched
    assert not command.parse("/指定BOT 10001 extra").matched
    assert not command.parse("指定BOT 10001").matched


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["response_manager"], indirect=True)
async def test_response_strategy_handler_reads_and_persists_current_group_only(
    loaded_plugin, monkeypatch
) -> None:
    registry = AccountRegistry()
    registry.register(FakeAccount("10001"), groups=[40001])
    loaded_plugin.account_registry = registry
    persist = AsyncMock()
    monkeypatch.setattr(registry, "persist_state", persist)

    current = await loaded_plugin.set_response_strategy.callable_target(
        make_session("admin"), Query("response_type", None)
    )

    assert str(current) == "当前群响应策略：random"
    assert persist.await_count == 0

    changed = await loaded_plugin.set_response_strategy.callable_target(
        make_session("admin"), make_query("response_type", "deterministic")
    )

    assert str(changed) == "已将当前群响应策略设为 deterministic"
    assert registry.response_type_for_group("40001") == "deterministic"
    persist.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["response_manager"], indirect=True)
async def test_response_strategy_handler_rejects_unbound_group(
    loaded_plugin,
) -> None:
    loaded_plugin.account_registry = AccountRegistry()

    unbound = await loaded_plugin.set_response_strategy.callable_target(
        make_session("admin"), Query("response_type", None)
    )
    assert str(unbound) == "当前群未绑定可用BOT"


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["response_manager"], indirect=True)
async def test_specified_bot_handler_validates_current_group_and_restores_default(
    loaded_plugin, monkeypatch
) -> None:
    registry = AccountRegistry()
    first = FakeAccount("10001")
    second = FakeAccount("10002")
    registry.register(first, groups=[40001])
    registry.register(second, available=False, groups=[40001])
    loaded_plugin.account_registry = registry
    persist = AsyncMock()
    monkeypatch.setattr(registry, "persist_state", persist)

    selected = await loaded_plugin.choose_response_bot.callable_target(
        make_session("admin"), make_query("account_id", "10002")
    )

    assert str(selected) == "已成功设定群指定响应BOT为10002"
    assert registry.response_type_for_group("40001") == "deterministic"
    assert registry.deterministic_account_for_group("40001") == "10002"
    persist.assert_awaited_once_with()

    cleared = await loaded_plugin.choose_response_bot.callable_target(
        make_session("admin"), make_query("account_id", "清除")
    )

    assert str(cleared) == "已清除当前群指定响应BOT，恢复默认选路"
    assert registry.deterministic_account_for_group("40001") == "10001"
    assert registry.select_account("40001") is first
    assert persist.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["response_manager"], indirect=True)
async def test_specified_bot_handler_hides_unknown_account_details(
    loaded_plugin,
) -> None:
    registry = AccountRegistry()
    registry.register(FakeAccount("10001"), groups=[40001])
    loaded_plugin.account_registry = registry

    result = await loaded_plugin.choose_response_bot.callable_target(
        make_session("admin"), make_query("account_id", "99999")
    )

    assert str(result) == "当前账号列表中没有找到这个 BOT"
    assert "KeyError" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["response_manager"], indirect=True)
async def test_bot_group_list_lists_every_account_group(
    loaded_plugin, monkeypatch
) -> None:
    registry = make_registry()
    second = FakeAccount("10003")
    registry.register(second, groups=[40002])
    loaded_plugin.account_registry = registry

    monkeypatch.setattr(
        "tenko.plugins._common.try_render", AsyncMock(return_value=None)
    )
    private_result = await loaded_plugin.bot_group_list.callable_target(
        make_private_session("90001"),
        Query("account_id", None),
        render_service=loaded_plugin.RenderService(),
    )
    assert "群40001" in str(private_result)
    assert "群40002" in str(private_result)


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["response_manager"], indirect=True)
async def test_bot_list_shows_each_bot_with_avatar_name_and_state(
    loaded_plugin, monkeypatch
) -> None:
    registry = make_registry()
    loaded_plugin.account_registry = registry
    fetch = AsyncMock(return_value="data:image/png;base64,AA==")
    monkeypatch.setattr(loaded_plugin, "_fetch_avatar", fetch)
    renderer = AsyncMock(return_value=b"jpeg-bytes")
    monkeypatch.setattr("tenko.plugins._common.try_render", renderer)

    result = await loaded_plugin.bot_list.callable_target(
        make_session(), render_service=loaded_plugin.RenderService()
    )

    assert isinstance(result[0], Image)
    assert renderer.await_args.args[1] == "bots.html"
    data = renderer.await_args.args[2]
    first, second = data["bots"]
    assert (first["id"], first["state"], first["chips"][0]["text"]) == (
        "10001",
        "禁言中",
        "负责回复",
    )
    assert first["avatar"].startswith("data:image/png")
    assert (second["state"], second["chips"][0]["text"]) == ("离线", "候补")
    assert data["stats"][:2] == [(0, "可回复"), (2, "已绑定")]
    fetch.assert_any_await("https://q1.qlogo.cn/g?b=qq&nk=10001&s=100")

    # 渲染失败时退回纯文本，同样列出每个 BOT。
    monkeypatch.setattr(
        "tenko.plugins._common.try_render", AsyncMock(return_value=None)
    )
    text = str(
        await loaded_plugin.bot_list.callable_target(
            make_session(), render_service=loaded_plugin.RenderService()
        )
    )
    assert "可回复 0/2（定向）" in text and "10002：离线 · 候补" in text
