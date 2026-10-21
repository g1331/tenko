from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from arclet.alconna import Alconna, Args, CommandMeta, Option
from arclet.alconna import command_manager
from arclet.entari import Session, command, plugin
from arclet.entari.command import Query
from arclet.entari.plugin import Plugin, PluginRole, get_plugin_commands

from tenko.host.features import feature_service
from tenko.host.guards import command_requirements, require, requirement_text
from tenko.host.perm import Permission
from tenko.host.plugins import PluginInfo, PluginRuntime
from tenko.host.registry import get_registry
from tenko.plugins._common import context_from_session, render_or_text
from tenko.render import RenderService, data_uri


plugin.metadata(
    "帮助系统",
    PluginRole.NORMAL,
    author=["13"],
    version="0.1.0",
    description="根据 Entari 当前注册命令生成帮助文本。",
    classifier=["required"],
)


plugin_runtime = PluginRuntime()

_HELP_SECTIONS = (
    ("required", "系统插件", "系统必需功能"),
    ("available", "运行插件", "当前可用功能"),
    ("unavailable", "维护插件", "暂不可用功能"),
    # 仅在有已加载的第三方插件时显示。
    ("third_party", "第三方", "第三方插件包"),
)
_COMMAND_HELP_FIELDS = (("说明", "description"), ("用法", "usage"), ("示例", "example"))
_ICON_DIR = Path(__file__).resolve().parents[2] / "templates" / "assets" / "icons"
# 内置插件的菜单图标（Phosphor fill）；未列出的插件用 package。
_BUILTIN_ICONS = {
    "announcement": "megaphone",
    "exception_catcher": "bug",
    "feature_manager": "toggle-right",
    "friend_request": "user-plus",
    "group_manager": "shield-check",
    "helper": "question",
    "perm_manager": "key",
    "plugin_manager": "puzzle-piece",
    "response_manager": "robot",
    "startup_notify": "bell-ringing",
    "status": "pulse",
    "updater": "arrow-circle-up",
    "webui_account": "browser",
}


help_command = Alconna(
    "帮助",
    Args["index?", int],
    meta=CommandMeta(
        "列出 Entari 已注册命令或查看单条命令详情",
        usage="帮助 [编号]",
        example="$帮助\n$帮助 1",
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


def _first_prefix(value: object) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, Iterable):
        return next(
            (str(item) for item in value if item is not None and str(item)), None
        )
    return None


def _command_prefix(command: object, prefixes: object) -> str:
    return (
        _first_prefix(getattr(command, "prefixes", None))
        or _first_prefix(prefixes)
        or "/"
    )


def _command_meta_text(command: object, field: str) -> str | None:
    value = getattr(getattr(command, "meta", None), field, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _prefix_command_line(line: str, command_name: str, prefix: str) -> str:
    if line.startswith(command_name) and not line.startswith(prefix):
        return f"{prefix}{line}"
    return line


def _format_command_value(
    value: str, field: str, command_name: str, prefix: str
) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if field in {"usage", "example"}:
        lines = [_prefix_command_line(line, command_name, prefix) for line in lines]
    return "\n".join(lines)


def _command_option_lines(command: object) -> list[str]:
    """列出命令里写了 ``help_text`` 的选项；Alconna 内置的 --help 等不展示。"""

    return [
        f"{'/'.join([option.name, *sorted(option.aliases - {option.name})])}："
        f"{option.help_text}"
        for option in getattr(command, "options", ())
        if type(option) is Option and option.help_text != option.dest
    ]


def _format_command_help(
    command: object,
    command_name: str,
    prefixes: object,
    requirement: str | None = None,
) -> str:
    display_name = str(getattr(command, "command", None) or command_name)
    prefix = _command_prefix(command, prefixes)
    fields: list[tuple[str, str]] = []
    for label, field in _COMMAND_HELP_FIELDS:
        value = _command_meta_text(command, field)
        if value is None:
            continue
        formatted = _format_command_value(value, field, display_name, prefix)
        if formatted:
            fields.append((label, formatted))
    if options := _command_option_lines(command):
        fields.append(("选项", "\n".join(options)))
    if requirement:
        fields.append(("权限", requirement))

    lines = [f"{prefix}{display_name}"]
    for index, (label, value) in enumerate(fields):
        is_last = index == len(fields) - 1
        branch = "└" if is_last else "├"
        value_lines = value.splitlines()
        lines.append(f"{branch} {label}：{value_lines[0]}")
        continuation = "  " if is_last else "│ "
        lines.extend(f"{continuation}{line}" for line in value_lines[1:])
    return "\n".join(lines)


def _plugin_help(native_plugin: object) -> str:
    """将插件注册的命令元数据格式化为纯文本帮助。"""

    commands = get_plugin_commands(native_plugin)  # type: ignore[arg-type]
    if not commands:
        return "该插件未注册命令"
    requirements = command_requirements(native_plugin)
    return "\n\n".join(
        _format_command_help(
            command_manager.get_command(command_name),
            command_name,
            prefixes,
            requirement_text(requirements.get(command_name, {})),
        )
        for prefixes, command_name in commands
    )


def _metadata_value(native_plugin: object | None, field: str) -> object | None:
    return getattr(getattr(native_plugin, "metadata", None), field, None)


def _classifiers(native_plugin: object | None) -> tuple[str, ...]:
    value = _metadata_value(native_plugin, "classifier")
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return ()


def _plugin_icon(info: PluginInfo) -> str:
    """内置插件用自带图标，第三方插件用 registry 缓存里的图标，都没有时用默认图标。"""

    if info.source == "builtin" and info.name in _BUILTIN_ICONS:
        return data_uri(_ICON_DIR / f"{_BUILTIN_ICONS[info.name]}.svg")
    if info.source == "external":
        registry = get_registry()
        index = registry.cached()
        entry = index.find(info.name) if index is not None else None
        if entry is not None and (path := registry.icon_path(entry)) is not None:
            return data_uri(path)
    return data_uri(_ICON_DIR / "package.svg")


def _plugin_command_names(native_plugin: object | None) -> list[str]:
    if not isinstance(native_plugin, Plugin):
        return []
    return [
        f"{_command_prefix(command_manager.get_command(name), prefixes)}{name}"
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
            # 第三方插件自带的 classifier 不可信，不能借 host/required 进入系统区。
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
    index: Query[int] = Query("index", None),
    *,
    render_service: RenderService,
):
    context = context_from_session(session)
    value = index.result if index.available else None
    if value is not None:
        return build_help(value)

    group_id = context.channel_id if context.chat_type == "group" else None
    help_data = build_help_data(group_id)
    return await render_or_text(
        render_service, "help.html", help_data, format_help_text(help_data)
    )
