from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from arclet.entari.command import Query
from arclet.entari.plugin import get_plugins
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


def make_session(user_id: str, role: str):
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
    return SimpleNamespace(
        event=SimpleNamespace(
            _origin=event,
            channel=event.channel,
            guild=event.guild,
            user=event.user,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
async def test_helper_trigger_uses_native_command_registry(
    loaded_plugin, monkeypatch
) -> None:
    monkeypatch.setattr(
        "tenko.plugins._common.try_render", AsyncMock(return_value=None)
    )

    result = await loaded_plugin.helper.callable_target(
        make_session("20001", "member"),
        Query("index", None),
        render_service=loaded_plugin.RenderService(),
    )

    assert "Tenko 已注册命令" in str(result)
    assert "系统插件：" in str(result)
    assert "运行插件：" in str(result)
    assert "维护插件：" in str(result)
    assert loaded_plugin.help_command.parse("/帮助").matched
    assert not loaded_plugin.help_command.parse("/-help 1").matched
    assert not loaded_plugin.help_command.parse("/-帮助 1").matched
    assert not loaded_plugin.help_command.parse("帮助").matched


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_help_data_partitions_required_available_and_maintenance(
    monkeypatch, loaded_plugin
):
    infos = tuple(
        loaded_plugin.PluginInfo(
            name=name,
            path=Path(name),
            is_package=True,
            qualified_name=f"tenko.plugins.{name}",
        )
        for name in ("required", "available", "disabled", "maintenance")
    )
    native_plugins = tuple(
        SimpleNamespace(
            id=f"tenko.plugins.{name}",
            metadata=SimpleNamespace(
                name=f"{name} plugin",
                description=f"{name} description",
                classifier=("required",) if name == "required" else (),
            ),
        )
        for name in ("required", "available", "disabled", "maintenance")
    )

    class FakeFeatureService:
        @staticmethod
        def is_maintenance(plugin_name):
            return plugin_name == "maintenance"

        @staticmethod
        def is_enabled(plugin_name, group_id):
            return plugin_name not in {"disabled", "required"}

    monkeypatch.setattr(loaded_plugin.plugin_runtime, "discover", lambda: infos)
    monkeypatch.setattr(
        "tenko.host.plugins.get_plugins", lambda *, subplugged: native_plugins
    )
    monkeypatch.setattr(loaded_plugin, "feature_service", FakeFeatureService())

    data = loaded_plugin.build_help_data("40001")

    assert [item["name"] for item in data["required"]] == ["required plugin"]
    assert [item["name"] for item in data["available"]] == [
        "available plugin",
        "disabled plugin",
    ]
    assert [item["name"] for item in data["unavailable"]] == ["maintenance plugin"]
    assert data["required"][0]["state_label"] == "已关闭"
    assert data["available"][0]["state_label"] == "运行中"
    assert data["available"][1]["state_label"] == "已关闭"
    assert data["unavailable"][0]["state_label"] == "维护中"
    assert data["total"] == 4
    assert data["enabled_count"] == 1

    text = loaded_plugin.format_help_text(data)
    assert (
        text.index("系统插件：") < text.index("运行插件：") < text.index("维护插件：")
    )
    assert "├ " in text
    assert "└ " in text


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_help_marks_host_plugins_as_protected(monkeypatch, loaded_plugin):
    info = loaded_plugin.PluginInfo(
        name="updater",
        path=Path("updater"),
        is_package=True,
        qualified_name="tenko.plugins.updater",
    )
    native_plugin = SimpleNamespace(
        id="tenko.plugins.updater",
        metadata=SimpleNamespace(
            name="升级系统",
            description="管理宿主升级。",
            classifier=("required", "host"),
        ),
    )
    monkeypatch.setattr(loaded_plugin.plugin_runtime, "discover", lambda: (info,))
    monkeypatch.setattr(
        "tenko.host.plugins.get_plugins", lambda *, subplugged: (native_plugin,)
    )

    data = loaded_plugin.build_help_data()

    assert data["sections"][0]["title"] == "系统插件"
    assert data["sections"][0]["subtitle"] == "系统必需功能"
    assert data["required"][0]["state_label"] == "受保护"


def use_fake_native_commands(monkeypatch, loaded_plugin) -> None:
    """测试里的原生插件是假对象：命令登记取自 ``_extra``，且没有声明权限要求。"""

    monkeypatch.setattr(
        loaded_plugin, "get_plugin_commands", lambda plugin: plugin._extra["commands"]
    )
    monkeypatch.setattr(loaded_plugin, "command_requirements", lambda plugin: {})


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_command_help_lists_option_descriptions(loaded_plugin):
    from arclet.alconna import Alconna, Args, CommandMeta, Option

    command = Alconna(
        "示例",
        Option("--time", Args["time", int], alias=["-t"], help_text="时长（分钟）"),
        Option("--quiet"),  # 没写说明的选项不展示
        meta=CommandMeta("示例命令"),
    )

    text = loaded_plugin._format_command_help(command, "示例", ["/"])

    assert "选项：--time/-t：时长（分钟）" in text
    assert "--quiet" not in text
    assert "--help" not in text


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_help_number_shows_declared_permission_of_real_commands(loaded_plugin):
    native = next(item for item in get_plugins() if item.id == "tenko.plugins.helper")

    assert loaded_plugin._plugin_help(native).endswith("└ 权限：成员")


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_help_number_uses_plugin_commands_for_card_positions(
    monkeypatch, loaded_plugin
):
    use_fake_native_commands(monkeypatch, loaded_plugin)
    plugin_names = (
        "announcement",
        "exception_catcher",
        "feature_manager",
        "group_manager",
        "helper",
        "perm_manager",
        "render",
        "response_manager",
        "status",
        "updater",
    )
    commands_by_plugin = {
        "group_manager": (
            "同意邀请",
            "拒绝邀请",
            "待审邀请",
            "退群",
            "重置能力",
            "群设置",
            "禁言",
            "解禁",
            "解禁自己",
            "全体禁言",
            "全体解禁",
            "撤回",
            "踢出",
        ),
        "updater": ("检查更新", "升级", "回滚", "重启"),
    }
    infos = tuple(
        loaded_plugin.PluginInfo(
            name=name,
            path=Path(name),
            is_package=True,
            qualified_name=f"tenko.plugins.{name}",
        )
        for name in plugin_names
    )

    def make_native_plugin(name):
        return SimpleNamespace(
            id=f"tenko.plugins.{name}",
            metadata=SimpleNamespace(
                name=f"{name} plugin",
                description=f"{name} description",
                classifier=("required",),
            ),
            _extra={
                "commands": [
                    (["/"], command_name)
                    for command_name in commands_by_plugin.get(name, ())
                ]
            },
        )

    native_plugins = tuple(make_native_plugin(name) for name in plugin_names)
    command_help = {
        command_name: SimpleNamespace(
            command=command_name,
            prefixes=["/"],
            meta=SimpleNamespace(
                description=f"{command_name} description",
                usage=f"{command_name} <参数>",
                example=f"/{command_name} example",
            ),
        )
        for commands in commands_by_plugin.values()
        for command_name in commands
    }
    lookups = []

    def get_command(command_name):
        lookups.append(command_name)
        return command_help[command_name]

    monkeypatch.setattr(loaded_plugin.plugin_runtime, "discover", lambda: infos)
    monkeypatch.setattr(
        "tenko.host.plugins.get_plugins", lambda *, subplugged: native_plugins
    )
    monkeypatch.setattr(loaded_plugin.command_manager, "get_command", get_command)

    data = loaded_plugin.build_help_data()
    items = {
        item["plugin"]: item
        for section in data["sections"]
        for item in section["items"]
    }

    assert items["group_manager"]["number"] == 4
    assert items["updater"]["number"] == 10
    assert loaded_plugin.build_help(4) == "\n\n".join(
        "\n".join(
            (
                f"/{command_name}",
                f"├ 说明：{command_name} description",
                f"├ 用法：/{command_name} <参数>",
                f"└ 示例：/{command_name} example",
            )
        )
        for command_name in commands_by_plugin["group_manager"]
    )
    assert loaded_plugin.build_help(10) == "\n\n".join(
        "\n".join(
            (
                f"/{command_name}",
                f"├ 说明：{command_name} description",
                f"├ 用法：/{command_name} <参数>",
                f"└ 示例：/{command_name} example",
            )
        )
        for command_name in commands_by_plugin["updater"]
    )
    assert loaded_plugin.build_help(2) == "该插件未注册命令"
    assert loaded_plugin.build_help(11) == "编号不在范围内~"
    assert lookups == [
        *commands_by_plugin["group_manager"],
        *commands_by_plugin["updater"],
    ]


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_plugin_help_omits_missing_metadata_fields(monkeypatch, loaded_plugin) -> None:
    use_fake_native_commands(monkeypatch, loaded_plugin)
    command = SimpleNamespace(
        command="测试",
        prefixes=["/"],
        meta=SimpleNamespace(description="测试说明", usage="测试 <参数>", example=None),
    )
    native_plugin = SimpleNamespace(_extra={"commands": [(["/"], "测试")]})
    monkeypatch.setattr(
        loaded_plugin.command_manager, "get_command", lambda command_name: command
    )

    assert loaded_plugin._plugin_help(native_plugin) == (
        "/测试\n├ 说明：测试说明\n└ 用法：/测试 <参数>"
    )


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_plugin_help_indents_multiline_metadata(monkeypatch, loaded_plugin) -> None:
    use_fake_native_commands(monkeypatch, loaded_plugin)
    command = SimpleNamespace(
        command="测试",
        prefixes=["/"],
        meta=SimpleNamespace(
            description=None,
            usage="测试 <参数>",
            example="测试 1\n/测试 2",
        ),
    )
    native_plugin = SimpleNamespace(_extra={"commands": [(["/"], "测试")]})
    monkeypatch.setattr(
        loaded_plugin.command_manager, "get_command", lambda command_name: command
    )

    assert loaded_plugin._plugin_help(native_plugin) == (
        "/测试\n├ 用法：/测试 <参数>\n└ 示例：/测试 1\n  /测试 2"
    )


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_help_lists_loaded_external_plugins_in_their_own_section(
    monkeypatch, loaded_plugin
):
    def info(name: str, source: str):
        return loaded_plugin.PluginInfo(name, None, False, f"mod_{name}", source)

    infos = (
        info("local", "builtin"),
        info("ext_on", "external"),
        info("ext_off", "external"),  # 已发现但未启用：没有原生插件，不显示
    )

    def native(name: str, *classifier: str):
        return SimpleNamespace(
            id=f"mod_{name}",
            metadata=SimpleNamespace(
                name=f"{name} plugin", description="", classifier=classifier
            ),
        )

    # 外部插件自带的 host / required 标记不能让它进入系统插件区。
    natives = (native("local"), native("ext_on", "required", "host"))

    class FakeFeatureService:
        @staticmethod
        def is_maintenance(plugin_name):
            return False

        @staticmethod
        def is_enabled(plugin_name, group_id):
            return True

    monkeypatch.setattr(loaded_plugin.plugin_runtime, "discover", lambda: infos)
    monkeypatch.setattr("tenko.host.plugins.get_plugins", lambda *, subplugged: natives)
    monkeypatch.setattr(loaded_plugin, "feature_service", FakeFeatureService())

    data = loaded_plugin.build_help_data("40001")

    assert [item["plugin"] for item in data["third_party"]] == ["ext_on"]
    assert data["third_party"][0]["state_label"] == "运行中"
    assert data["required"] == ()
    assert [item["plugin"] for item in data["available"]] == ["local"]
    assert [section["title"] for section in data["sections"]] == [
        "系统插件",
        "运行插件",
        "维护插件",
        "第三方",
    ]
    assert data["total"] == 2 and data["enabled_count"] == 2
    text = loaded_plugin.format_help_text(data)
    assert text.index("运行插件：") < text.index("第三方：")
    assert "2. ext_on plugin（运行中）" in text.split("第三方：")[1]


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_help_hides_third_party_section_when_none_loaded(monkeypatch, loaded_plugin):
    info = loaded_plugin.PluginInfo("local", None, False, "mod_local")
    native = SimpleNamespace(
        id="mod_local", metadata=SimpleNamespace(name="本地", classifier=())
    )
    monkeypatch.setattr(loaded_plugin.plugin_runtime, "discover", lambda: (info,))
    monkeypatch.setattr(
        "tenko.host.plugins.get_plugins", lambda *, subplugged: (native,)
    )
    data = loaded_plugin.build_help_data()
    assert [section["key"] for section in data["sections"]] == [
        "required",
        "available",
        "unavailable",
    ]
    assert "第三方" not in loaded_plugin.format_help_text(data)


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_help_icon_uses_registry_icon_then_default(
    tmp_path, monkeypatch, loaded_plugin
):
    icon = tmp_path / "ext-0123abcd.png"
    icon.write_bytes(b"\x89PNG\r\n\x1a\n")
    entries = {"ext": SimpleNamespace(id="ext")}
    registry = SimpleNamespace(
        cached=lambda: SimpleNamespace(find=entries.get),
        icon_path=lambda entry: icon,
    )
    monkeypatch.setattr(loaded_plugin, "get_registry", lambda: registry)

    def icon_of(name: str, source: str) -> str:
        info = loaded_plugin.PluginInfo(name, None, False, f"mod_{name}", source)
        return loaded_plugin._plugin_icon(info)

    assert icon_of("ext", "external").startswith("data:image/png;base64,")
    default = icon_of("unknown", "external")
    assert default.startswith("data:image/svg+xml;base64,")
    assert icon_of("helper", "builtin") not in {default, icon_of("ext", "external")}
