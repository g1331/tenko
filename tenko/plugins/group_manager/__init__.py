from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from arclet.alconna import Alconna, Args, CommandMeta, MultiVar, Option
from arclet.entari import Session, command, plugin
from arclet.entari.command import Match, Query
from arclet.entari.event.base import GuildRequestEvent
from arclet.entari.plugin import PluginRole, collect_disposes
from loguru import logger
from satori import User
from satori.element import At, Author, select

from tenko.host.actions import action_service
from tenko.host.accounts import account_registry
from tenko.context import QQ_PLATFORM, MessageContext
from tenko.host.guards import group_only, private_only, require
from tenko.host.inbox import InboxItem, inbox
from tenko.host.perm import Permission, PermissionChecker, permission_store
from tenko.host.ratelimit import rate_limit_service
from tenko.plugins._common import list_data, render_or_text
from tenko.host.action_errors import action_errors, report_action_error
from tenko.context import context_from_session
from tenko.host.notify import notify
from tenko.host.render import RenderService


plugin.metadata(
    "群管理",
    PluginRole.NORMAL,
    author=["13"],
    description="查询群设置并通过宿主动作服务执行群管理操作。",
    classifier=["required"],
)


permission_checker = PermissionChecker()
_database_warning_groups: set[str] = set()


INVITE_TIMEOUT_SECONDS = 60 * 60


@dataclass(slots=True)
class PendingInvite:
    """当前进程中等待管理员决定的 Satori 群邀请。"""

    request_id: str
    account: Any
    group_id: str
    group_name: str
    inviter_id: str | None
    inviter_name: str
    comment: str
    created_at: float


pending_invites: dict[str, PendingInvite] = {}
invite_expiry_tasks: dict[str, asyncio.Task[None]] = {}


def _dispose_invite_state() -> None:
    for task in tuple(invite_expiry_tasks.values()):
        task.cancel()
    invite_expiry_tasks.clear()
    for pending in tuple(pending_invites.values()):
        _forget_invite(pending)


collect_disposes(_dispose_invite_state)


def _normalize_id(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _event_inviter_id(event: GuildRequestEvent) -> str | None:
    inviter_id = _normalize_id(event.user.id) if event.user else None
    if inviter_id is not None:
        return inviter_id

    origin = getattr(event, "_origin", None)
    data = getattr(origin, "_data", None)
    if isinstance(data, Mapping):
        return _normalize_id(data.get("user_id", data.get("inviter_id")))
    return None


def _event_inviter_name(event: GuildRequestEvent, inviter_id: str | None) -> str:
    name = _normalize_id(event.user.name) if event.user else None
    if name is not None:
        return name
    if inviter_id is not None:
        return inviter_id
    return "未知邀请人"


def _request_context(event: GuildRequestEvent, inviter_id: str | None = None):
    origin = getattr(event, "_origin", None)
    if origin is None:
        return None
    if inviter_id is not None:
        origin_user = getattr(origin, "user", None)
        if _normalize_id(getattr(origin_user, "id", None)) != inviter_id:
            try:
                # Satori 的 GuildRequestEvent 以标准 user 字段承载邀请人；
                # 某些适配器只在原始 _data.user_id 中保留该字段，补齐标准
                # 对象后仍使用同一套 MessageContext/PermissionChecker。
                origin = replace(origin, user=User(inviter_id))
            except (TypeError, ValueError):
                return None
    try:
        return MessageContext.from_event(origin)
    except (AttributeError, TypeError, ValueError):
        return None


async def _is_privileged_inviter(
    event: GuildRequestEvent, inviter_id: str | None
) -> bool:
    if inviter_id is None:
        logger.info(
            "Group invite {} has no inviter ID; keeping it in the pending flow",
            getattr(getattr(event, "message", None), "id", None),
        )
        return False
    context = _request_context(event, inviter_id)
    if context is None:
        return False
    try:
        return await permission_checker.require_perm(context, Permission.BotAdmin)
    except Exception:
        # 权限数据库属于旧系统的可选读取边界；读取失败时按普通邀请
        # 进入待审队列，不能因为权限查询异常自动放行邀请。
        logger.exception("Could not check group invite inviter permission")
        return False


def _ensure_action_account(pending: PendingInvite) -> None:
    """让提前到达的邀请事件也能复用已注册账号的 ActionService。"""

    registry = getattr(action_service, "registry", None)
    account_id = _normalize_id(getattr(pending.account, "self_id", None))
    if registry is None or account_id is None:
        return
    # 事件携带的账号句柄可能是重连后的新对象；register 会保留已有群
    # 绑定，同时更新 ActionService 后续真正使用的协议句柄。
    registry.register(pending.account)


async def _send_reviewer_notice(pending: PendingInvite, text: str) -> None:
    await notify(
        text,
        preferred=pending.account,
        actions=action_service,
        permission_checker=permission_checker,
    )


async def _apply_invite_decision(
    pending: PendingInvite,
    approved: bool,
    comment: str,
    *,
    context: MessageContext | None = None,
    system: bool = False,
) -> None:
    _ensure_action_account(pending)
    await action_service.approve_group_invite(
        pending.account,
        pending.request_id,
        approved,
        comment,
        context=context,
        permission_checker=permission_checker,
        system=system,
    )


def _cancel_invite_expiry(request_id: str) -> None:
    task = invite_expiry_tasks.pop(request_id, None)
    if task is not None and not task.done():
        task.cancel()


def _forget_invite(pending: PendingInvite) -> None:
    pending_invites.pop(pending.request_id, None)
    _cancel_invite_expiry(pending.request_id)
    inbox.discard(
        "group", str(getattr(pending.account, "self_id", "")), pending.request_id
    )


async def _expire_invite(request_id: str) -> None:
    try:
        await asyncio.sleep(INVITE_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return
    pending = pending_invites.get(request_id)
    if pending is None:
        return
    try:
        await _apply_invite_decision(
            pending,
            False,
            "拒绝了你的入群邀请!",
            system=True,
        )
    except Exception:
        logger.exception("Failed to auto-reject expired invite {}", request_id)
        invite_expiry_tasks.pop(request_id, None)
        return
    invite_expiry_tasks.pop(request_id, None)
    _forget_invite(pending)
    await _send_reviewer_notice(
        pending,
        f"入群邀请 {request_id} 已超过 1 小时，已自动拒绝。",
    )


def _invite_notice(pending: PendingInvite) -> str:
    group = f"{pending.group_name}({pending.group_id})"
    inviter = (
        f"{pending.inviter_name}({pending.inviter_id})"
        if pending.inviter_id is not None
        else f"{pending.inviter_name}（ID 不可得）"
    )
    inviter_warning = (
        "\n邀请人 ID 不可得，未执行自动同意检查，将按待审流程处理。"
        if pending.inviter_id is None
        else ""
    )
    return (
        f"群邀请待审\n收到邀请的账号: {pending.account.self_id}\n"
        f"群: {group}\n邀请人: {inviter}\n"
        f"请求 ID: {pending.request_id}\n"
        "审批权限：目标群 BotAdmin/Master；GroupAdmin 无权审批。"
        f"{inviter_warning}\n"
        f"请在 1 小时内使用 /同意邀请 {pending.request_id} 或 "
        f"/拒绝邀请 {pending.request_id} [理由] 处理。"
    )


@plugin.listen(GuildRequestEvent)
async def invited_event(event: GuildRequestEvent):
    """迁移旧版群邀请处理：管理员邀请自动同意，其余进入待审队列。"""

    request_id = _normalize_id(event.message.id)
    group_id = _normalize_id(event.guild.id)
    if request_id is None or group_id is None:
        logger.warning("Ignore malformed guild request event: {}", event)
        return

    inviter_id = _event_inviter_id(event)
    pending = PendingInvite(
        request_id=request_id,
        account=event.account,
        group_id=group_id,
        group_name=str(event.guild.name or group_id),
        inviter_id=inviter_id,
        inviter_name=_event_inviter_name(event, inviter_id),
        comment=str(event.message.content or ""),
        created_at=asyncio.get_running_loop().time(),
    )
    _ensure_action_account(pending)
    if await _is_privileged_inviter(event, inviter_id):
        try:
            await _apply_invite_decision(
                pending,
                True,
                "已同意您的邀请~",
                context=_request_context(event, inviter_id),
            )
        except Exception:
            # 审批 action 失败时不能丢掉平台仍在等待的请求；保留到待审队列，
            # 同时把 action 错误记录下来，交给管理员稍后重试。
            logger.exception(
                "Failed to automatically approve privileged group invite: "
                "request={} group={}",
                pending.request_id,
                pending.group_id,
            )
        else:
            logger.info(
                "Automatically approved privileged group invite: request={} group={} "
                "inviter={}",
                pending.request_id,
                pending.group_id,
                pending.inviter_id,
            )
            return

    pending_invites[pending.request_id] = pending
    _cancel_invite_expiry(pending.request_id)
    invite_expiry_tasks[pending.request_id] = asyncio.create_task(
        _expire_invite(pending.request_id)
    )
    inbox.add(
        InboxItem(
            kind="group",
            request_id=pending.request_id,
            account=pending.account,
            user_id=pending.inviter_id,
            user_name=pending.inviter_name,
            comment=pending.comment,
            decide=lambda approved, comment: _decide_from_panel(
                pending, approved, comment
            ),
            group_id=pending.group_id,
            group_name=pending.group_name,
            expires_at=datetime.now(UTC) + timedelta(seconds=INVITE_TIMEOUT_SECONDS),
        )
    )
    await _send_reviewer_notice(pending, _invite_notice(pending))
    logger.info(
        "Recorded pending group invite: request={} group={} inviter={}",
        pending.request_id,
        pending.group_id,
        pending.inviter_id,
    )


async def _decide_from_panel(
    pending: PendingInvite, approved: bool, comment: str
) -> None:
    # 面板只对已登录的 Master 开放，权限在 WebUI 层校验。
    default = "已同意您的邀请~" if approved else "BOT拒绝了你的入群邀请!"
    await _apply_invite_decision(pending, approved, comment or default, system=True)
    _forget_invite(pending)


def _pending_invite(request_id: str) -> PendingInvite | None:
    return pending_invites.get(str(request_id).strip())


async def _can_review_invite(session: Session, pending: PendingInvite) -> bool:
    context = context_from_session(session)
    if context.chat_type == "group" and context.channel_id == pending.group_id:
        return await permission_checker.require_perm(context, Permission.BotAdmin)
    # Bot 未入目标群时，只有 Master 能从私聊或其他群处理，避免把审批权
    # 意外扩大到任意群管理员。
    return await permission_checker.require_perm(context, Permission.Master)


invite_approve_command = Alconna(
    "同意邀请",
    Args["request_id", str],
    meta=CommandMeta(
        "同意待审群邀请（目标群 BotAdmin/Master；GroupAdmin 无权）",
        usage="同意邀请 <请求ID>",
        example="同意邀请 abc123",
        compact=False,
    ),
)
invite_approve_command.shortcut("同意", command="同意邀请", prefix=True)


@command.on(invite_approve_command)
async def approve_invite(session: Session, request_id: Match[str]):
    pending = _pending_invite(request_id.result)
    if pending is None:
        return f"未找到待审邀请: {request_id.result}"
    if not await _can_review_invite(session, pending):
        return "权限不足"
    try:
        await _apply_invite_decision(
            pending,
            True,
            "已同意您的邀请~",
            context=context_from_session(session),
        )
    except Exception as error:
        logger.exception("Failed to approve invite {}", pending.request_id)
        await report_action_error(error, session)
        return "处理邀请失败，已通知开发者"
    _forget_invite(pending)
    return f"已同意邀请 {pending.request_id}"


invite_reject_command = Alconna(
    "拒绝邀请",
    Args["request_id", str],
    Args["reason", MultiVar(str, "*")],
    meta=CommandMeta(
        "拒绝待审群邀请（目标群 BotAdmin/Master；GroupAdmin 无权）",
        usage="拒绝邀请 <请求ID> [理由]",
        example="拒绝邀请 abc123 非工作群",
        compact=False,
    ),
)
invite_reject_command.shortcut("拒绝", command="拒绝邀请", prefix=True)


@command.on(invite_reject_command)
async def reject_invite(
    session: Session,
    request_id: Match[str],
    reason: Match[tuple[str, ...]],
):
    pending = _pending_invite(request_id.result)
    if pending is None:
        return f"未找到待审邀请: {request_id.result}"
    if not await _can_review_invite(session, pending):
        return "权限不足"
    comment = " ".join(reason.result).strip() if reason.available else ""
    comment = comment or "BOT拒绝了你的入群邀请!"
    try:
        await _apply_invite_decision(
            pending,
            False,
            comment,
            context=context_from_session(session),
        )
    except Exception as error:
        logger.exception("Failed to reject invite {}", pending.request_id)
        await report_action_error(error, session)
        return "处理邀请失败，已通知开发者"
    _forget_invite(pending)
    return f"已拒绝邀请 {pending.request_id}"


pending_invites_command = Alconna(
    "待审邀请",
    meta=CommandMeta(
        "查看待审群邀请（Master 私聊；目标群 BotAdmin/Master 审批，GroupAdmin 无权）",
        usage="待审邀请",
        example="/待审邀请",
        compact=True,
    ),
)


@command.on(pending_invites_command)
@private_only()
@require(Permission.Master)
async def pending_invite_list(session: Session):
    if not pending_invites:
        return "当前没有待审邀请"
    lines = [
        f"{pending.request_id}: {pending.group_name}({pending.group_id}) "
        f"邀请人 {pending.inviter_name}"
        + (
            f"({pending.inviter_id})"
            if pending.inviter_id is not None
            else "（ID 不可得）"
        )
        for pending in pending_invites.values()
    ]
    return "待审邀请:\n" + "\n".join(lines)


leave_group_command = Alconna(
    "退群",
    # 群号和账号都是纯数字；\b 保证整词匹配（Alconna 的正则只做前缀匹配）。
    Args["group_id", r"re:\d+\b"],
    Args["account_id", r"re:\d+\b"],
    meta=CommandMeta(
        "指定 BOT 退出指定群（群内或 Master 私聊）",
        usage="退群 <群号> <BOT账号>",
        example="退群 40001 10001\n/quit 40001 10001",
        compact=False,
    ),
)
leave_group_command.shortcut("quit", command="退群", prefix=True)


@command.on(leave_group_command)
@require(Permission.Master)
async def leave_group(
    session: Session,
    group_id: Match[str],
    account_id: Match[str],
):
    context = context_from_session(session)
    target_group = group_id.result
    target_account = account_id.result

    registry = getattr(action_service, "registry", None)
    if registry is not None:
        account = registry.get(target_account)
        bound_accounts = registry.bound_accounts_for_group(target_group)
        if account is None or all(
            str(getattr(candidate, "self_id", "")) != target_account
            for candidate in bound_accounts
        ):
            return "没有找到目标群和BOT"

    async with action_errors(session):
        await action_service.leave_group(
            target_account,
            target_group,
            context=context,
            permission_checker=permission_checker,
        )
        return f"BOT{target_account}已退出群{target_group}!"


reset_capability_command = Alconna(
    "重置能力",
    Args["account_id?", int],
    meta=CommandMeta(
        "重置账号的平台能力学习状态（仅超管）",
        usage="重置能力 [账号]",
        example="重置能力\n重置能力 10001",
        compact=False,
    ),
)


@command.on(reset_capability_command)
@private_only()
@require(Permission.Master)
async def reset_capability(
    session: Session,
    account_id: Query[int] = Query("account_id"),
):
    context = context_from_session(session)
    target_account = (
        str(account_id.result) if account_id.available else context.account_id
    )
    reset_count = action_service.reset_capabilities(target_account)
    return f"已重置账号 {target_account} 的 {reset_count} 项平台能力学习状态"


async def read_group_settings(
    group_id: str, platform: str = QQ_PLATFORM
) -> dict[str, object]:
    """只读查询群设置；数据库不可用时返回默认值。"""

    from tenko.db.errors import DatabaseUnavailableError

    blacklisted = permission_store.blacklisted("group", platform, group_id)
    try:
        from tenko.db.repositories import group_setting_repository

        setting_row = await group_setting_repository.get((platform, group_id))
    except DatabaseUnavailableError as error:
        if group_id not in _database_warning_groups:
            _database_warning_groups.add(group_id)
            logger.warning(
                "群 {} 的设置数据库不可用，使用默认设置: {}", group_id, error
            )
        return {
            "blacklisted": blacklisted,
            "frequency_limitation": True,
            "response_type": "random",
        }

    response_type = (
        account_registry.response_type_for_group((platform, group_id)) or "random"
    )

    return {
        "blacklisted": blacklisted,
        "frequency_limitation": (
            True if setting_row is None else bool(setting_row.frequency_limitation)
        ),
        "response_type": response_type,
    }


def format_group_settings(group_id: str, settings: dict[str, object]) -> str:
    return (
        f"群{group_id}设置:\n"
        f"群状态: {'黑名单' if settings['blacklisted'] else '正常'}\n"
        f"频率限制: {'开启' if settings['frequency_limitation'] else '关闭'}\n"
        f"响应账号策略: {settings['response_type']}"
    )


_STRATEGY = {"random": "随机", "deterministic": "定向"}


def group_settings_data(
    group_id: str, settings: dict[str, object]
) -> dict[str, object]:
    """``list.html`` 的渲染数据，内容与 :func:`format_group_settings` 一致。"""

    strategy = str(settings["response_type"])
    rows = (
        ("群状态", "黑名单" if settings["blacklisted"] else "正常", ""),
        ("频率限制", "开启" if settings["frequency_limitation"] else "关闭", ""),
        ("响应账号策略", _STRATEGY.get(strategy, strategy), strategy),
    )
    items = tuple(
        {"number": number, "name": name, "meta": value, "detail": detail, "badge": ""}
        for number, (name, value, detail) in enumerate(rows, 1)
    )
    return list_data(
        "群设置", "只读查询", f"群 {group_id}", f"群{group_id}", items, "暂无设置"
    )


group_setting_command = Alconna(
    "群设置",
    Option(
        "--group",
        Args["group_id", int],
        alias=["-g", "群"],
        help_text="群内仅查询当前群；跨群查询需 Master 私聊",
    ),
    meta=CommandMeta(
        "只读查询群设置",
        usage="群设置 [--group <群号>]",
        example="/群设置\n/群设置 --group 40001",
        compact=True,
    ),
)


# 群聊要求 GroupAdmin、私聊要求 Master，随聊天类型而变，所以不用装饰器。
@command.on(group_setting_command)
async def group_setting(
    session: Session,
    group: Query[int] = Query("group.group_id"),
    *,
    render_service: RenderService,
):
    context = context_from_session(session)
    if context.chat_type == "group":
        target_group = str(group.result) if group.available else context.channel_id
        if target_group != context.channel_id:
            return "群内只能查询当前群"
        if not await permission_checker.require_perm(context, Permission.GroupAdmin):
            return "权限不足"
    elif context.chat_type == "private":
        if not await permission_checker.require_perm(context, Permission.Master):
            return "权限不足"
        if not group.available:
            return "Master 私聊查询请提供 --group <群号>"
        target_group = str(group.result)
    else:
        return "该指令仅支持群聊或 Master 私聊执行"
    settings = await read_group_settings(target_group, context.platform)
    return await render_or_text(
        render_service,
        "list.html",
        group_settings_data(target_group, settings),
        format_group_settings(target_group, settings),
    )


frequency_limit_command = Alconna(
    "频率限制",
    Args["state?", "开启|关闭"],
    meta=CommandMeta(
        "查询或设置当前群的命令频率限制",
        usage="频率限制 [开启|关闭]",
        example="/频率限制\n/频率限制 关闭",
        compact=False,
    ),
)


@command.on(frequency_limit_command)
@group_only()
@require(Permission.GroupAdmin)
async def frequency_limit(
    session: Session,
    state: Query[str] = Query("state"),
):
    context = context_from_session(session)
    group_id = context.group
    if not state.available:
        current = "开启" if rate_limit_service.is_group_limited(group_id) else "关闭"
        return f"当前群频率限制：{current}"

    from tenko.db.errors import DatabaseUnavailableError
    from tenko.db.repositories import group_setting_repository

    enabled = str(state.result) == "开启"
    try:
        await group_setting_repository.set_frequency_limitation(group_id, enabled)
    except DatabaseUnavailableError:
        return "设置数据库暂不可用，请稍后再试"
    rate_limit_service.set_group_limited(group_id, enabled)
    return f"已{'开启' if enabled else '关闭'}当前群的频率限制"


def _quoted_user_id(session: Session) -> str | None:
    """被回复消息的发送者：Entari 已把回复解析为 ``session.quote``。"""

    quote = session.quote
    if quote is None:
        return None
    author = next(iter(select(quote, Author)), None)
    return author.id if author is not None else None


def _value_id(value: object) -> str | None:
    if isinstance(value, At):
        return value.id
    normalized = str(value).strip()
    if normalized.startswith("@"):
        normalized = normalized[1:]
    return normalized if normalized.isdigit() else None


def _moderation_target(
    arguments: tuple[object, ...],
    session: Session,
    option_minutes: Match[int],
) -> tuple[str | None, int | None, bool]:
    """解析文本/At/回复三种目标，并保留旧命令的分钟单位。"""

    target: str | None = None
    target_from_argument = False
    numeric_values: list[str] = []
    invalid = False
    for value in arguments:
        if isinstance(value, At):
            target = target or _value_id(value)
            target_from_argument = True
            if target is None:
                invalid = True
            continue
        candidate = _value_id(value)
        if candidate is None:
            invalid = True
        else:
            numeric_values.append(candidate)

    quoted_target = _quoted_user_id(session)
    target = target or quoted_target
    if target is None and numeric_values:
        target = numeric_values[0]

    minutes: int | None = option_minutes.result if option_minutes.available else None
    if minutes is None:
        if (target_from_argument or quoted_target is not None) and numeric_values:
            minutes = int(numeric_values[-1])
        elif len(numeric_values) > 1:
            minutes = int(numeric_values[1])
    return target, minutes, invalid


async def _target_has_management_permission(context, target_id: str) -> bool:
    target_context = replace(context, user_id=target_id, member_role=None)
    return await permission_checker.require_perm(target_context, Permission.GroupAdmin)


_MODERATION_ARGS = Args["arguments", MultiVar(object, "*")]


mute_command = Alconna(
    "禁言",
    _MODERATION_ARGS,
    Option(
        "--time",
        Args["time", int],
        alias=["-t"],
        default=2,
        help_text="禁言时长（分钟），默认 2",
    ),
    meta=CommandMeta(
        "禁言群成员（时长单位为分钟）",
        usage="禁言 [@成员|成员ID] [分钟] [-t <分钟>]",
        example="禁言 @123456 5",
        compact=False,
    ),
)


@command.on(mute_command)
@group_only()
@require(Permission.GroupAdmin)
async def mute(
    session: Session,
    arguments: Match[tuple[object, ...]],
    time: Match[int],
):
    context = context_from_session(session)
    target_id, minutes, invalid = _moderation_target(arguments.result, session, time)
    if invalid or target_id is None:
        return "请@成员、填写成员ID或回复目标消息"
    if minutes is None:
        minutes = 2
    if not 0 < minutes <= 30 * 24 * 60:
        return "时间非法!范围(分钟): `0 < time <= 43200`"
    if target_id == context.account_id or account_registry.get(target_id):
        return "禁言bot?给你一棒槌!"
    if await _target_has_management_permission(context, target_id):
        return "bot权限不足!(目标权限>=32)"
    async with action_errors(session):
        await action_service.mute_member(
            context.account_id,
            context.channel_id,
            target_id,
            minutes * 60,
            context=context,
            permission_checker=permission_checker,
        )
        return f"已设置【{target_id}】{minutes}分钟的禁言!"


unmute_command = Alconna(
    "解禁",
    _MODERATION_ARGS,
    meta=CommandMeta(
        "解除群成员禁言",
        usage="解禁 [@成员|成员ID]（也可回复目标消息）",
        example="解禁 @123456",
        compact=False,
    ),
)


@command.on(unmute_command)
@group_only()
@require(Permission.GroupAdmin)
async def unmute(
    session: Session,
    arguments: Match[tuple[object, ...]],
):
    context = context_from_session(session)
    target_id, _, invalid = _moderation_target(
        arguments.result, session, Match(None, False)
    )
    if invalid or target_id is None:
        return "请@成员、填写成员ID或回复目标消息"
    async with action_errors(session):
        await action_service.unmute_member(
            context.account_id,
            context.channel_id,
            target_id,
            context=context,
            permission_checker=permission_checker,
        )
        return f"已解禁{target_id}!"


unmute_self_command = Alconna(
    "解禁自己",
    meta=CommandMeta(
        "解除当前 BOT 在本群的禁言状态",
        usage="解禁自己",
        example="/解禁自己",
        compact=True,
    ),
)


@command.on(unmute_self_command)
@group_only()
@require(Permission.GroupAdmin)
async def unmute_self(session: Session):
    context = context_from_session(session)
    async with action_errors(session):
        await action_service.unmute_member(
            context.account_id,
            context.channel_id,
            context.account_id,
            context=context,
            permission_checker=permission_checker,
        )
        return "已解除本BOT在当前群的禁言状态!"


whole_mute_command = Alconna(
    "全体禁言",
    meta=CommandMeta(
        "开启全体禁言", usage="全体禁言", example="/全体禁言", compact=False
    ),
)


@command.on(whole_mute_command)
@group_only()
@require(Permission.GroupAdmin)
async def mute_all(session: Session):
    context = context_from_session(session)
    async with action_errors(session):
        await action_service.mute_group(
            context.account_id,
            context.channel_id,
            True,
            context=context,
            permission_checker=permission_checker,
        )
        return "开启全体禁言成功!"


whole_unmute_command = Alconna(
    "全体解禁",
    meta=CommandMeta(
        "关闭全体禁言",
        usage="全体解禁",
        example="/全体解禁\n/关闭全体禁言",
        compact=False,
    ),
)
whole_unmute_command.shortcut("关闭全体禁言", command="全体解禁", prefix=True)


@command.on(whole_unmute_command)
@group_only()
@require(Permission.GroupAdmin)
async def unmute_all(session: Session):
    context = context_from_session(session)
    async with action_errors(session):
        await action_service.unmute_group(
            context.account_id,
            context.channel_id,
            context=context,
            permission_checker=permission_checker,
        )
        return "关闭全体禁言成功!"


recall_command = Alconna(
    "撤回",
    meta=CommandMeta(
        "撤回回复的消息",
        usage="撤回（回复一条消息）",
        example="/撤回（回复需要撤回的消息）",
        compact=False,
    ),
)


@command.on(recall_command)
@group_only()
@require(Permission.GroupAdmin)
async def recall(session: Session):
    context = context_from_session(session)
    quote = session.quote
    if quote is None or quote.id is None:
        return "请回复需要撤回的消息"
    async with action_errors(session):
        await action_service.delete_message(
            context.account_id,
            quote.id,
            channel_id=context.channel_id,
            context=context,
            permission_checker=permission_checker,
        )


essence_command = Alconna(
    "加精",
    Args["message_id?", r"re:\d+\b"],
    meta=CommandMeta(
        "设置回复消息或指定消息为群精华",
        usage="加精 [消息ID]（或回复消息）",
        example="/加精 60001\n/设精（回复消息）",
        compact=False,
    ),
)
essence_command.shortcut("设精", command="加精", prefix=True)


@command.on(essence_command)
@group_only()
@require(Permission.GroupAdmin)
async def set_essence(
    session: Session,
    message_id: Query[str] = Query("message_id"),
):
    context = context_from_session(session)
    target_message_id = message_id.result if message_id.available else None
    if target_message_id is None and session.quote:
        target_message_id = session.quote.id
    # 命令参数已由 Alconna 限定为数字；回复消息的 ID 来自平台，仍需确认。
    if not target_message_id or not target_message_id.isdigit():
        return "请提供数字消息ID或回复需要加精的消息"
    async with action_errors(session):
        await action_service.set_essence(
            context.account_id,
            target_message_id,
            context=context,
            permission_checker=permission_checker,
        )
        return "加精成功"


kick_command = Alconna(
    "踢出",
    _MODERATION_ARGS,
    meta=CommandMeta(
        "踢出群成员",
        usage="踢出 [@成员|成员ID]（也可回复目标消息）",
        example="踢出 @123456",
        compact=False,
    ),
)


@command.on(kick_command)
@group_only()
@require(Permission.GroupAdmin)
async def kick(
    session: Session,
    arguments: Match[tuple[object, ...]],
):
    context = context_from_session(session)
    target_id, _, invalid = _moderation_target(
        arguments.result, session, Match(None, False)
    )
    if invalid or target_id is None:
        return "请@成员、填写成员ID或回复目标消息"
    if target_id == context.account_id or account_registry.get(target_id):
        return "不能踢出bot!"
    if await _target_has_management_permission(context, target_id):
        return "bot权限不足!(目标权限>=32)"
    async with action_errors(session):
        await action_service.kick_member(
            context.account_id,
            context.channel_id,
            target_id,
            context=context,
            permission_checker=permission_checker,
        )
        return f"已将{target_id}踢出群聊!"
