"""插件的说明资料：命令帮助字段和图标，/帮助 和 WebUI 插件详情共用。

这里不能放 Entari 插件模块里的东西：WebUI 在插件体系之外导入它。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from arclet.alconna import Option, command_manager
from arclet.entari.plugin import get_plugin_commands

from tenko.host.guards import command_requirements, requirement_text
from tenko.host.plugins.registry import Registry, get_registry

_COMMAND_HELP_FIELDS = (("说明", "description"), ("用法", "usage"), ("示例", "example"))
ICON_DIR = Path(__file__).resolve().parents[2] / "templates" / "assets" / "icons"
# 内置插件的菜单图标（Phosphor fill）；未列出的插件用 package。
BUILTIN_ICONS = {
    "announcement": "megaphone",
    "exception_catcher": "bug",
    "feature_manager": "toggle-right",
    "friend_request": "user-plus",
    "group_manager": "shield-check",
    "helper": "question",
    "notify": "bell-ringing",
    "perm_manager": "key",
    "plugin_manager": "puzzle-piece",
    "response_manager": "robot",
    "startup_notify": "bell-ringing",
    "status": "pulse",
    "updater": "arrow-circle-up",
    "webui_account": "browser",
}


def plugin_icon_path(name: str, source: str, registry: Registry | None = None) -> Path:
    """内置插件用自带图标，第三方插件用 registry 缓存里的图标，都没有时用默认图标。

    内置图标是单色 SVG（``fill="currentColor"``），registry 图标是 PNG。
    """

    if source == "builtin" and name in BUILTIN_ICONS:
        return ICON_DIR / f"{BUILTIN_ICONS[name]}.svg"
    if source == "external":
        registry = registry or get_registry()
        index = registry.cached()
        entry = index.find(name) if index is not None else None
        if entry is not None and (path := registry.icon_path(entry)) is not None:
            return path
    return ICON_DIR / "package.svg"


def _first_prefix(value: object) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, Iterable):
        return next(
            (str(item) for item in value if item is not None and str(item)), None
        )
    return None


def command_prefix(command: object, prefixes: object) -> str:
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


def command_help_fields(
    command: object,
    command_name: str,
    prefixes: object,
    requirement: str | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """返回命令的显示名（带前缀）和说明、用法等字段，图片和文本两种输出共用。"""

    display_name = str(getattr(command, "command", None) or command_name)
    prefix = command_prefix(command, prefixes)
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
    return f"{prefix}{display_name}", fields


def format_fields(title: str, fields: list[tuple[str, str]]) -> str:
    lines = [title]
    for index, (label, value) in enumerate(fields):
        is_last = index == len(fields) - 1
        branch = "└" if is_last else "├"
        value_lines = value.splitlines()
        lines.append(f"{branch} {label}：{value_lines[0]}")
        continuation = "  " if is_last else "│ "
        lines.extend(f"{continuation}{line}" for line in value_lines[1:])
    return "\n".join(lines)


def format_command_help(
    command: object,
    command_name: str,
    prefixes: object,
    requirement: str | None = None,
) -> str:
    return format_fields(
        *command_help_fields(command, command_name, prefixes, requirement)
    )


def plugin_command_help(
    native_plugin: object,
) -> list[tuple[str, list[tuple[str, str]]]]:
    """插件每条命令的 (显示名, 字段)；按注册顺序排列。"""

    requirements = command_requirements(native_plugin)
    return [
        command_help_fields(
            command_manager.get_command(command_name),
            command_name,
            prefixes,
            requirement_text(requirements.get(command_name, {})),
        )
        for prefixes, command_name in get_plugin_commands(native_plugin)  # type: ignore[arg-type]
    ]
