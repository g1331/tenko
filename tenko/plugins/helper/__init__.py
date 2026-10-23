from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from arclet.alconna import Alconna, Args, CommandMeta, Option, store_true
from arclet.alconna import command_manager
from arclet.entari import Session, command, plugin
from arclet.entari.command import Query
from arclet.entari.plugin import Plugin, PluginRole, get_plugin_commands

from tenko.host.features import feature_service
from tenko.host.guards import require
from tenko.host.perm import Permission
from tenko.host.plugins.docs import (
    command_prefix,
    format_fields,
    plugin_command_help,
    plugin_icon_path,
)
from tenko.host.plugins.runtime import PluginInfo, PluginRuntime
from tenko.context import context_from_session, group_reference
from tenko.plugins._common import render_or_text
from tenko.host.render import RenderService, data_uri


plugin.metadata(
    "帮助系统",
    PluginRole.NORMAL,
    author=["13"],
    description="根据 Entari 当前注册命令生成帮助文本。",
    classifier=["required"],
)


plugin_runtime = PluginRuntime()

_HELP_SECTIONS = (
    ("required", "系统插件", "系统必需功能"),
    ("available", "运行插件", "当前可用功能"),
    ("unavailable", "维护插件", "暂不可用功能"),
    # 仅在有已加载的扩展插件时显示。
    ("third_party", "扩展插件", "独立安装的插件包"),
)

help_command = Alconna(
    "帮助",
    Args["index?", int],
    Option(
        "-t",
        alias=["--text"],
        action=store_true,
        default=False,
        help_text="强制使用文本输出",
    ),
    meta=CommandMeta(
        "列出 Entari 已注册命令或查看单条命令详情",
        usage="帮助 [编号] [-t]",
        example="$帮助\n$帮助 1\n$帮助 1 -t",
        compact=True,
    ),
)


def registered_commands():
    """返回 Alconna 维护的实时命令注册表。"""

    return [item for item in command_manager.get_commands() if not item.meta.hide]


def _help_plugins() -> tuple[tuple[PluginInfo, object], ...]:
    plugins: list[tuple[PluginInfo, object]] = []
    for info in plugin_runtime.discover():
        native_plugin = info.native_plugin
        if native_plugin is not None:
            plugins.append((info, native_plugin))
    return tuple(plugins)


def _plugin_help(native_plugin: object) -> str:
    """将插件注册的命令元数据格式化为纯文本帮助。"""

    commands = plugin_command_help(native_plugin)
    if not commands:
        return "该插件未注册命令"
    return "\n\n".join(format_fields(*command) for command in commands)


def _plugin_icon(info: PluginInfo) -> str:
    return data_uri(plugin_icon_path(info.name, info.source))


def _metadata_value(native_plugin: object | None, field: str) -> object | None:
    return getattr(getattr(native_plugin, "metadata", None), field, None)


def _classifiers(native_plugin: object | None) -> tuple[str, ...]:
    value = _metadata_value(native_plugin, "classifier")
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return ()


def _plugin_command_names(native_plugin: object | None) -> list[str]:
    if not isinstance(native_plugin, Plugin):
        return []
    return [
        f"{command_prefix(command_manager.get_command(name), prefixes)}{name}"
        for prefixes, name in get_plugin_commands(native_plugin)
    ]


def _plugin_item(
    info: PluginInfo,
    native_plugin: object | None,
    number: int,
    state: str,
    state_label: str,
) -> dict[str, Any]:
    name = _metadata_value(native_plugin, "name") or info.name
    description = _metadata_value(native_plugin, "description") or ""
    return {
        "number": number,
        "name": str(name),
        "plugin": info.name,
        "description": str(description),
        "state": state,
        "state_label": state_label,
        "icon": _plugin_icon(info),
        "commands": _plugin_command_names(native_plugin),
    }


def build_help_data(group_id: str | int | None = None) -> dict[str, Any]:
    """根据已加载的原生插件构建由三个部分组成的帮助模型。

    目录扫描由 ``PluginRuntime`` 负责并刻意保留在那里。这里只显示当前已向
    Entari 注册的插件，因此未导入的目录不会作为看似可运行的功能误显示。
    """

    sections: dict[str, list[dict[str, Any]]] = {
        key: [] for key, _, _ in _HELP_SECTIONS
    }
    for number, (info, native_plugin) in enumerate(_help_plugins(), 1):
        classifiers = _classifiers(native_plugin)
        if info.source == "external":
            # 扩展插件自带的 classifier 不可信，不能借 host/required 进入系统区。
            key = "third_party"
            state_label = (
                "维护中"
                if feature_service.is_maintenance(info.name)
                else "运行中"
                if feature_service.is_enabled(info.name, group_id)
                else "已关闭"
            )
        elif "host" in classifiers:
            key = "required"
            state_label = "受保护"
        elif feature_service.is_maintenance(info.name):
            key = "unavailable"
            state_label = "维护中"
        elif "required" in classifiers:
            key = "required"
            state_label = (
                "运行中"
                if feature_service.is_enabled(info.name, group_id)
                else "已关闭"
            )
        else:
            key = "available"
            state_label = (
                "运行中"
                if feature_service.is_enabled(info.name, group_id)
                else "已关闭"
            )
        sections[key].append(
            _plugin_item(info, native_plugin, number, key, state_label)
        )

    section_data = tuple(
        {
            "key": key,
            "title": title,
            "subtitle": subtitle,
            "items": tuple(sections[key]),
            "count": len(sections[key]),
        }
        for key, title, subtitle in _HELP_SECTIONS
        if key != "third_party" or sections[key]
    )
    return {
        "title": "Tenko 已注册命令",
        "subtitle": "按插件状态整理可用功能",
        "usage": "/帮助 [编号] 查看单项命令详情",
        "group_id": None if group_id is None else str(group_id),
        "sections": section_data,
        "required": tuple(sections["required"]),
        "available": tuple(sections["available"]),
        "unavailable": tuple(sections["unavailable"]),
        "third_party": tuple(sections["third_party"]),
        "total": number,
        "enabled_count": sum(
            item["state"] in {"required", "available", "third_party"}
            and item["state_label"] not in {"已关闭", "维护中"}
            for item in (
                *sections["required"],
                *sections["available"],
                *sections["unavailable"],
                *sections["third_party"],
            )
        ),
    }


def format_help_text(data: dict[str, Any]) -> str:
    """将帮助模型格式化为文本回退结果。"""

    lines = [str(data["title"]), f"用法：{data['usage']}"]
    for section in data["sections"]:
        lines.extend(("", f"{section['title']}："))
        if not section["items"]:
            lines.append("└ 暂无")
            continue
        for index, item in enumerate(section["items"]):
            branch = "└" if index == len(section["items"]) - 1 else "├"
            lines.append(
                f"{branch} {item['number']}. {item['name']}（{item['state_label']}）"
            )
    return "\n".join(lines)


def build_help_detail_data(
    index: int, group_id: str | int | None = None
) -> dict[str, Any] | None:
    """单个插件的详情：菜单里的那一项加上每条命令的说明字段；编号越界时为 ``None``。"""

    plugins = _help_plugins()
    if not 1 <= index <= len(plugins):
        return None
    item = next(
        item
        for section in build_help_data(group_id)["sections"]
        for item in section["items"]
        if item["number"] == index
    )
    commands = [
        {"title": title, "fields": [{"label": k, "value": v} for k, v in fields]}
        for title, fields in plugin_command_help(plugins[index - 1][1])
    ]
    return {"item": item, "commands": commands}


def build_help(index: int | None = None, *, group_id: str | int | None = None) -> str:
    """构建完整的插件帮助，或某个插件的单项命令帮助。"""

    if index is not None:
        plugins = _help_plugins()
        if not 1 <= index <= len(plugins):
            return "编号不在范围内~"
        return _plugin_help(plugins[index - 1][1])
    return format_help_text(build_help_data(group_id))


@command.on(help_command)
@require(Permission.User)
async def helper(
    session: Session,
    index: Query[int] = Query("index"),
    text_mode: Query[bool] = Query("t.value", False),
    *,
    render_service: RenderService,
):
    context = context_from_session(session)
    value = index.result if index.available else None
    group_id = group_reference(context.group) if context.chat_type == "group" else None
    if value is not None:
        text = build_help(value)
        detail = None if text_mode.result else build_help_detail_data(value, group_id)
        if detail is None:
            return text
        return await render_or_text(render_service, "help_detail.html", detail, text)

    help_data = build_help_data(group_id)
    if text_mode.result:
        return format_help_text(help_data)
    return await render_or_text(
        render_service, "help.html", help_data, format_help_text(help_data)
    )
