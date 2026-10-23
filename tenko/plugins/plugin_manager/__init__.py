"""扩展插件的目录、安装、更新、卸载与回滚命令（Master，群聊和私聊都可用）。

新装插件首次启用时多数可直接加载，更新、卸载和回滚在 ``/重启`` 后生效。插件目录里的插件直接安装；不在目录里的来源
安装前要二次确认，确认只接受发起命令的同一个人在同一个会话里的回复。目录每小时刷新
一次，已安装的插件被目录封禁时立即停用，并私聊通知 Master。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import (
    MessageChain,
    MessageCreatedEvent,
    Ready,
    Session,
    command,
    plugin,
    scheduler,
)
from arclet.entari.command import Match, Query
from arclet.entari.plugin import PluginRole
from loguru import logger
from packaging.requirements import Requirement

from tenko.host.actions import action_service
from tenko.host.guards import require
from tenko.host.notify import notify
from tenko.host.perm import Permission
from tenko.host.plugins.docs import plugin_icon_path
from tenko.host.plugins.manager import (
    PluginChange,
    PluginManagerError,
    PluginSource,
    get_plugin_manager,
    parse_source,
)
from tenko.host.plugins.runtime import PluginRuntime, api_incompatibility
from tenko.host.plugins.storage import PluginMigrationError
from tenko.host.plugins.registry import (
    RegistryError,
    RegistryIndex,
    RegistryPlugin,
    compatible,
    get_registry,
    installed_notes,
    registry_updates,
    resume_source,
    source_for,
    why_incompatible,
)
from tenko.context import context_from_session
from tenko.plugins._common import render_or_text
from tenko.host.render import RenderService, data_uri

plugin.metadata(
    "插件管理",
    PluginRole.NORMAL,
    author=["13"],
    description="浏览插件目录，安装、更新、卸载、回滚扩展插件，并恢复插件环境。",
    classifier=["required", "host"],
)

_YES = frozenset({"是", "y", "yes", "确认"})
_TIER = {"official": "官方", "listed": "已收录", "unlisted": "未收录"}
_MARKET_LIMIT = 20
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
        "安装扩展插件：插件目录里的 ID（可带 @版本），或未收录的 PyPI 包名、"
        "带 tag/提交号的 Git URL；重启后生效",
        usage="安装插件 <插件ID[@版本]|包名|Git URL@tag>",
        example="/安装插件 example\n/安装插件 example@0.1.0\n"
        "/安装插件 https://github.com/o/r@v1.0.0",
        compact=False,
    ),
)


async def resolve_source(spec: str) -> tuple[PluginSource, str]:
    """先查插件目录，查不到再按未收录来源解析；返回来源和给 Master 看的说明。"""

    if "://" not in spec and (found := await get_registry().resolve(spec)):
        entry, version = found
        authors = "、".join(entry.authors) or "未署名"
        return source_for(entry, version), (
            f"{entry.name}（{entry.id}）{version.version}，"
            f"{_TIER[entry.tier]}，作者 {authors}"
        )
    parsed = parse_source(spec)
    index = get_registry().cached()
    if index and (reason := index.blocked_reason(None, _dist_of(parsed))):
        raise PluginManagerError("预检", f"该来源已被插件目录封禁：{reason}")
    return parsed, parsed.requirement


def _dist_of(source: PluginSource) -> str | None:
    return Requirement(source.requirement).name if source.kind == "pypi" else None


@command.on(install_command)
@require(Permission.Master)
async def install(session: Session, source: Match[str]):
    try:
        parsed, label = await resolve_source(source.result)
    except PluginManagerError as error:
        return str(error)
    if parsed.trust != "unlisted":
        await session.send(f"正在安装 {label}，请稍候…")
    else:
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
        f"/启用插件 {change.plugin_id} 即可加载（个别插件需要 /重启）。"
    )


update_command = Alconna(
    "更新插件",
    Args["plugin_id?", str]["source?", str],
    meta=CommandMeta(
        "更新插件；已收录的插件更新到目录里最新的可用版本，其余 PyPI 来源更新到兼容的"
        "最新版本，未收录的 Git 来源需给出新的 URL@tag",
        usage="更新插件 [插件ID] [新的 Git URL@tag]",
        example="/更新插件\n/更新插件 example",
        compact=False,
    ),
)


@command.on(update_command)
@require(Permission.Master)
async def update(
    session: Session,
    plugin_id: Query[str] = Query("plugin_id"),
    source: Query[str] = Query("source"),
):
    target = plugin_id.result if plugin_id.available else None
    try:
        parsed = parse_source(source.result) if source.available else None
    except PluginManagerError as error:
        return str(error)
    manager = get_plugin_manager()
    if target and (held := manager.suspended().get(target)):
        # 停用的插件：装上能用的版本即恢复，启用状态沿用停用前。
        await session.send(f"插件<{target}>已停用，正在装上兼容版本以恢复，请稍候…")
        try:
            change = await manager.resume(
                target,
                parsed or await resume_source(target, held),
                actor=_actor(session),
            )
        except PluginManagerError as error:
            return str(error)
        live = await manager.go_live(PluginRuntime(), [change])
        if target not in live:  # 停用前就没启用：装好即可，启用时再加载
            return f"已恢复插件<{target}> {_version_change(change)}（未启用）。"
        why = live[target]
        return f"已恢复插件<{target}> {_version_change(change)}" + (
            f"，但{why}，执行 /重启 后加载。" if why else "，已在运行中生效。"
        )
    await session.send("正在检查并更新，请稍候…")
    try:
        changes = await manager.update(
            target,
            parsed,
            sources={}
            if parsed
            else await registry_updates(get_plugin_manager(), target),
            actor=_actor(session),
        )
    except PluginManagerError as error:
        return str(error)
    if not changes:
        return "已是最新版本"
    live = await manager.go_live(PluginRuntime(), changes)
    lines = [
        f"<{c.plugin_id}> {_version_change(c)}"
        + ("（已生效）" if live.get(c.plugin_id, "") is None else "")
        + (f"（{why}）" if (why := live.get(c.plugin_id)) else "")
        for c in changes
    ]
    pending = any(why for why in live.values())
    tail = "执行 /重启 后生效" if pending else "运行中的插件已换成新版本"
    return "已更新：\n" + "\n".join(lines) + f"\n{tail}；不满意可 /回滚插件。"


uninstall_command = Alconna(
    "卸载插件",
    Args["plugin_id", str]["clear?", "清除数据"],
    meta=CommandMeta(
        "卸载扩展插件；数据目录默认保留，带“清除数据”时一并永久删除",
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
    clear: Query[str] = Query("clear"),
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
        "列出由插件管理器安装的扩展插件及状态",
        usage="插件列表",
        example="/插件列表",
        compact=True,
    ),
)


@command.on(list_command)
@require(Permission.Master)
async def list_plugins(session: Session, *, render_service: RenderService):
    manager = get_plugin_manager()
    rows, suspended = manager.status(), manager.suspended()
    runtime = PluginRuntime()
    local = [i for i in runtime.discover() if i.is_local]
    if not rows and not suspended and not local:
        return "没有已安装的扩展插件"
    index = get_registry().cached()  # 不联网；目录每小时刷新
    lines, items, updates = [], [], 0
    for row in rows:
        state = "已启用" if row.enabled else "未启用"
        restart = "，需要重启" if row.restart_required else ""
        note = _registry_note(index, row) if index else ""
        lines.append(
            f"{row.plugin_id} {row.version}（{_TIER.get(row.trust, row.trust)}，"
            f"{state}{restart}）{note}"
        )
        notes = (
            installed_notes(index, row.plugin_id, row.dist, row.version, row.trust)
            if index
            else {"blocked": None, "yanked": None, "update": None}
        )
        entry = index.find(row.plugin_id) if index else None
        chips = [
            _tier_chip(row.trust),
            {"text": state, "tone": "ok" if row.enabled else ""},
        ]
        if row.restart_required:
            chips.append({"text": "需要重启", "tone": "warn"})
        if notes["update"]:
            updates += 1
            chips.append({"text": f"可更新到 {notes['update']}", "tone": "accent"})
        warning = notes["blocked"] and f"已被目录封禁：{notes['blocked']}"
        warning = warning or (notes["yanked"] and f"此版本已撤回：{notes['yanked']}")
        items.append(
            {
                "icon": _icon(row.plugin_id),
                "name": entry.name if entry else row.plugin_id,
                "id": row.plugin_id,
                "chips": chips,
                "description": entry.description if entry else "",
                "meta": row.dist,
                "note": warning,
                "note_tone": "warn",
                "version": row.version,
                "version_label": "当前版本",
            }
        )
    for plugin_id, held in sorted(suspended.items()):
        record = held["record"]
        why = held.get("error") or held.get("reason") or "与当前版本不兼容"
        lines.append(f"{plugin_id} {record['version']}（已停用：{why}）")
        entry = index.find(plugin_id) if index else None
        items.append(
            {
                "icon": _icon(plugin_id),
                "name": entry.name if entry else plugin_id,
                "id": plugin_id,
                "chips": [
                    _tier_chip(record["trust"]),
                    {"text": "已停用 · 不兼容", "tone": "warn"},
                ],
                "description": entry.description if entry else "",
                "meta": record["dist"],
                "note": f"{why}。/更新插件 {plugin_id} 装上兼容版本即可恢复",
                "note_tone": "warn",
                "version": record["version"],
                "version_label": "停用前版本",
            }
        )
    for info in local:
        state = "已启用" if runtime.is_enabled(info) else "未启用"
        lines.append(f"{info.name}（本地，{state}）")
        items.append(
            {
                "icon": _icon(info.name),
                "name": info.display_name,
                "id": info.name,
                "chips": [
                    {"text": "本地", "tone": ""},
                    {"text": state, "tone": "ok" if state == "已启用" else ""},
                ],
                "description": "",
                "meta": str(info.path),
                "note": f"改了代码后 /重载插件 {info.name} 生效",
                "note_tone": "",
                "version": None,
                "version_label": "",
            }
        )
    try:
        drift = await asyncio.to_thread(manager.drift)
    except PluginManagerError:
        drift = []
    alert = (
        "环境缺少或版本不符：" + "、".join(drift) + "，请 /同步插件环境"
        if drift
        else None
    )
    if alert:
        lines.append(alert)
    data = {
        "eyebrow": "TENKO · PLUGINS",
        "title": "插件列表",
        "subtitle": "由插件管理器安装的扩展插件",
        "stats": [
            (len(rows), "已安装"),
            (sum(r.enabled for r in rows), "已启用"),
            (updates, "可更新"),
        ],
        "section": "扩展插件",
        "section_note": "",
        "items": items,
        "more": "",
        "alert": alert,
        "foot": "/启用插件 /停用插件 <ID> · /更新插件 [ID] · /卸载插件 <ID>"
        + (" · /重载插件 <ID>" if local else ""),
    }
    return await render_or_text(render_service, "plugins.html", data, "\n".join(lines))


def _icon(plugin_id: str) -> str:
    """目录缓存里的图标，没有时用默认图标。"""

    return data_uri(plugin_icon_path(plugin_id, "external"))


def _tier_chip(tier: str) -> dict[str, str]:
    return {
        "text": _TIER.get(tier, tier),
        "tone": "accent" if tier == "official" else "",
    }


enable_command = Alconna(
    "启用插件",
    Args["plugin_id", str],
    meta=CommandMeta(
        "启用已安装的扩展插件；首次启用时多数可直接加载",
        usage="启用插件 <插件ID>",
        example="/启用插件 example",
        compact=False,
    ),
)
disable_command = Alconna(
    "停用插件",
    Args["plugin_id", str],
    meta=CommandMeta(
        "停用扩展插件；已加载的立即停用",
        usage="停用插件 <插件ID>",
        example="/停用插件 example",
        compact=False,
    ),
)


def _registry_note(index: RegistryIndex, row) -> str:
    notes = installed_notes(index, row.plugin_id, row.dist, row.version, row.trust)
    if notes["blocked"]:
        return f" 已被目录封禁：{notes['blocked']}"
    if notes["yanked"]:
        return f" 此版本已撤回：{notes['yanked']}"
    return f" 可更新到 {notes['update']}" if notes["update"] else ""


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
        return f"没有已安装的扩展插件 {plugin_id}，见 /插件列表"
    if enabled and (reason := api_incompatibility(plugin_id)):
        return f"无法启用插件<{plugin_id}>：{reason}"
    index = get_registry().cached()
    row = next(
        (r for r in get_plugin_manager().status() if r.plugin_id == plugin_id), None
    )
    dist = row.dist if row else None
    if enabled and index and (reason := index.blocked_reason(plugin_id, dist)):
        return f"无法启用插件<{plugin_id}>：已被插件目录封禁（{reason}）"
    if not await runtime.set_enabled(info, enabled):
        return f"插件<{plugin_id}>状态切换失败"
    if enabled:
        if reason := await get_plugin_manager().load_now(runtime, info):
            return f"已启用插件<{plugin_id}>，但{reason}，执行 /重启 后加载。"
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


reload_command = Alconna(
    "重载插件",
    Args["plugin_id", str],
    meta=CommandMeta(
        "让本地插件（.tenko/plugins/local/<ID>/）改过的代码立即生效，不用重启",
        usage="重载插件 <插件ID>",
        example="/重载插件 hello",
        compact=False,
    ),
)


@command.on(reload_command)
@require(Permission.Master)
async def reload_local(plugin_id: Match[str]):
    pid = plugin_id.result
    runtime = PluginRuntime()
    info = next((i for i in runtime.discover() if i.name == pid and i.is_local), None)
    if info is None:
        return f"没有本地插件 {pid}，本地插件放在 .tenko/plugins/local/<ID>/ 目录"
    if not runtime.is_enabled(info):
        return f"本地插件<{pid}>未启用，先 /启用插件 {pid}"
    try:
        if runtime.is_loaded(info):
            await runtime.swap(info)
        else:  # 上次加载失败，改好后直接加载
            await runtime.load_external(info)
    except PluginMigrationError as error:
        return f"已重载本地插件<{pid}>，但{error}"
    except Exception as error:  # noqa: BLE001 - 插件代码的任何错误
        cause = error.__cause__ or error.__context__ or error
        return f"重载失败，旧代码继续运行：{type(cause).__name__}: {cause}"
    return f"已重载本地插件<{pid}>"


market_command = Alconna(
    "插件市场",
    Args["keyword?", str],
    meta=CommandMeta(
        "搜索插件目录，官方插件和能装在当前版本上的排在前面",
        usage="插件市场 [关键词]",
        example="/插件市场\n/插件市场 工具",
        compact=False,
    ),
)
detail_command = Alconna(
    "插件详情",
    Args["plugin_id", str],
    meta=CommandMeta(
        "查看插件目录里某个插件的简介、作者、来源和版本兼容性",
        usage="插件详情 <插件ID>",
        example="/插件详情 example",
        compact=False,
    ),
)


async def _index() -> RegistryIndex | str:
    try:
        return await get_registry().load()
    except RegistryError as error:
        return str(error)


@command.on(market_command)
@require(Permission.Master)
async def market(
    keyword: Query[str] = Query("keyword"), *, render_service: RenderService
):
    index = await _index()
    if isinstance(index, str):
        return index
    word = keyword.result if keyword.available else ""
    hits = index.search(word)
    if not hits:
        return "插件目录里没有匹配的插件"
    installed = {r.plugin_id for r in get_plugin_manager().status()}
    lines, items = [], []
    for entry in hits[:_MARKET_LIMIT]:
        latest = compatible(entry)
        tags = "".join(
            f"[{t}]"
            for t, on in (
                (_TIER[entry.tier], entry.tier == "official"),
                ("已安装", entry.id in installed),
                ("不兼容", latest is None),
            )
            if on
        )
        chips = [_tier_chip(entry.tier)]
        chips += [{"text": "已安装", "tone": "ok"}] if entry.id in installed else []
        chips += [{"text": "不兼容", "tone": "warn"}] if latest is None else []
        version = latest.version if latest else ""
        summary = entry.description[:40]
        lines.append(f"{entry.id} {entry.name} {version}{tags} — {summary}".strip())
        items.append(
            {
                "icon": _icon(entry.id),
                "name": entry.name,
                "id": entry.id,
                "chips": chips,
                "description": entry.description,
                "meta": "作者：" + ("、".join(entry.authors) or "未署名"),
                "note": None,
                "note_tone": "",
                "version": version,
                "version_label": "可安装" if latest else "无可用版本",
            }
        )
    more = (
        f"（共 {len(hits)} 个，只显示前 {_MARKET_LIMIT} 个）"
        if len(hits) > _MARKET_LIMIT
        else ""
    )
    text = (
        f"插件目录{more}：\n"
        + "\n".join(lines)
        + "\n/插件详情 <ID> 查看详情，/安装插件 <ID> 安装"
    )
    data = {
        "eyebrow": "TENKO · PLUGIN MARKET",
        "title": "插件市场",
        "subtitle": f"搜索「{word}」" if word else "插件目录 tenko-registry",
        "stats": [
            (len(hits), "个插件"),
            (sum(p.tier == "official" for p in hits), "官方"),
            (sum(compatible(p) is not None for p in hits), "可安装"),
        ],
        "section": "目录插件",
        "section_note": "官方与可安装的排在前面",
        "items": items,
        "more": more.strip("（）"),
        "alert": None,
        "foot": "/插件详情 <ID> 查看详情 · /安装插件 <ID> 安装",
    }
    return await render_or_text(render_service, "plugins.html", data, text)


def format_detail(entry: RegistryPlugin, installed: str | None) -> str:
    source = (
        f"PyPI {entry.package}"
        if entry.source_type == "pypi"
        else f"Git {entry.git_url}"
        + (f"（{entry.subdirectory}）" if entry.subdirectory else "")
    )
    latest = compatible(entry)
    if latest:
        state = f"可安装 {latest.version}"
    elif entry.versions:
        state = f"不能安装：{why_incompatible(entry.versions[0])}"
    else:
        state = "目录里还没有版本"
    lines = [
        f"{entry.name}（{entry.id}）",
        f"等级：{_TIER[entry.tier]}",
        f"作者：{'、'.join(entry.authors) or '未署名'}",
        f"简介：{entry.description or '无'}",
        f"来源：{source}",
    ]
    lines += [f"主页：{entry.homepage}"] if entry.homepage else []
    lines += [f"许可证：{entry.license}"] if entry.license else []
    lines += [f"标签：{'、'.join(entry.tags)}"] if entry.tags else []
    lines.append(f"状态：{state}" + (f"（已安装 {installed}）" if installed else ""))
    if entry.versions:
        lines.append("最近版本：")
        for item in entry.versions[:5]:
            lines.append(f"- {item.version} {why_incompatible(item) or '可安装'}")
    return "\n".join(lines)


def detail_data(entry: RegistryPlugin, installed: str | None) -> dict[str, object]:
    """``plugin_detail.html`` 的渲染数据，与 :func:`format_detail` 内容一致。"""

    source = (
        f"PyPI {entry.package}"
        if entry.source_type == "pypi"
        else f"Git {entry.git_url}"
        + (f"（{entry.subdirectory}）" if entry.subdirectory else "")
    )
    latest = compatible(entry)
    fields = [("作者", "、".join(entry.authors) or "未署名"), ("来源", source)]
    fields += [("主页", entry.homepage)] if entry.homepage else []
    fields += [("许可证", entry.license)] if entry.license else []
    fields += [("标签", "、".join(entry.tags))] if entry.tags else []
    versions = []
    for item in entry.versions[:5]:
        reason = why_incompatible(item)
        versions.append(
            {
                "version": item.version,
                "state": reason or "可安装",
                "tone": "warn" if reason else "ok",
                "installed": item.version == installed,
            }
        )
    if latest:
        state = f"可安装 {latest.version}"
    elif entry.versions:
        state = f"不能安装：{why_incompatible(entry.versions[0])}"
    else:
        state = "目录里还没有版本"
    return {
        "item": {
            "id": entry.id,
            "name": entry.name,
            "icon": _icon(entry.id),
            "tier_label": _TIER[entry.tier],
            "description": entry.description,
            "latest": latest.version if latest else None,
            "installed": installed,
            "state": state,
        },
        "fields": fields,
        "versions": versions,
        "foot": (
            f"/安装插件 {entry.id} 安装"
            if not installed
            else "已是可安装的最新版本"
            if latest is None or installed == latest.version
            else f"/更新插件 {entry.id} 更新到 {latest.version}"
        ),
    }


@command.on(detail_command)
@require(Permission.Master)
async def detail(plugin_id: Match[str], *, render_service: RenderService):
    index = await _index()
    if isinstance(index, str):
        return index
    entry = index.find(plugin_id.result)
    if entry is None:
        return f"插件目录里没有 {plugin_id.result}"
    if reason := index.blocked_reason(entry.id, entry.dist):
        return f"插件 {entry.id} 已被目录封禁：{reason}"
    row = next(
        (r for r in get_plugin_manager().status() if r.plugin_id == entry.id), None
    )
    installed = row.version if row else None
    return await render_or_text(
        render_service,
        "plugin_detail.html",
        detail_data(entry, installed),
        format_detail(entry, installed),
    )


async def enforce_blocklist(index: RegistryIndex) -> list[str]:
    """停用被目录封禁、仍在启用的插件（不卸载，数据保留），返回停用说明。"""

    runtime = PluginRuntime()
    infos = {i.name: i for i in runtime.discover() if i.source == "external"}
    rows = {r.plugin_id: r for r in get_plugin_manager().status()}
    enabled = set(runtime.read_state()["enabled"])
    stopped = []
    for plugin_id in sorted(enabled & set(infos)):
        row = rows.get(plugin_id)
        reason = index.blocked_reason(plugin_id, row.dist if row else None)
        if reason and await runtime.set_enabled(infos[plugin_id], False):
            stopped.append(f"<{plugin_id}>：{reason}")
    return stopped


async def notify_masters(text: str) -> None:
    """按通知设置告知 Master。"""

    await notify(text, actions=action_service)


async def refresh_registry() -> None:
    try:
        index = await get_registry().load(refresh=True)
    except RegistryError as error:
        logger.warning("{}", error)
        return
    if stopped := await enforce_blocklist(index):
        text = (
            "以下扩展插件已被插件目录封禁，已自动停用（未卸载，数据保留）：\n"
            + "\n".join(stopped)
            + "\n确认后可用 /卸载插件 移除。"
        )
        logger.warning(text)
        await notify_masters(text)


@plugin.listen(Ready)
async def on_ready():
    await refresh_registry()


@scheduler.schedule(lambda: timedelta(hours=1), label="tenko-plugin-registry")
async def scheduled_refresh():
    await refresh_registry()
