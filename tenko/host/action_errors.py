"""平台动作失败的处理：转成群内短提示，并把原始错误上报给 Master。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from arclet.entari import Session
from loguru import logger

from .actions import (
    ActionAccountUnavailable,
    ActionCapabilityUnavailable,
    ActionExecutionError,
    ActionPermissionDenied,
    ActionService,
    ActionServiceError,
    ActionTargetUnavailable,
)

ErrorReporter = Callable[[BaseException, object], Awaitable[None]]
# 异常捕获插件加载时登记的上报函数；宿主不反向导入插件。
_error_reporter: ErrorReporter | None = None


def register_error_reporter(reporter: ErrorReporter) -> Callable[[], None]:
    """登记动作错误的上报函数，返回注销函数（交给插件的 ``collect_disposes``）。"""

    global _error_reporter
    _error_reporter = reporter

    def unregister() -> None:
        global _error_reporter
        if _error_reporter is reporter:
            _error_reporter = None

    return unregister


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
    """经异常捕获插件登记的上报函数向 superusers 报告动作错误，并吞掉投递异常。"""

    send_error_report = _error_reporter
    if send_error_report is None:
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
