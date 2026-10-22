from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from arclet.entari.command import Match
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

from tenko.host.features import FeatureService
from tenko.host.perm import PermissionChecker, PermissionRegistry


def make_session(user_id: str = "20001", role: str = "admin"):
    event = Event(
        type=EventType.MESSAGE_CREATED,
        timestamp=datetime.now(),
        login=Login(platform="onebot", user=User("10001")),
        channel=Channel("40001", ChannelType.TEXT),
        guild=Guild("40001", "Tenko"),
        member=Member(user=User(user_id), roles=[Role(role)]),
        user=User(user_id),
        message=MessageObject.from_elements("50001", []),
    )
    return SimpleNamespace(event=SimpleNamespace(_origin=event))


@pytest.mark.parametrize("loaded_plugin", ["feature_manager"], indirect=True)
def test_feature_commands_drop_legacy_dash_aliases(loaded_plugin) -> None:
    assert loaded_plugin.enable_command.parse("/开启 1").matched
    assert loaded_plugin.disable_command.parse("/关闭 1").matched
    assert not loaded_plugin.enable_command.parse("/-开启 1").matched
    assert not loaded_plugin.disable_command.parse("/-关闭 1").matched


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["feature_manager"], indirect=True)
async def test_feature_commands_persist_group_switch_and_restore(
    loaded_plugin, repositories, monkeypatch
) -> None:
    service = FeatureService(repositories["feature"])
    await service.initialize()
    monkeypatch.setattr(loaded_plugin, "feature_service", service)
    loaded_plugin.permission_checker = PermissionChecker(registry=PermissionRegistry())
    session = make_session()

    disabled = await loaded_plugin.disable.callable_target(
        session, Match("announcement", True)
    )

    assert str(disabled) == "功能<announcement>已关闭~"
    assert not service.is_enabled("announcement", "40001")
    restored = FeatureService(repositories["feature"])
    await restored.initialize()
    assert not restored.is_enabled("announcement", "40001")

    enabled = await loaded_plugin.enable.callable_target(
        session, Match("announcement", True)
    )

    assert str(enabled) == "功能<announcement>已开启~"
    await restored.initialize()
    assert restored.is_enabled("announcement", "40001")


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["feature_manager"], indirect=True)
async def test_feature_commands_cannot_disable_required_host_plugin(loaded_plugin):
    loaded_plugin.permission_checker = PermissionChecker(registry=PermissionRegistry())

    result = await loaded_plugin.disable.callable_target(
        make_session(), Match("feature_manager", True)
    )

    assert str(result) == "无法操作必须插件<功能开关>"


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["feature_manager"], indirect=True)
async def test_feature_commands_can_disable_required_function_plugin(
    loaded_plugin, monkeypatch
) -> None:
    info = loaded_plugin.PluginInfo(
        name="announcement",
        path=Path("announcement"),
        is_package=True,
        qualified_name="tenko.plugins.announcement",
    )
    native = SimpleNamespace(
        id="tenko.plugins.announcement",
        metadata=SimpleNamespace(name="公告", classifier=("required",)),
    )
    monkeypatch.setattr(loaded_plugin.plugin_runtime, "discover", lambda: (info,))
    monkeypatch.setattr(
        "tenko.host.plugins.runtime.get_plugins", lambda *, subplugged: (native,)
    )
    service = FeatureService()
    monkeypatch.setattr(loaded_plugin, "feature_service", service)
    loaded_plugin.permission_checker = PermissionChecker(registry=PermissionRegistry())

    result = await loaded_plugin.disable.callable_target(
        make_session(), Match("announcement", True)
    )

    assert str(result) == "功能<公告>已关闭~"
    assert not service.is_enabled("announcement", "40001")


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["feature_manager"], indirect=True)
async def test_feature_commands_toggle_global_feature(loaded_plugin, monkeypatch):
    info = loaded_plugin.PluginInfo(
        name="startup_notify",
        path=Path("startup_notify"),
        is_package=True,
        qualified_name="tenko.plugins.startup_notify",
    )
    native = SimpleNamespace(
        id="tenko.plugins.startup_notify",
        metadata=SimpleNamespace(
            name="启动通知",
            classifier=("required",),
            feature_scope="global",
        ),
    )
    monkeypatch.setattr(loaded_plugin.plugin_runtime, "discover", lambda: (info,))
    monkeypatch.setattr(
        "tenko.host.plugins.runtime.get_plugins", lambda *, subplugged: (native,)
    )
    service = FeatureService()
    monkeypatch.setattr(loaded_plugin, "feature_service", service)
    loaded_plugin.permission_checker = PermissionChecker(
        registry=PermissionRegistry(bot_admin_ids=["20001"])
    )

    refused = await loaded_plugin.disable.callable_target(
        make_session("20002"), Match("startup_notify", True)
    )
    assert str(refused) == "权限不足"
    assert service.is_enabled("startup_notify", "40001")

    result = await loaded_plugin.disable.callable_target(
        make_session("20001"), Match("startup_notify", True)
    )

    assert str(result) == "功能<启动通知>已关闭~"
    assert not service.is_enabled("startup_notify", "40001")
    assert not service.is_enabled("startup_notify", "40002")


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["feature_manager"], indirect=True)
async def test_feature_commands_reject_unknown_plugin(loaded_plugin):
    loaded_plugin.permission_checker = PermissionChecker(registry=PermissionRegistry())

    unknown_result = await loaded_plugin.disable.callable_target(
        make_session(), Match("not-a-plugin", True)
    )
    assert str(unknown_result) == "编号不在运行插件范围内~"


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["feature_manager"], indirect=True)
async def test_feature_numbers_match_help_and_external_cannot_claim_protection(
    loaded_plugin, monkeypatch
) -> None:
    def info(name: str, source: str) -> object:
        return loaded_plugin.PluginInfo(
            name,
            None,
            False,
            f"mod_{name}",
            source,  # type: ignore[arg-type]
        )

    infos = (
        info("local", "builtin"),
        info("ext_off", "external"),
        info("ext_on", "external"),
    )
    natives = (
        SimpleNamespace(
            id="mod_local", metadata=SimpleNamespace(name="本地", classifier=())
        ),
        # 第三方插件自带 classifier=host 也不能让它变成受保护插件。
        SimpleNamespace(
            id="mod_ext_on",
            metadata=SimpleNamespace(name="外部", classifier=("required", "host")),
        ),
    )
    monkeypatch.setattr(loaded_plugin.plugin_runtime, "discover", lambda: infos)
    monkeypatch.setattr(
        "tenko.host.plugins.runtime.get_plugins", lambda *, subplugged: natives
    )
    service = FeatureService()
    monkeypatch.setattr(loaded_plugin, "feature_service", service)
    loaded_plugin.permission_checker = PermissionChecker(registry=PermissionRegistry())

    # /帮助 只列出已加载的插件：ext_on 是第 2 号，而不是发现顺序里的第 3 号。
    assert loaded_plugin.resolve_plugin("2").name == "ext_on"
    assert loaded_plugin.resolve_plugin("3") is None
    assert not loaded_plugin._is_protected(infos[2])

    result = await loaded_plugin.disable.callable_target(
        make_session(), Match("2", True)
    )
    assert str(result) == "功能<外部>已关闭~"
    assert not service.is_enabled("ext_on", "40001")


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["feature_manager"], indirect=True)
async def test_clear_plugin_data_asks_for_confirmation(
    loaded_plugin, monkeypatch, tmp_path
) -> None:
    from external_plugin_support import entry, patch_entry_points

    monkeypatch.chdir(tmp_path)
    patch_entry_points(monkeypatch, entry("demo", "tenko_t_clear"))
    data = tmp_path / ".tenko" / "plugin-data" / "demo"
    data.mkdir(parents=True)
    (data / "data.db").write_bytes(b"x")

    def session(answer: str):
        async def send(_message) -> None: ...

        async def prompt(**_kwargs) -> str:
            return answer

        return SimpleNamespace(send=send, prompt=prompt)

    async def clear(answer: str, plugin_id: str = "demo") -> str:
        result = await loaded_plugin.clear_data.callable_target(
            session(answer), Match(plugin_id, True)
        )
        return str(result)

    assert await clear("否") == "已取消"
    assert data.is_dir()
    assert await clear("是") == "已清除插件<demo>的数据"
    assert not data.exists()
    assert "只能清除" in await clear("是", "missing")
    # 已卸载的插件没有入口了，保留的数据仍可清除。
    gone = tmp_path / ".tenko" / "plugin-data" / "gone"
    gone.mkdir()
    assert await clear("是", "gone") == "已清除插件<gone>的数据"
    assert not gone.exists()
    assert "只能清除" in await clear("是", "../demo")
