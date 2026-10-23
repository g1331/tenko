from __future__ import annotations

import asyncio
import base64
import time

import httpx
from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import Session, command, plugin
from arclet.entari.command import Match, Query
from arclet.entari.plugin import PluginRole

from tenko.host.accounts import (
    AccountRegistry,
    account_key,
    account_reference,
    account_registry,
)
from tenko.host.guards import group_only, private_only, require
from tenko.host.perm import Permission
from tenko.context import QQ_PLATFORM, context_from_session, group_reference
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


_AVATAR_TTL = 3600
_AVATAR_BYTES = 512 * 1024
# 头像地址 -> (取到的时间, data URI 或 None)；失败也缓存，避免每次都等超时。
_avatar_cache: dict[str, tuple[float, str | None]] = {}
_STRATEGY = {"random": "随机", "deterministic": "定向"}


def _bot_user(account: object) -> object | None:
    return getattr(getattr(account, "self_info", None), "user", None)


def _avatar_url(account: object) -> str | None:
    url = getattr(_bot_user(account), "avatar", None)
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return url
    platform, account_id = account_key(account)
    if platform == QQ_PLATFORM and account_id.isdigit():
        return f"https://q1.qlogo.cn/g?b=qq&nk={account_id}&s=100"
    return None


async def _fetch_avatar(url: str) -> str | None:
    """下载头像嵌进图片：渲染页面不联网，远程图片加载慢会拖到渲染超时。"""

    now = time.monotonic()
    if (hit := _avatar_cache.get(url)) and now - hit[0] < _AVATAR_TTL:
        return hit[1]
    value = None
    try:
        async with httpx.AsyncClient(timeout=3, follow_redirects=True) as client:
            response = await client.get(url)
        kind = response.headers.get("content-type", "").split(";")[0].strip()
        if (
            response.is_success
            and kind.startswith("image/")
            and len(response.content) <= _AVATAR_BYTES
        ):
            encoded = base64.b64encode(response.content).decode()
            value = f"data:{kind};base64,{encoded}"
    except httpx.HTTPError:
        pass
    _avatar_cache[url] = (now, value)
    return value


async def bot_avatars(accounts) -> dict[str, str | None]:
    urls = {url for account in accounts if (url := _avatar_url(account))}
    found = await asyncio.gather(*(_fetch_avatar(url) for url in urls))
    return dict(zip(urls, found, strict=True))


def build_bots_data(
    registry: AccountRegistry, group_id: str, avatars: dict[str, str | None]
) -> tuple[dict[str, object], str] | None:
    """当前群绑定的 BOT：头像、昵称、账号和状态；返回模板数据和纯文本。"""

    bound = registry.bound_accounts_for_group(group_id)
    if not bound:
        return None
    strategy = registry.response_type_for_group(group_id) or "random"
    leader = registry.deterministic_account_for_group(group_id)
    bots, lines, leader_name = [], [], "未指定的 BOT"
    for account in bound:
        ref = account_reference(account_key(account))
        user = _bot_user(account)
        name = str(getattr(user, "name", None) or ref)
        if not registry.is_available(account):
            tone, state, note = "off", "离线", ""
        elif registry.is_muted(account, group_id):
            until = registry.mute_until(account, group_id)
            tone, state = "warn", "禁言中"
            note = "永久" if until is None else f"至 {until.astimezone():%m-%d %H:%M}"
        else:
            tone, state, note = "ok", "在线", ""
        chips = []
        if ref == leader:
            leader_name = name
        if strategy == "deterministic":
            chips.append(
                {"text": "负责回复", "tone": "accent"}
                if ref == leader
                else {"text": "候补", "tone": ""}
            )
        bots.append(
            {
                "id": account_key(account)[1],
                "name": name,
                "initial": name[:1],
                "avatar": avatars.get(_avatar_url(account) or ""),
                "tone": tone,
                "state": state,
                "state_note": note,
                "chips": chips,
            }
        )
        bot_id = bots[-1]["id"]
        label = bot_id if name == bot_id else f"{name}({bot_id})"
        extra = "".join(f" · {c['text']}" for c in chips)
        lines.append(f"- {label}：{state}{f' {note}' if note else ''}{extra}")
    usable = len(registry.accounts_for_group(group_id))
    muted = sum(bot["tone"] == "warn" for bot in bots)
    if strategy == "deterministic":
        subtitle = f"响应策略 · 定向：由 {leader_name} 回复，其余候补"
    else:
        subtitle = "响应策略 · 随机：可回复的 BOT 轮流响应"
    data = {
        "group": f"群 {group_id}",
        "subtitle": subtitle,
        "stats": [(usable, "可回复"), (len(bound), "已绑定"), (muted, "禁言中")],
        "note": "按绑定顺序",
        "bots": bots,
    }
    text = (
        f"群{group_id} BOT：可回复 {usable}/{len(bound)}（{_STRATEGY[strategy]}）\n"
        + "\n".join(lines)
    )
    return data, text


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


bot_list_command = Alconna(
    "BOT列表",
    meta=CommandMeta(
        "查看本群绑定的 BOT：头像、昵称、账号、在线和禁言状态，以及谁负责回复",
        usage="BOT列表",
        example="/BOT列表",
        compact=True,
    ),
)


@command.on(bot_list_command)
@group_only()
@require(Permission.User)
async def bot_list(session: Session, *, render_service: RenderService):
    group = group_reference(context_from_session(session).group)
    bound = account_registry.bound_accounts_for_group(group)
    built = build_bots_data(account_registry, group, await bot_avatars(bound))
    if built is None:
        return f"本群还没有绑定 BOT：{group}"
    data, text = built
    return await render_or_text(render_service, "bots.html", data, text)


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
    group_id = group_reference(context.group)
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
    group_id = group_reference(context.group)
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
