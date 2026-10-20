from __future__ import annotations

import inspect
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from arclet.entari import BasicConfModel, MessageChain, Session
from loguru import logger
from satori import Image, Text

from tenko.context import MessageContext
from tenko.host.actions import (
    ActionAccountUnavailable,
    ActionCapabilityUnavailable,
    ActionExecutionError,
    ActionPermissionDenied,
    ActionService,
    ActionServiceError,
    ActionTargetUnavailable,
)
from tenko.host.perm import superuser_ids
from tenko.render import RenderService, try_render


def context_from_session(session: Session) -> MessageContext:
    """将 Entari 的消息 Session 转换为 Tenko 权限上下文。"""

    origin = getattr(session.event, "_origin", None)
    if origin is None:
        raise ValueError("Entari session event does not expose a Satori origin")
    return MessageContext.from_event(origin)


class NotifyGroupConfig(BasicConfModel):
    """内置插件共用的 Entari 插件配置：管理通知的群目标，空值表示改发 Master 私聊。"""

    notify_group: str | int | None = None


def master_id_for_account(account: object, permission_checker: object) -> str | None:
    """按现有通知策略解析账号对应的第一个 Master。"""

    registry = getattr(permission_checker, "registry", None)
    configured_master = str(getattr(registry, "master_id", None) or "").strip()
    if configured_master:
        return configured_master
    return next(iter(superuser_ids(getattr(account, "platform", "onebot"))), None)


async def send_private_message(session: Session, user_id: str, content: str) -> bool:
    """向指定用户发送文本，失败时只记录日志并返回 False。"""

    sender = getattr(session, "send_private_message", None)
    if callable(sender):
        try:
            result = sender([Text(content)], user_id=str(user_id))
            if inspect.isawaitable(result):
                await result
            return True
        except Exception:
            logger.exception("私聊消息发送失败: user_id={}", user_id)
            logger.error("私聊消息内容如下:\n{}", content)
            return False

    account = getattr(session, "account", None)
    protocol = getattr(account, "protocol", None)
    sender = getattr(protocol, "send_private_message", None)
    if not callable(sender):
        logger.error("当前会话没有私聊发送能力: user_id={}", user_id)
        logger.error("私聊消息内容如下:\n{}", content)
        return False
    try:
        result = sender(str(user_id), [Text(content)])
        if inspect.isawaitable(result):
            await result
        return True
    except Exception:
        logger.exception("私聊消息发送失败: user_id={}", user_id)
        logger.error("私聊消息内容如下:\n{}", content)
        return False


def action_error_message(error: BaseException) -> str:
    """将动作错误转换为不会泄漏平台回执的群内短消息。"""

    if isinstance(error, ActionPermissionDenied):
        return "权限不足"
    if isinstance(error, ActionCapabilityUnavailable):
        return "该账号暂不支持此操作（或已被临时限制），已通知开发者"
    if isinstance(error, ActionExecutionError):
        failure = error.failure
        if failure is not None and ActionService._is_permission_failure(failure):
            return "该账号在此群没有管理员权限"
        return "平台操作失败，已通知开发者"
    if isinstance(error, ActionAccountUnavailable):
        return "账号暂不可用，已通知开发者"
    if isinstance(error, ActionTargetUnavailable):
        return "当前群暂不可用，已通知开发者"
    if isinstance(error, ActionServiceError):
        return "平台操作失败，已通知开发者"
    return "平台操作失败，已通知开发者"


@asynccontextmanager
async def action_errors(session: Session) -> AsyncIterator[None]:
    """把平台动作失败转成群内短提示，并向 superusers 上报原始错误。

    成功消息写在 ``async with`` 块内 ``return``；失败时异常被吞掉、提示已经发出，
    块后无返回，处理函数得到 ``None``（Entari 不再发送任何内容）。
    """

    try:
        yield
    except ActionServiceError as error:
        await report_action_error(error, session)
        await session.send(action_error_message(error))


def _action_failure_fields(error: BaseException) -> tuple[object, ...]:
    failure = getattr(error, "failure", None)
    if failure is None:
        return (None,) * 7
    return (
        failure.account_id,
        getattr(failure.capability, "value", failure.capability),
        failure.action,
        failure.retcode,
        failure.message,
        failure.wording,
        failure.echo,
    )


async def report_action_error(error: BaseException, origin: object) -> None:
    """复用异常捕获插件向 superusers 报告动作错误，并吞掉投递异常。"""

    reporter = sys.modules.get("tenko.plugins.exception_catcher")
    send_error_report = getattr(reporter, "send_error_report", None)
    if not callable(send_error_report):
        account, capability, action, retcode, message, wording, echo = (
            _action_failure_fields(error)
        )
        logger.error(
            "Action error reporter unavailable: account={} capability={} "
            "action={} retcode={} message={} wording={} echo={}",
            account,
            capability,
            action,
            retcode,
            message,
            wording,
            echo,
        )
        return
    try:
        await send_error_report(error, origin)
    except Exception:
        account, capability, action, retcode, message, wording, echo = (
            _action_failure_fields(error)
        )
        logger.exception(
            "Action error report delivery failed: account={} capability={} "
            "action={} retcode={} message={} wording={} echo={}",
            account,
            capability,
            action,
            retcode,
            message,
            wording,
            echo,
        )


def normalize_targets(value: object) -> tuple[str, ...]:
    """将 Alconna 已解析的值规范化为数据库 ID。"""

    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)  # type: ignore[union-attr]


async def render_or_text(
    render_service: RenderService,
    template: str,
    data: Mapping[str, object],
    fallback: str,
) -> MessageChain | str:
    """渲染 HTML 模板为图片消息；渲染不可用或失败时返回文本 ``fallback``。"""

    image = await try_render(render_service.render_template, template, dict(data))
    if image is None:
        return fallback
    return MessageChain(Image.of(raw=image, mime="image/jpeg"))


def list_data(
    title: str,
    subtitle: str,
    badge: str,
    summary: str,
    items: tuple[dict[str, object], ...],
    empty_text: str,
) -> dict[str, object]:
    """``list.html`` 模板的渲染数据。"""

    return {
        "title": title,
        "subtitle": subtitle,
        "badge": badge,
        "summary": summary,
        "items": items,
        "item_count": len(items),
        "empty_text": empty_text,
    }
