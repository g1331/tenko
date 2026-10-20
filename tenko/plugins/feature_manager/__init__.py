from __future__ import annotations

from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import Session, command, plugin
from arclet.entari.command import Match
from arclet.entari.plugin import PluginRole

from tenko.host.features import feature_service
from tenko.host.guards import group_only, require
from tenko.host.perm import Permission, PermissionChecker
from tenko.host.plugin_storage import PLUGIN_DATA_ROOT, clear_plugin_data
from tenko.host.plugins import _PLUGIN_ID, PluginInfo, PluginRuntime
from tenko.plugins._common import context_from_session


plugin.metadata(
    "功能开关",
    PluginRole.NORMAL,
    author=["13"],
    version="0.1.0",
    description="管理群内插件功能开关。",
    classifier=["required", "host"],
)


permission_checker = PermissionChecker()
plugin_runtime = PluginRuntime()
_PROTECTED_PLUGIN_NAMES = frozenset({"feature_manager", "功能开关"})


def resolve_plugin(value: str) -> PluginInfo | None:
    """按帮助编号、目录名、导入名或元数据名定位插件。"""

    normalized = str(value)
    infos = plugin_runtime.discover()
    if normalized.isdigit():
        # 编号与 /帮助 一致，只数已加载的插件（未启用的第三方插件不在其中）。
        loaded = [info for info in infos if info.native_plugin is not None]
        index = int(normalized)
        if 1 <= index <= len(loaded):
            return loaded[index - 1]
        return None
    for info in infos:
        if normalized in info.lookup_names or normalized == info.display_name:
            return info
    return None


def _is_protected(info: PluginInfo) -> bool:
    if info.name in _PROTECTED_PLUGIN_NAMES:
        return True
    native = info.native_plugin
    classifiers = getattr(getattr(native, "metadata", None), "classifier", ())
    # ``required`` 只表示插件应随宿主加载并在帮助中归入系统区域；普通
    # 功能插件仍然需要支持群级开关。``host`` 才是不能由群管理员关闭的
    # 控制平面标记，只对内置插件有效，第三方插件不能自封受保护。
    return info.source == "builtin" and "host" in classifiers


def _is_global_feature(info: PluginInfo) -> bool:
    native = info.native_plugin
    metadata = getattr(native, "metadata", None)
    return getattr(metadata, "feature_scope", "group") == "global"


async def _change(session: Session, operation: str, feature: Match[str]):
    context = context_from_session(session)
    if not feature_service.ready:
        return "功能状态暂不可用，请稍后再试"

    info = resolve_plugin(feature.result)
    if info is None:
        return "编号不在运行插件范围内~"
    target_name = info.display_name
    if _is_protected(info):
        return f"无法操作必须插件<{target_name}>"

    is_global = _is_global_feature(info)
    # 全局功能影响所有群，只能由 BotAdmin 切换；是否全局取决于目标插件，所以
    # 这里不能用装饰器。
    if is_global and not await permission_checker.require_perm(
        context, Permission.BotAdmin
    ):
        return "权限不足"
    enabled = (
        feature_service.is_enabled(info.name)
        if is_global
        else feature_service.is_enabled(info.name, context.channel_id)
    )
    requested_enabled = operation == "开启"
    if enabled == requested_enabled:
        return f"功能{target_name}已处于{operation}状态请不要重复{operation}!"
    if is_global:
        feature_service.set_global_enabled(info.name, requested_enabled)
    else:
        feature_service.set_enabled(info.name, context.channel_id, requested_enabled)
    await feature_service.persist_state()
    return f"功能<{target_name}>{'已开启' if requested_enabled else '已关闭'}~"


enable_command = Alconna(
    "开启",
    Args["feature", str],
    meta=CommandMeta(
        "开启当前群的插件功能",
        usage="开启 <插件编号或名称>",
        example="/开启 3\n/开启 公告",
        compact=False,
    ),
)


@command.on(enable_command)
@group_only()
@require(Permission.GroupAdmin)
async def enable(session: Session, feature: Match[str]):
    return await _change(session, "开启", feature)


disable_command = Alconna(
    "关闭",
    Args["feature", str],
    meta=CommandMeta(
        "关闭当前群的插件功能",
        usage="关闭 <插件编号或名称>",
        example="/关闭 3\n/关闭 公告",
        compact=False,
    ),
)


@command.on(disable_command)
@group_only()
@require(Permission.GroupAdmin)
async def disable(session: Session, feature: Match[str]):
    return await _change(session, "关闭", feature)


clear_data_command = Alconna(
    "清除插件数据",
    Args["plugin_id", str],
    meta=CommandMeta(
        "删除第三方插件的数据目录（JSON 存储、数据库和备份），不可恢复",
        usage="清除插件数据 <插件ID>",
        example="/清除插件数据 example",
        compact=False,
    ),
)


@command.on(clear_data_command)
@require(Permission.Master)
async def clear_data(session: Session, plugin_id: Match[str]):
    info = next(
        (i for i in plugin_runtime.discover() if i.name == plugin_id.result), None
    )
    name = plugin_id.result
    # 已卸载的插件不再有入口，但它保留的数据仍可以清除。
    uninstalled = (
        info is None
        and _PLUGIN_ID.fullmatch(name) is not None
        and (PLUGIN_DATA_ROOT / name).is_dir()
    )
    if not uninstalled and (info is None or info.source != "external"):
        return "只能清除已安装的第三方插件的数据"
    if info is not None and info.native_plugin is not None:
        # 已加载的插件仍持有数据库连接；先停用并重启，让它在本次运行里不再加载。
        return f"插件<{info.name}>正在运行，请先在 WebUI 停用并重启 Tenko 后再清除"
    await session.send(
        f"将永久删除插件<{name}>的全部数据（含数据库和备份），确定吗？（是/否）"
    )
    reply = await session.prompt(timeout=30, timeout_message="回复等待超时，已取消")
    if str(reply or "").strip().lower() not in {"是", "y", "yes", "确认"}:
        return "已取消"
    removed = clear_plugin_data(name)
    return f"已清除插件<{name}>的数据" if removed else f"插件<{name}>没有数据"
