from __future__ import annotations

from arclet.alconna import Alconna, Args, CommandMeta, Field, MultiVar, Option
from arclet.entari import Session, command, plugin
from arclet.entari.command import Match, Query
from arclet.entari.event.base import GuildMemberRemovedEvent, InternalEvent
from arclet.entari.plugin import PluginRole
from loguru import logger

from tenko.context import context_from_session
from tenko.db.errors import DatabaseUnavailableError
from tenko.host.actions import ActionServiceError, action_service
from tenko.host.guards import group_only, private_only, require
from tenko.host.notify import load_settings
from tenko.host.perm import (
    GLOBAL_SCOPE,
    Permission,
    PermissionChecker,
    superuser_ids,
)
from tenko.host.render import RenderService
from tenko.plugins._common import list_data, normalize_targets, render_or_text


plugin.metadata(
    "权限管理",
    PluginRole.NORMAL,
    author=["13"],
    description="管理 Tenko 的手动授权、BOT 管理员和黑名单。",
    classifier=["required"],
)


permission_checker = PermissionChecker()

_GROUP_OPTION = Option(
    "--group",
    Args["group_id", str],
    alias=["-g", "群"],
    help_text="指定目标群（跨群需要 BOT 管理员）",
)
_REASON_OPTION = Option(
    "--reason", Args["reason", str], alias=["-r", "原因"], help_text="拉黑原因"
)


def _targets_arg():
    return Args[
        "member_id", MultiVar(str), Field(missing_tips=lambda: "至少需要一个用户 ID")
    ]


def _failure_summary(targets: tuple[str, ...], failures: list[tuple[str, str]]) -> str:
    succeeded = len(targets) - len(failures)
    result = (
        f"共解析{len(targets)}个目标\n其中{succeeded}个执行成功,{len(failures)}个失败"
    )
    if failures:
        result += "\n\n失败目标:" + "".join(
            f"\n{target}-{reason}" for target, reason in failures
        )
    return result


def _database_error(error: BaseException, action: str) -> str:
    logger.warning("权限数据库不可用，{}未执行: {}", action, error)
    return f"数据库暂不可用，{action}未执行"


async def _target_group(session: Session, group: Query[str]) -> str | None:
    """返回目标群；跨群操作要求 BotAdmin，不满足时返回 ``None``。"""

    context = context_from_session(session)
    if not group.available:
        return context.channel_id
    target = str(group.result)
    if target != context.channel_id and not await permission_checker.require_perm(
        context, Permission.BotAdmin
    ):
        return None
    return target


async def _in_group(session: Session, group_id: str, user_id: str) -> bool:
    account = getattr(session, "account", None)
    if account is None:
        raise ActionServiceError("当前会话没有可用账号，无法确认群成员身份")
    return await action_service.get_group_member(account, group_id, user_id) is not None


grant_command = Alconna(
    "授权",
    Args["level", "群管理"],
    _targets_arg(),
    _GROUP_OPTION,
    meta=CommandMeta(
        "授予群管理权限（群主、群管理按实时群身份计算，不用授权）",
        usage="授权 群管理 <用户ID...> [--group <群号>]",
        example="授权 群管理 123456",
        compact=False,
    ),
)


@command.on(grant_command)
@group_only()
@require(Permission.GroupAdmin)
async def grant(
    session: Session,
    member_id: Match[tuple[str, ...]],
    group: Query[str] = Query("group.group_id"),
):
    context = context_from_session(session)
    # 跨群授权取决于参数，额外要求 BotAdmin，所以留在函数体里。
    target_group = await _target_group(session, group)
    if target_group is None:
        return "跨群授权需要 BOT 管理员"
    targets = normalize_targets(member_id.result)
    failures: list[tuple[str, str]] = []
    for target in targets:
        try:
            if not await _in_group(session, target_group, target):
                failures.append((target, f"没有在群{target_group}找到该成员"))
            elif not await permission_checker.store.grant(
                context.platform, target_group, target
            ):
                failures.append((target, "已经是授权的群管理"))
        except DatabaseUnavailableError as error:
            logger.warning("授权 {} 未执行，数据库不可用: {}", target, error)
            failures.append((target, "数据库暂不可用，未授权"))
        except ActionServiceError as error:
            logger.warning("成员 {} 的群成员身份确认失败: {}", target, error)
            failures.append((target, f"无法确认{target}是否在群内"))
    return _failure_summary(targets, failures)


revoke_command = Alconna(
    "取消授权",
    _targets_arg(),
    _GROUP_OPTION,
    meta=CommandMeta(
        "取消手动授予的群管理权限",
        usage="取消授权 <用户ID...> [--group <群号>]",
        example="取消授权 123456",
        compact=False,
    ),
)


@command.on(revoke_command)
@group_only()
@require(Permission.GroupAdmin)
async def revoke(
    session: Session,
    member_id: Match[tuple[str, ...]],
    group: Query[str] = Query("group.group_id"),
):
    context = context_from_session(session)
    target_group = await _target_group(session, group)
    if target_group is None:
        return "跨群取消授权需要 BOT 管理员"
    targets = normalize_targets(member_id.result)
    failures: list[tuple[str, str]] = []
    for target in targets:
        try:
            if not await permission_checker.store.revoke(
                context.platform, target_group, target
            ):
                failures.append((target, "不是手动授权的群管理"))
        except DatabaseUnavailableError as error:
            logger.warning("取消授权 {} 未执行，数据库不可用: {}", target, error)
            failures.append((target, "数据库暂不可用，未取消"))
    return _failure_summary(targets, failures)


bot_admin_command = Alconna(
    "BOT管理",
    Args["action", "添加|删除"],
    _targets_arg(),
    meta=CommandMeta(
        "增删 BOT 管理员",
        usage="BOT管理 <添加|删除> <用户ID...>",
        example="BOT管理 添加 123456",
        compact=False,
    ),
)


@command.on(bot_admin_command)
@require(Permission.Master)
async def change_bot_admin(
    session: Session, action: Match[str], member_id: Match[tuple[str, ...]]
):
    context = context_from_session(session)
    targets = normalize_targets(member_id.result)
    failures: list[tuple[str, str]] = []
    for target in targets:
        try:
            if action.result == "添加":
                if permission_checker.store.blacklisted(
                    "user", context.platform, target
                ):
                    failures.append((target, "在黑名单内，请先解除拉黑"))
                elif not await permission_checker.store.grant(
                    context.platform, GLOBAL_SCOPE, target
                ):
                    failures.append((target, "已经是BOT管理"))
            elif not await permission_checker.store.revoke(
                context.platform, GLOBAL_SCOPE, target
            ):
                failures.append((target, "还不是BOT管理"))
        except DatabaseUnavailableError as error:
            logger.warning("BOT 管理员 {} 修改失败: {}", target, error)
            failures.append((target, "数据库暂不可用，未修改"))
    return _failure_summary(targets, failures)


def _id_list_data(title: str, badge: str, meta: str, values) -> dict[str, object]:
    items = tuple(
        {"number": index, "name": str(value), "meta": meta, "detail": "", "badge": ""}
        for index, value in enumerate(values, 1)
    )
    return list_data(title, "", badge, f"共 {len(items)} 项", items, f"{title}为空")


def _blacklist_data(title: str, records) -> dict[str, object]:
    items = tuple(
        {
            "number": index,
            "name": record.target_id,
            "meta": record.reason or "未填写原因",
            "detail": (
                f"操作人 {record.operator_id or '-'} · "
                f"{record.created_at:%Y-%m-%d %H:%M}"
                if record.created_at
                else f"操作人 {record.operator_id or '-'}"
            ),
            "badge": "",
        }
        for index, record in enumerate(records, 1)
    )
    return list_data(title, "", "BLACK", f"共 {len(items)} 项", items, f"{title}为空")


@command.on(
    Alconna(
        "BOT管理列表",
        meta=CommandMeta("查询 BOT 管理员", usage="BOT管理列表", compact=True),
    )
)
@private_only()
@require(Permission.Master)
async def bot_admin_list(session: Session, *, render_service: RenderService):
    context = context_from_session(session)
    values = permission_checker.store.grants(context.platform, GLOBAL_SCOPE)
    if not values:
        return "当前还没有BOT管理哦~"
    return await render_or_text(
        render_service,
        "list.html",
        _id_list_data("BOT 管理列表", "ADMIN", "BOT 管理员", values),
        "BOT管理列表:\n" + "\n".join(values),
    )


permission_list_command = Alconna(
    "权限列表",
    _GROUP_OPTION,
    meta=CommandMeta(
        "查询群内手动授权的群管理",
        usage="权限列表 [--group <群号>]",
        example="权限列表\n权限列表 --group 40001",
        compact=True,
    ),
)
permission_list_command.shortcut(
    "perm list",
    {"command": "权限列表", "fuzzy": True, "prefix": False},
)


# 群聊查本群要求群管理、查别的群和私聊要求 BotAdmin，随参数而变，所以不用装饰器。
@command.on(permission_list_command)
async def permission_list(
    session: Session,
    group: Query[str] = Query("group.group_id"),
    *,
    render_service: RenderService,
):
    context = context_from_session(session)
    if context.chat_type == "group" and not group.available:
        target_group = context.channel_id
        required = Permission.GroupAdmin
    elif group.available:
        target_group = str(group.result)
        required = (
            Permission.GroupAdmin
            if target_group == context.channel_id
            else Permission.BotAdmin
        )
    else:
        return "私聊查询请提供 --group <群号>"
    if not await permission_checker.require_perm(context, required):
        return "权限不足"
    values = permission_checker.store.grants(context.platform, target_group)
    note = "群主、群管理按实时群身份计算，这里只列手动授权的群管理"
    text = f"群{target_group}手动授权的群管理:\n" + (
        "\n".join(values) if values else "（无）"
    )
    data = _id_list_data(f"群{target_group} 授权列表", "GRANT", "群管理", values)
    data["subtitle"] = note
    return await render_or_text(render_service, "list.html", data, f"{text}\n\n{note}")


def _protected(platform: str, user_id: str) -> str | None:
    if user_id in superuser_ids(platform):
        return "无法拉黑 Master"
    if permission_checker.store.granted(platform, GLOBAL_SCOPE, user_id):
        return "无法拉黑BOT管理，请先取消其BOT管理"
    return None


blacklist_command = Alconna(
    "拉黑",
    _targets_arg(),
    _REASON_OPTION,
    meta=CommandMeta(
        "把用户加入全局黑名单（任何群和私聊都不再响应）",
        usage="拉黑 <用户ID...> [--reason <原因>]",
        example="拉黑 123456 -r 刷屏",
        compact=False,
    ),
)


@command.on(blacklist_command)
@require(Permission.BotAdmin)
async def add_user_blacklist(
    session: Session,
    member_id: Match[tuple[str, ...]],
    reason: Query[str] = Query("reason.reason"),
):
    context = context_from_session(session)
    targets = normalize_targets(member_id.result)
    failures: list[tuple[str, str]] = []
    for target in targets:
        if problem := _protected(context.platform, target):
            failures.append((target, problem))
            continue
        try:
            if not await permission_checker.store.add_blacklist(
                "user",
                context.platform,
                target,
                reason=reason.result if reason.available else "",
                operator_id=context.user_id,
            ):
                failures.append((target, "已经在黑名单内"))
        except DatabaseUnavailableError as error:
            logger.warning("拉黑 {} 未执行，数据库不可用: {}", target, error)
            failures.append((target, "数据库暂不可用，未拉黑"))
    return _failure_summary(targets, failures)


unblacklist_command = Alconna(
    "解除拉黑",
    _targets_arg(),
    meta=CommandMeta(
        "把用户移出全局黑名单",
        usage="解除拉黑 <用户ID...>",
        example="解除拉黑 123456",
        compact=False,
    ),
)


@command.on(unblacklist_command)
@require(Permission.BotAdmin)
async def remove_user_blacklist(session: Session, member_id: Match[tuple[str, ...]]):
    context = context_from_session(session)
    targets = normalize_targets(member_id.result)
    failures: list[tuple[str, str]] = []
    for target in targets:
        try:
            if not await permission_checker.store.remove_blacklist(
                "user", context.platform, target
            ):
                failures.append((target, "不在黑名单内"))
        except DatabaseUnavailableError as error:
            logger.warning("解除拉黑 {} 未执行，数据库不可用: {}", target, error)
            failures.append((target, "数据库暂不可用，未解除"))
    return _failure_summary(targets, failures)


@command.on(
    Alconna("黑名单", meta=CommandMeta("查询全局黑名单", usage="黑名单", compact=True))
)
@private_only()
@require(Permission.BotAdmin)
async def user_blacklist(session: Session, *, render_service: RenderService):
    return await _show_blacklist(session, "user", "全局黑名单", render_service)


async def _show_blacklist(
    session: Session, kind, title: str, render_service: RenderService
):
    platform = context_from_session(session).platform
    records = [
        r for r in permission_checker.store.blacklist(kind) if r.platform == platform
    ]
    if not records:
        return f"{title}为空"
    text = f"{title}:\n" + "\n".join(
        f"{r.target_id}" + (f"（{r.reason}）" if r.reason else "") for r in records
    )
    return await render_or_text(
        render_service, "list.html", _blacklist_data(title, records), text
    )


async def _is_notify_group(group_id: str) -> bool:
    settings = await load_settings()
    return settings.mode == "group" and group_id == settings.group


group_blacklist_command = Alconna(
    "拉黑群",
    Args["group_id?", str],
    _REASON_OPTION,
    meta=CommandMeta(
        "把群加入黑名单（该群完全不响应；省略群号时为当前群）",
        usage="拉黑群 [群号] [--reason <原因>]",
        example="拉黑群 40001 -r 广告群",
        compact=False,
    ),
)


@command.on(group_blacklist_command)
@require(Permission.BotAdmin)
async def add_group_blacklist(
    session: Session,
    group_id: Match[str],
    reason: Query[str] = Query("reason.reason"),
):
    context = context_from_session(session)
    if group_id.available:
        target = str(group_id.result)
    elif context.chat_type == "group":
        target = context.channel_id
    else:
        return "私聊中请提供群号"
    if await _is_notify_group(target):
        return f"群{target}是通知群，不能拉黑"
    try:
        added = await permission_checker.store.add_blacklist(
            "group",
            context.platform,
            target,
            reason=reason.result if reason.available else "",
            operator_id=context.user_id,
        )
    except DatabaseUnavailableError as error:
        return _database_error(error, "拉黑群")
    if not added:
        return f"群{target}已经在黑名单内"
    return f"已拉黑群{target}，可在私聊用 /解除拉黑群 {target} 恢复"


@command.on(
    Alconna(
        "解除拉黑群",
        Args["group_id", str],
        meta=CommandMeta(
            "把群移出黑名单",
            usage="解除拉黑群 <群号>",
            example="解除拉黑群 40001",
            compact=False,
        ),
    )
)
@require(Permission.BotAdmin)
async def remove_group_blacklist(session: Session, group_id: Match[str]):
    context = context_from_session(session)
    try:
        removed = await permission_checker.store.remove_blacklist(
            "group", context.platform, str(group_id.result)
        )
    except DatabaseUnavailableError as error:
        return _database_error(error, "解除拉黑群")
    if not removed:
        return f"群{group_id.result}不在黑名单内"
    return f"已解除拉黑群{group_id.result}"


@command.on(
    Alconna(
        "群黑名单", meta=CommandMeta("查询群黑名单", usage="群黑名单", compact=True)
    )
)
@private_only()
@require(Permission.BotAdmin)
async def group_blacklist(session: Session, *, render_service: RenderService):
    return await _show_blacklist(session, "group", "群黑名单", render_service)


@plugin.listen(GuildMemberRemovedEvent)
async def revoke_on_leave(event: GuildMemberRemovedEvent):
    # 退群即撤销该群的手动授权，免得重新入群后自动带着群管理权限。
    try:
        await permission_checker.store.revoke(
            event.login.platform, event.guild.id, event.user.id
        )
    except DatabaseUnavailableError as error:
        logger.warning("退群成员 {} 的授权清理失败: {}", event.user.id, error)


@plugin.listen(InternalEvent)
async def log_unmapped_membership_capability(event: InternalEvent):
    # OneBot 适配器对于某些成员管理能力没有对应的 Satori 事件。
    # 在 NapCat 能力映射得到确认前保留显式日志；不要静默丢弃收到的内部事件。
    logger.warning(
        "收到未映射的成员管理能力事件，待 NapCat capability 确认: {}",
        getattr(event._origin, "_type", None),
    )
