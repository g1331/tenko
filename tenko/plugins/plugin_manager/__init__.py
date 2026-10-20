"""第三方插件的安装、更新、卸载与回滚命令（Master，群聊和私聊都可用）。

操作都在重启后才生效，复用 ``/重启``。来源不在插件目录里的（目前所有来源都是）
安装前要二次确认；确认只接受发起命令的同一个人在同一个会话里的回复。
"""

from __future__ import annotations

import asyncio

from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import MessageChain, MessageCreatedEvent, Session, command, plugin
from arclet.entari.command import Match, Query
from arclet.entari.plugin import PluginRole

from tenko.host.guards import require
from tenko.host.perm import Permission
from tenko.host.plugin_manager import (
    PluginChange,
    PluginManagerError,
    get_plugin_manager,
    parse_source,
)
from tenko.host.plugins import PluginRuntime, api_incompatibility
from tenko.plugins._common import context_from_session

plugin.metadata(
    "插件管理",
    PluginRole.NORMAL,
    author=["Tenko"],
    version="0.1.0",
    description="安装、更新、卸载、回滚第三方插件，并恢复插件环境。",
    classifier=["required", "host"],
)

_YES = frozenset({"是", "y", "yes", "确认"})
_UNLISTED_WARNING = (
    "来源未经 Tenko 检查：插件与 bot 拥有相同的权限（账号、数据库、文件系统）。"
    "Git 来源在安装时还会运行仓库里的构建脚本。"
)


def same_conversation(origin, event) -> bool:
    """回复是否来自发起命令的同一个人、同一个会话（私聊回复不能确认群里的操作）。"""

    return bool(
        origin.user
        and event.user
        and origin.user.id == event.user.id
        and origin.channel
        and event.channel
        and origin.channel.id == event.channel.id
    )


async def ask(session: Session, text: str, accepted: frozenset[str]) -> bool:
    async def waiter(content: MessageChain, replied: Session[MessageCreatedEvent]):
        if same_conversation(session.event, replied.event):
            return content

    waiter.__annotations__ = {"content": MessageChain, "replied": type(session)}
    reply = await session.prompt(
        waiter, text, timeout=60, timeout_message="回复等待超时，已取消"
    )
    return reply is not None and str(reply).strip().lower() in accepted


def _version_change(change: PluginChange) -> str:
    return f"{change.old_version or '无'} → {change.new_version or '无'}"


def _actor(session: Session) -> str:
    return context_from_session(session).user_id


install_command = Alconna(
    "安装插件",
    Args["source", str],
    meta=CommandMeta(
        "安装第三方插件（PyPI 包名或带 tag/提交号的 Git URL），重启后生效",
        usage="安装插件 <包名[版本范围]|Git URL@tag>",
        example="/安装插件 tenko-plugin-example\n/安装插件 https://github.com/o/r@v1.0.0",
        compact=False,
    ),
)


@command.on(install_command)
@require(Permission.Master)
async def install(session: Session, source: Match[str]):
    try:
        parsed = parse_source(source.result)
    except PluginManagerError as error:
        return str(error)
    if parsed.trust == "unlisted":
        if not await ask(
            session,
            f"将从未收录的来源安装：{parsed.requirement}\n{_UNLISTED_WARNING}\n"
            "确定继续吗？（是/否）",
            _YES,
        ):
            return "已取消"
        if not await ask(
            session,
            "再确认一次：回复「确认安装」开始安装，其他回复取消。",
            frozenset({"确认安装"}),
        ):
            return "已取消"
    await session.send("正在解析依赖并安装，请稍候…")
    try:
        change = await get_plugin_manager().install(parsed, actor=_actor(session))
    except PluginManagerError as error:
        return str(error)
    return (
        f"已安装插件<{change.plugin_id}> {change.new_version}（默认未启用）。\n"
        f"/启用插件 {change.plugin_id} 后执行 /重启 生效。"
    )


update_command = Alconna(
    "更新插件",
    Args["plugin_id?", str]["source?", str],
    meta=CommandMeta(
        "更新插件；不带 ID 时更新全部 PyPI 来源的插件，Git 来源需给出新的 URL@tag",
        usage="更新插件 [插件ID] [新的 Git URL@tag]",
        example="/更新插件\n/更新插件 example",
        compact=False,
    ),
)


@command.on(update_command)
@require(Permission.Master)
async def update(
    session: Session,
    plugin_id: Query[str] = Query("plugin_id", None),
    source: Query[str] = Query("source", None),
):
    try:
        parsed = parse_source(source.result) if source.available else None
    except PluginManagerError as error:
        return str(error)
    await session.send("正在检查并更新，请稍候…")
    try:
        changes = await get_plugin_manager().update(
            plugin_id.result if plugin_id.available else None,
            parsed,
            actor=_actor(session),
        )
    except PluginManagerError as error:
        return str(error)
    if not changes:
        return "已是最新版本"
    lines = [f"<{c.plugin_id}> {_version_change(c)}" for c in changes]
    return "已更新：\n" + "\n".join(lines) + "\n执行 /重启 后生效；不满意可 /回滚插件。"


uninstall_command = Alconna(
    "卸载插件",
    Args["plugin_id", str]["clear?", "清除数据"],
    meta=CommandMeta(
        "卸载第三方插件；数据目录默认保留，带“清除数据”时一并永久删除",
        usage="卸载插件 <插件ID> [清除数据]",
        example="/卸载插件 example\n/卸载插件 example 清除数据",
        compact=False,
    ),
)


@command.on(uninstall_command)
@require(Permission.Master)
async def uninstall(
    session: Session,
    plugin_id: Match[str],
    clear: Query[str] = Query("clear", None),
):
    manager = get_plugin_manager()
    clear_data = clear.available
    row = next((r for r in manager.status() if r.plugin_id == plugin_id.result), None)
    if row is None:
        return f"{plugin_id.result} 不是由插件管理器安装的"
    if clear_data and row.loaded:
        return (
            f"插件<{row.plugin_id}>正在运行，请先 /停用插件 并 /重启，"
            "再带“清除数据”卸载"
        )
    if not await ask(
        session,
        f"将卸载插件<{row.plugin_id}>（{row.dist} {row.version}）。"
        + ("数据将被永久删除。" if clear_data else "数据目录会保留，重装后可继续使用。")
        + "确定吗？（是/否）",
        _YES,
    ):
        return "已取消"
    if clear_data and not await ask(
        session,
        "「清除数据」会永久删除插件的 JSON 存储、数据库和备份，无法恢复。"
        "回复「确认清除」继续，其他回复取消。",
        frozenset({"确认清除"}),
    ):
        return "已取消"
    try:
        change = await manager.uninstall(
            row.plugin_id, clear_data=clear_data, actor=_actor(session)
        )
    except PluginManagerError as error:
        return str(error)
    note = "，数据已清除" if change.data_cleared else "，数据已保留"
    return f"已卸载插件<{change.plugin_id}>{note}。执行 /重启 后彻底生效。"


rollback_command = Alconna(
    "回滚插件",
    meta=CommandMeta(
        "撤销最近一次插件安装、更新或卸载（版本、依赖和插件数据库一起恢复）",
        usage="回滚插件",
        example="/回滚插件",
        compact=True,
    ),
)


@command.on(rollback_command)
@require(Permission.Master)
async def rollback(session: Session):
    try:
        changes = await get_plugin_manager().rollback(actor=_actor(session))
    except PluginManagerError as error:
        return str(error)
    lines = [f"<{c.plugin_id}> {_version_change(c)}" for c in changes]
    return "已回滚：\n" + "\n".join(lines) + "\n执行 /重启 后生效。"


sync_command = Alconna(
    "同步插件环境",
    meta=CommandMeta(
        "按插件锁重装缺失的插件包（例如执行 uv sync 之后）",
        usage="同步插件环境",
        example="/同步插件环境",
        compact=True,
    ),
)


@command.on(sync_command)
@require(Permission.Master)
async def sync_environment(session: Session):
    await session.send("正在按插件锁恢复环境，请稍候…")
    try:
        repaired = await get_plugin_manager().sync_env(actor=_actor(session))
    except PluginManagerError as error:
        return str(error)
    if not repaired:
        return "插件环境与插件锁一致，无需恢复"
    return "已重装：" + "、".join(repaired) + "\n执行 /重启 后生效。"


list_command = Alconna(
    "插件列表",
    meta=CommandMeta(
        "列出由插件管理器安装的第三方插件及状态",
        usage="插件列表",
        example="/插件列表",
        compact=True,
    ),
)


@command.on(list_command)
@require(Permission.Master)
async def list_plugins(session: Session):
    manager = get_plugin_manager()
    rows = manager.status()
    if not rows:
        return "没有已安装的第三方插件"
    lines = []
    for row in rows:
        state = "已启用" if row.enabled else "未启用"
        restart = "，需要重启" if row.restart_required else ""
        lines.append(f"{row.plugin_id} {row.version}（{row.source}，{state}{restart}）")
    try:
        drift = await asyncio.to_thread(manager.drift)
    except PluginManagerError:
        drift = []
    if drift:
        lines.append("环境缺少或版本不符：" + "、".join(drift) + "，请 /同步插件环境")
    return "\n".join(lines)


enable_command = Alconna(
    "启用插件",
    Args["plugin_id", str],
    meta=CommandMeta(
        "启用已安装的第三方插件；首次启用需要重启",
        usage="启用插件 <插件ID>",
        example="/启用插件 example",
        compact=False,
    ),
)
disable_command = Alconna(
    "停用插件",
    Args["plugin_id", str],
    meta=CommandMeta(
        "停用第三方插件；已加载的立即停用",
        usage="停用插件 <插件ID>",
        example="/停用插件 example",
        compact=False,
    ),
)


async def _toggle(plugin_id: str, enabled: bool) -> str:
    runtime = PluginRuntime()
    info = next(
        (
            i
            for i in runtime.discover()
            if i.name == plugin_id and i.source == "external"
        ),
        None,
    )
    if info is None:
        return f"没有已安装的第三方插件 {plugin_id}，见 /插件列表"
    if enabled and (reason := api_incompatibility(plugin_id)):
        return f"无法启用插件<{plugin_id}>：{reason}"
    if not await runtime.set_enabled(info, enabled):
        return f"插件<{plugin_id}>状态切换失败"
    if enabled:
        if runtime.restart_required(info):
            return f"已启用插件<{plugin_id}>，执行 /重启 后加载。"
        return f"已启用插件<{plugin_id}>"
    return f"已停用插件<{plugin_id}>"


@command.on(enable_command)
@require(Permission.Master)
async def enable(plugin_id: Match[str]):
    return await _toggle(plugin_id.result, True)


@command.on(disable_command)
@require(Permission.Master)
async def disable(plugin_id: Match[str]):
    return await _toggle(plugin_id.result, False)
