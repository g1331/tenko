from __future__ import annotations

from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import Session, command, plugin
from arclet.entari.command import Match, Query
from arclet.entari.plugin import PluginRole

from tenko.host.accounts import AccountRegistry, account_registry
from tenko.host.guards import group_only, private_only, require
from tenko.host.perm import Permission
from tenko.context import context_from_session
from tenko.plugins._common import list_data, render_or_text
from tenko.host.render import RenderService


plugin.metadata(
    "响应管理查询",
    PluginRole.NORMAL,
    author=["13"],
    description="查询并管理 Tenko 多账号在线、群绑定、响应策略和禁言状态。",
    classifier=["required"],
)


def _mute_label(registry: AccountRegistry, account_id: str, group_id: str) -> str:
    if not registry.is_muted(account_id, group_id):
        return "未禁言"
    until = registry.mute_until(account_id, group_id)
    return "禁言（永久）" if until is None else f"禁言至 {until.isoformat()}"


def _account_label(
    registry: AccountRegistry, account_id: str, group_id: str | None = None
) -> str:
    status = "在线" if registry.is_available(account_id) else "离线"
    if group_id is not None:
        return f"{status}，{_mute_label(registry, account_id, group_id)}"
    return status


def format_group_bots(registry: AccountRegistry, group_id: str | int) -> str:
    """格式化单个群的绑定数和可用数。"""

    normalized_group = str(group_id)
    accounts = registry.bound_accounts_for_group(normalized_group)
    if not accounts:
        return f"没有找到目标群:{normalized_group}"

    available = registry.accounts_for_group(normalized_group)
    return f"群{normalized_group}BOT数: {len(accounts)}；可用数: {len(available)}"


def format_account_groups(registry: AccountRegistry, account_id: str | int) -> str:
    """格式化单个账号及其所有已知群路由，不改变状态。"""

    normalized_account = str(account_id)
    if registry.get(normalized_account) is None:
        return f"没有找到指定BOT：{normalized_account}"

    groups = registry.groups_for_account(normalized_account)
    lines = [
        f"BOT{normalized_account}: {_account_label(registry, normalized_account)}",
        f"已绑定{len(groups)}个群",
    ]
    if not groups:
        lines.append("暂无群绑定")
        return "\n".join(lines)

    for group_id in groups:
        response_type = registry.response_type_for_group(group_id) or "random"
        lines.append(
            f"群{group_id}: {response_type}; "
            f"{_account_label(registry, normalized_account, group_id)}"
        )
    return "\n".join(lines)


def format_online_bots(registry: AccountRegistry) -> str:
    """格式化所有已注册账号的总体可用情况。"""

    accounts = tuple(registry.accounts.items())
    online_count = sum(registry.is_available(account_id) for account_id, _ in accounts)
    lines = [f"在线BOT列表:{online_count}/{len(accounts)}"]
    if not accounts:
        lines.append("当前没有注册BOT")
    return "\n".join(lines)


def build_group_bots_data(
    registry: AccountRegistry, group_id: str | int
) -> dict[str, object] | None:
    """构建当前群的数量汇总，不暴露账号身份。"""

    normalized_group = str(group_id)
    bound = registry.bound_accounts_for_group(normalized_group)
    if not bound:
        return None
    available = registry.accounts_for_group(normalized_group)
    summary = f"群{normalized_group}BOT数: {len(bound)}；可用数: {len(available)}"
    items = (
        {
            "number": 1,
            "name": f"群{normalized_group}",
            "meta": f"BOT 数量：{len(bound)}",
            "detail": f"当前可用：{len(available)}",
            "badge": "当前群",
        },
    )
    return list_data(
        "BOT列表",
        "当前群 · 多账号绑定数量",
        f"群 {normalized_group}",
        summary,
        items,
        f"没有找到目标群:{normalized_group}",
    )


def _account_group_labels(
    registry: AccountRegistry, account_id: str
) -> tuple[str, ...]:
    return tuple(
        f"群{group_id}: {registry.response_type_for_group(group_id) or 'random'}; "
        f"{_account_label(registry, account_id, group_id)}"
        for group_id in registry.groups_for_account(account_id)
    )


def build_account_groups_data(
    registry: AccountRegistry, account_id: str | int
) -> dict[str, object] | None:
    """构建仅供 Master 查看的一张账号路由卡片。"""

    normalized_account = str(account_id)
    if registry.get(normalized_account) is None:
        return None

    groups = registry.groups_for_account(normalized_account)
    items = [
        {
            "number": 1,
            "name": f"BOT{normalized_account}",
            "meta": _account_label(registry, normalized_account),
            "detail": f"已绑定{len(groups)}个群",
            "badge": "BOT",
        }
    ]
    for index, group_id in enumerate(groups, 2):
        response_type = registry.response_type_for_group(group_id) or "random"
        items.append(
            {
                "number": index,
                "name": f"群{group_id}",
                "meta": f"响应策略：{response_type}",
                "detail": _account_label(registry, normalized_account, group_id),
                "badge": "绑定",
            }
        )
    if not groups:
        items[0]["detail"] = "暂无群绑定"
    return list_data(
        "BOT群列表",
        "Master 私聊 · 单个 BOT 群绑定",
        f"BOT {normalized_account}",
        f"BOT{normalized_account} · {_account_label(registry, normalized_account)}",
        tuple(items),
        "暂无群绑定",
    )


def build_all_account_groups_data(
    registry: AccountRegistry, account_ids: tuple[str, ...]
) -> dict[str, object]:
    """构建仅供 Master 查看列表使用的全部跨群路由。"""

    items = []
    for index, account_id in enumerate(account_ids, 1):
        groups = _account_group_labels(registry, account_id)
        items.append(
            {
                "number": index,
                "name": f"BOT{account_id}",
                "meta": _account_label(registry, account_id),
                "detail": "\n".join(groups) if groups else "暂无群绑定",
                "badge": "BOT",
            }
        )
    return list_data(
        "BOT群列表",
        "Master 私聊 · 全部群绑定与状态",
        "MASTER",
        f"共 {len(items)} 个 BOT",
        tuple(items),
        "当前没有注册BOT",
    )


def build_online_bots_data(
    registry: AccountRegistry, group_id: str | int
) -> dict[str, object] | None:
    """构建当前群的在线数量汇总，不暴露账号身份。"""

    normalized_group = str(group_id)
    bound = registry.bound_accounts_for_group(normalized_group)
    if not bound:
        return None
    available = registry.accounts_for_group(normalized_group)
    summary = f"群{normalized_group}在线BOT: {len(available)}/{len(bound)}"
    items = (
        {
            "number": 1,
            "name": f"群{normalized_group}",
            "meta": f"在线 BOT：{len(available)} / {len(bound)}",
            "detail": "仅显示当前群汇总",
            "badge": "当前群",
        },
    )
    return list_data(
        "在线 BOT",
        "当前群 · 在线数量",
        f"群 {normalized_group}",
        summary,
        items,
        f"没有找到目标群:{normalized_group}",
    )


bot_list_command = Alconna(
    "BOT列表",
    meta=CommandMeta(
        "查询当前群的多账号绑定和可用数量",
        usage="BOT列表",
        example="$BOT列表",
        compact=True,
    ),
)


@command.on(bot_list_command)
@group_only()
@require(Permission.BotAdmin)
async def bot_list(session: Session, *, render_service: RenderService):
    context = context_from_session(session)
    result = format_group_bots(account_registry, context.channel_id)
    data = build_group_bots_data(account_registry, context.channel_id)
    if data is None:
        return result
    return await render_or_text(render_service, "list.html", data, result)


bot_group_list_command = Alconna(
    "BOT群列表",
    Args["account_id?", str],
    meta=CommandMeta(
        "查询 BOT 的群绑定和群内禁言状态",
        usage="BOT群列表 [BOT账号]",
        example="$BOT群列表\n$BOT群列表 10001",
        compact=False,
    ),
)


@command.on(bot_group_list_command)
@private_only()
@require(Permission.Master)
async def bot_group_list(
    session: Session,
    account_id: Query[str] = Query("account_id"),
    *,
    render_service: RenderService,
):
    if account_id.available:
        result = format_account_groups(account_registry, account_id.result)
        data = build_account_groups_data(account_registry, account_id.result)
        if data is None:
            return result
        return await render_or_text(render_service, "list.html", data, result)

    if not account_registry.accounts:
        return "当前没有注册BOT"
    account_ids = tuple(account_registry.accounts)
    result = "\n\n".join(
        format_account_groups(account_registry, current_id)
        for current_id in account_ids
    )
    return await render_or_text(
        render_service,
        "list.html",
        build_all_account_groups_data(account_registry, account_ids),
        result,
    )


online_bot_command = Alconna(
    "在线BOT",
    meta=CommandMeta(
        "查询当前群在线 BOT 数量",
        usage="在线BOT",
        example="$在线BOT",
        compact=True,
    ),
)


@command.on(online_bot_command)
@group_only()
@require(Permission.User)
async def online_bot(session: Session, *, render_service: RenderService):
    context = context_from_session(session)
    normalized_group = str(context.channel_id)
    bound = account_registry.bound_accounts_for_group(normalized_group)
    available = account_registry.accounts_for_group(normalized_group)
    if not bound:
        return f"没有找到目标群:{normalized_group}"
    result = f"群{normalized_group}在线BOT: {len(available)}/{len(bound)}"
    return await render_or_text(
        render_service,
        "list.html",
        build_online_bots_data(account_registry, normalized_group),
        result,
    )


response_strategy_command = Alconna(
    "设定响应",
    Args["response_type?", "random|deterministic"],
    meta=CommandMeta(
        "查询或设置当前群响应策略",
        usage="设定响应 [random|deterministic]",
        example="/设定响应\n/设定响应 deterministic",
        compact=False,
    ),
)


@command.on(response_strategy_command)
@group_only()
@require(Permission.GroupAdmin)
async def set_response_strategy(
    session: Session,
    response_type: Query[str] = Query("response_type"),
):
    context = context_from_session(session)
    group_id = str(context.channel_id)
    current = account_registry.response_type_for_group(group_id)
    if current is None:
        return "当前群未绑定可用BOT"
    if not response_type.available:
        return f"当前群响应策略：{current}"

    requested = response_type.result
    if requested == current:
        return "响应模式与当前相同!"

    account_registry.set_response_type(group_id, requested)
    await account_registry.persist_state()
    return f"已将当前群响应策略设为 {requested}"


specified_bot_command = Alconna(
    "指定BOT",
    # 账号 ID 是纯数字；\b 保证整词匹配（Alconna 的正则只做前缀匹配）。
    Args["account_id", r"re:(?:\d+|清除)\b"],
    meta=CommandMeta(
        "指定或清除当前群的 deterministic 响应 BOT",
        usage="指定BOT <账号ID|清除>",
        example="/指定BOT 10001\n/指定BOT 清除",
        compact=False,
    ),
)


@command.on(specified_bot_command)
@group_only()
@require(Permission.GroupAdmin)
async def choose_response_bot(
    session: Session,
    account_id: Match[str],
):
    context = context_from_session(session)
    group_id = str(context.channel_id)
    if account_registry.response_type_for_group(group_id) is None:
        return "当前群未绑定可用BOT"

    requested = account_id.result
    if requested == "清除":
        account_registry.clear_deterministic_account(group_id)
        await account_registry.persist_state()
        return "已清除当前群指定响应BOT，恢复默认选路"
    if account_registry.get(requested) is None:
        return "当前账号列表中没有找到这个 BOT"
    if requested not in {
        str(account.self_id)
        for account in account_registry.bound_accounts_for_group(group_id)
    }:
        return "这个 BOT 尚未绑定当前群"

    account_registry.set_deterministic_account(group_id, requested)
    account_registry.set_response_type(group_id, "deterministic")
    await account_registry.persist_state()
    return f"已成功设定群指定响应BOT为{requested}"
