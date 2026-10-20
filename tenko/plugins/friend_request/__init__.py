"""好友申请处理：按全局策略自动同意，其余保持待处理并私聊通知 Master。"""

from __future__ import annotations

from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import Session, command, plugin
from arclet.entari.command import Query
from arclet.entari.event.base import FriendRequestEvent
from arclet.entari.plugin import PluginRole
from loguru import logger

from tenko.context import MessageContext
from tenko.db.errors import DatabaseUnavailableError
from tenko.host.actions import action_service
from tenko.host.friend_requests import (
    MODE_LABELS,
    default_repository,
    get_mode,
    set_mode,
    should_approve,
)
from tenko.host.guards import private_only, require
from tenko.host.perm import Permission, PermissionChecker
from tenko.plugins._common import master_id_for_account


plugin.metadata(
    "好友申请",
    PluginRole.NORMAL,
    author=["Tenko"],
    version="0.1.0",
    description="按全局策略处理好友申请，Master 私聊或 WebUI 可随时调整。",
    classifier=["required", "host"],
)

permission_checker = PermissionChecker()


async def _is_admin(user_id: str, account_id: str, platform: str) -> bool:
    context = MessageContext(
        account_id=account_id,
        event_type="friend-request",
        protocol_event_type=None,
        chat_type="private",
        channel_id=f"private:{user_id}",
        user_id=user_id,
        message_id="",
        text="",
        image_urls=(),
        platform=platform,
    )
    try:
        return await permission_checker.require_perm(context, Permission.BotAdmin)
    except Exception:
        # 读不到权限时不能自动放行，按普通申请交给 Master。
        logger.exception("Could not check friend request applicant permission")
        return False


def _notice(user_id: str, name: str, comment: str, request_id: str, reason: str) -> str:
    return (
        f"好友申请待处理（{reason}）\n"
        f"申请人: {name}({user_id})\n"
        f"验证消息: {comment or '（无）'}\n"
        f"请求 ID: {request_id}\n"
        "请在 QQ 的新朋友/好友通知中手动同意或拒绝；"
        "发送 /好友申请 查看或修改处理策略。"
    )


@plugin.listen(FriendRequestEvent)
async def friend_request_event(event: FriendRequestEvent):
    account = event.account
    request_id = str(event.message.id)
    user_id = str(event.user.id)

    try:
        mode = await get_mode(default_repository())
    except Exception:
        # 数据库不可用或设置损坏：不自动同意，按待处理通知 Master。
        logger.exception("Could not read friend request mode; keeping request pending")
        mode = None
        reason = "策略读取失败，未自动同意"
    else:
        reason = f"当前策略：{MODE_LABELS[mode]}"
    if mode == "off":
        logger.info("Friend request {} ignored: mode is off", request_id)
        return

    is_admin = mode == "admins" and await _is_admin(
        user_id, str(account.self_id), str(account.platform)
    )
    if should_approve(mode, is_admin):
        try:
            await action_service.approve_friend_request(account, request_id, True)
        except Exception:
            # 同意失败时申请仍在平台待处理，转给 Master 手动处理。
            logger.exception("Failed to approve friend request {}", request_id)
            reason = "自动同意失败"
        else:
            logger.info("Approved friend request {} from {}", request_id, user_id)
            return

    master_id = master_id_for_account(account, permission_checker)
    if master_id is None:
        logger.warning(
            "Friend request {} pending without configured Master", request_id
        )
        return
    try:
        await action_service.send_private_message(
            account,
            master_id,
            _notice(
                user_id,
                str(event.user.name or user_id),
                str(event.message.content or ""),
                request_id,
                reason,
            ),
        )
    except Exception:
        logger.exception("Failed to notify Master about friend request {}", request_id)


friend_request_command = Alconna(
    "好友申请",
    Args["mode?", "关闭|仅管理员|所有人"],
    meta=CommandMeta(
        "查看或设置好友申请处理策略（Master 私聊）",
        usage="好友申请 [关闭|仅管理员|所有人]",
        example="/好友申请\n/好友申请 所有人",
        compact=False,
    ),
)


@command.on(friend_request_command)
@private_only()
@require(Permission.Master)
async def friend_request_policy(
    session: Session, mode: Query[str] = Query("mode", None)
):
    repository = default_repository()
    try:
        if not mode.available:
            current = MODE_LABELS[await get_mode(repository)]
            return f"当前好友申请策略：{current}"
        value = next(key for key, label in MODE_LABELS.items() if label == mode.result)
        await set_mode(repository, value)
    except (DatabaseUnavailableError, ValueError):
        return "设置数据库暂不可用或数据异常，请稍后再试"
    return f"好友申请策略已设为：{MODE_LABELS[value]}"
