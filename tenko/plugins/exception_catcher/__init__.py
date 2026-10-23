from __future__ import annotations

import hashlib
import time
import traceback
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arclet.entari import BasicConfModel, ExceptionEvent, plugin, scheduler
from arclet.entari.plugin import get_plugins
from arclet.entari.plugin import PluginRole, plugin_config
from loguru import logger
from satori import Image, Text
from satori.exception import NotFoundException

from tenko.context import MessageContext
from tenko.host.metrics import MessageLog, message_metrics
from tenko.host.actions import (
    ActionExecutionError,
    ActionFailure,
    ActionTargetUnavailable,
)
from tenko.host import sandbox as sandbox_module
from tenko.host.errors import error_book, summary_text
from tenko.host.notify import notify
from tenko.host.sandbox import SandboxRefused
from tenko.host.render import RenderService, try_render


plugin.metadata(
    "异常捕获",
    PluginRole.UTILITY,
    author=["13"],
    description="捕获 Entari 全局异常并向超级用户发送带上下文的取证报告。",
    classifier=["required"],
)


class Config(BasicConfModel):
    """由宿主按 ``[exception]`` 传入。"""

    evidence_dir: str = ".tenko/exceptions"
    """投递失败时保留本地取证报告的目录。"""


config = plugin_config(Config)
MAX_EVIDENCE_FILES = 200
_IGNORED_EXCEPTION_NAMES = frozenset(
    {"AccountMuted", "UnknownTarget", "ActionTargetUnavailable"}
)
_IGNORED_EXCEPTION_PHRASES = (
    "account muted",
    "unknown target",
    "target not found",
    "目标不存在",
    "目标不可用",
    "对象位置未知",
    "对象不存在或不可及",
    "账号在对象所在聊天区域被封禁",
    "账号在群内禁言",
)


def get_error_hash(exception: BaseException) -> str:
    return hashlib.md5(
        f"{exception.__class__.__name__}:{exception}".encode()
    ).hexdigest()


def _ignored_exception_details(exception: BaseException) -> str:
    failure = getattr(exception, "failure", None)
    if not isinstance(failure, ActionFailure):
        return ""
    return " ".join(
        str(value).lower()
        for value in (
            failure.error_type,
            failure.message,
            failure.wording,
            failure.detail,
        )
        if value
    )


def is_ignored_exception(exception: BaseException) -> bool:
    """匹配旧版不报告的“目标不可达/账号被禁言”异常。"""

    if isinstance(exception, ActionTargetUnavailable | NotFoundException):
        return True
    if type(exception).__name__ in _IGNORED_EXCEPTION_NAMES:
        return True
    details = _ignored_exception_details(exception)
    if isinstance(exception, ActionExecutionError) and details:
        if any(phrase in details for phrase in _IGNORED_EXCEPTION_PHRASES):
            return True
        if "notfoundexception" in details or "unknowntarget" in details:
            return True
        if "accountmuted" in details:
            return True
    cause = exception.__cause__
    return cause is not None and cause is not exception and is_ignored_exception(cause)


def is_send_timeout(exception: BaseException) -> bool:
    """NapCat 发消息时等 QQ 确认超时（retcode 1200）。消息通常已经送达，只是回执慢了。"""

    seen: BaseException | None = exception
    while seen is not None:
        text = str(seen)
        if "Timeout: NTEvent" in text and "/sendMsg" in text:
            return True
        seen = seen.__cause__ if seen.__cause__ is not seen else None
    return False


def _compact(value: object, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _origin_event(origin: Any) -> tuple[Any, MessageContext | None]:
    event = getattr(origin, "_origin", None)
    if event is None:
        event = getattr(origin, "event", origin)
    raw_event = getattr(event, "_origin", event)
    try:
        return event, MessageContext.from_event(raw_event)
    except (AttributeError, TypeError, ValueError):
        return event, None


def _context_details(origin: Any) -> tuple[str, ...]:
    event, context = _origin_event(origin)
    if context is not None:
        group = context.channel_id if context.chat_type == "group" else "-"
        message = _compact(context.text)
        if not message and context.image_urls:
            message = f"[图片×{len(context.image_urls)}]"
        return (
            f"账号={context.account_id}",
            f"平台={context.platform}",
            f"群={group}",
            f"用户={context.user_id}",
            f"消息摘要={message or '<空消息>'}",
        )

    account = getattr(origin, "account", None) or getattr(event, "account", None)
    account_id = getattr(account, "self_id", "-")
    platform = getattr(account, "platform", "-")
    return (
        f"账号={account_id}",
        f"平台={platform}",
        "群=-",
        "用户=-",
        "消息摘要=-",
    )


def _report_time(value: datetime | None) -> datetime:
    # 与最近消息一样用本地时区，两边的时间才能直接对照。
    return (value or datetime.now()).astimezone()


def _recent_lines(
    recent_messages: Iterable[MessageLog] | None,
    recent_limit: int | None,
) -> tuple[str, ...]:
    if recent_messages is None:
        recent = message_metrics.recent_messages
        limit = message_metrics.buffer_size if recent_limit is None else recent_limit
    else:
        recent = tuple(recent_messages)
        limit = message_metrics.buffer_size if recent_limit is None else recent_limit
    if type(limit) is not int or limit <= 0:
        raise ValueError("recent_limit 必须是正整数")
    return tuple(record.summary() for record in recent[-limit:])


def _action_failure_lines(exception: BaseException) -> tuple[str, ...]:
    failure = getattr(exception, "failure", None)
    if not isinstance(failure, ActionFailure):
        return ()
    return (
        "平台动作结构化失败:",
        f"account_id={failure.account_id}",
        f"capability={failure.capability.value}",
        f"action={failure.action}",
        f"status={failure.status!r}",
        f"retcode={failure.retcode!r}",
        f"data={failure.data!r}",
        f"message={failure.message!r}",
        f"wording={failure.wording!r}",
        f"echo={failure.echo!r}",
        f"error_type={failure.error_type!r}",
        f"detail={failure.detail!r}",
        f"raw={failure.raw!r}",
    )


def generate_error_report(
    exception: BaseException,
    origin: Any | None = None,
    *,
    recent_messages: Iterable[MessageLog] | None = None,
    recent_limit: int | None = None,
    occurred_at: datetime | None = None,
) -> str:
    """生成包含异常、当前会话和最近消息的完整文本报告。"""

    occurred_at = _report_time(occurred_at)
    traceback_text = "".join(
        traceback.format_exception(type(exception), exception, exception.__traceback__)
    )
    recent = _recent_lines(recent_messages, recent_limit)
    lines = [
        "[Tenko 异常]",
        f"发生时间: {occurred_at.isoformat(timespec='seconds')}",
        f"异常类型: {type(exception).__module__}.{type(exception).__qualname__}",
        f"异常消息: {exception}",
        "当前会话: " + " ".join(_context_details(origin)),
    ]
    lines.extend(_action_failure_lines(exception))
    lines.append("最近消息（环形缓冲）:")
    if recent:
        lines.extend(f"{index}. {line}" for index, line in enumerate(recent, 1))
    else:
        lines.append("<无记录>")
    lines.extend(("完整 traceback:", traceback_text.rstrip("\n")))
    return "\n".join(lines)


def _write_local_evidence(
    exception: BaseException,
    report: str,
    *,
    evidence_dir: str | Path | None = None,
) -> Path | None:
    directory = Path(config.evidence_dir if evidence_dir is None else evidence_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    path = directory / f"{stamp}-{time.time_ns()}-{get_error_hash(exception)[:12]}.log"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(report + "\n", encoding="utf-8")
    except Exception:
        logger.exception("落盘异常取证报告失败: {}", path)
        logger.error("异常取证报告内容如下:\n{}", report)
        return None
    logger.warning("异常报告已落盘: {}", path)
    _prune_evidence(directory)
    return path


def _prune_evidence(directory: Path) -> None:
    """只保留最新的取证文件；文件名以时间戳开头，按名称排序即按时间排序。"""

    try:
        stale = sorted(directory.glob("*.log"))[:-MAX_EVIDENCE_FILES]
        for old in stale:
            old.unlink(missing_ok=True)
    except OSError:
        logger.exception("清理异常取证文件失败: {}", directory)


def _failure(event: Any, context: MessageContext | None) -> dict[str, str] | None:
    """触发异常的消息，留在内存里供在沙盒重放；不是消息事件时为 ``None``。"""

    raw = getattr(event, "_origin", event)
    content = getattr(getattr(raw, "message", None), "content", None)
    if context is None or not isinstance(content, str):
        return None
    return {
        "platform": context.platform,
        "account_id": context.account_id,
        "chat": context.chat_type,
        "group_id": context.channel_id if context.chat_type == "group" else "",
        "user_id": context.user_id,
        "content": content,
        "preview": _compact(context.text, 80)
        or (f"[图片×{len(context.image_urls)}]" if context.image_urls else ""),
    }


def _is_new_error(exception: BaseException, origin: Any) -> bool:
    """登记到异常聚合；只有首次出现的错误立即报告，重复的留给每小时汇总。"""

    modules = tuple(p.module.__name__ for p in get_plugins(True))
    event, context = _origin_event(origin)
    group = (
        context.channel_id
        if context is not None and context.chat_type == "group"
        else None
    )
    return error_book.record(exception, modules, group, _failure(event, context))


async def send_error_report(
    exception: BaseException,
    origin: Any,
    *,
    render_service: RenderService | None = None,
    evidence_dir: str | Path | None = None,
) -> None:
    """按通知设置发送报告，没有全部送达时保留本地证据。

    ``render_service=None`` 仅覆盖直接调用这个工具函数、无法经过 Entari
    listener 注入的边界；生产 listener ``except_handle`` 总是显式传入注入的
    服务实例。
    """

    if is_ignored_exception(exception):
        return
    if is_send_timeout(exception):
        # 不发报告：报告本身也是一条消息，协议端忙时同样会超时，只会越积越多。
        logger.warning("消息发送确认超时，通常已送达，不发异常报告：{}", exception)
        return
    if not _is_new_error(exception, origin):
        return
    report = generate_error_report(exception, origin)
    image = (
        None
        if render_service is None
        else await try_render(
            render_service.render_template, "report.html", {"report": report}
        )
    )
    content = (
        [Image.of(raw=image, mime="image/jpeg")]
        if image is not None
        else [Text(report)]
    )
    # 直接走协议：报告可能是图片，且 ActionService 自身出错时也要能送达。
    delivery_failed = not await notify(
        content, preferred=getattr(origin, "account", None), direct=True
    )

    if delivery_failed:
        _write_local_evidence(
            exception,
            report,
            evidence_dir=evidence_dir,
        )


@plugin.listen(ExceptionEvent)
async def except_handle(
    event: ExceptionEvent,
    *,
    render_service: RenderService,
):
    """处理 Entari dispatcher 发出的异常事件。"""

    if isinstance(event.origin, ExceptionEvent):
        return
    if isinstance(event.exception, SandboxRefused):
        # 沙盒里有意拦下的写入，不是故障：只在发起它的沙盒会话里说明。
        if sandbox_module.sandbox is not None and event.exception.channel:
            sandbox_module.sandbox.note(event.exception.channel, str(event.exception))
        return
    if is_ignored_exception(event.exception):
        return
    _, context = _origin_event(event.origin)
    if context is not None and context.platform == sandbox_module.SANDBOX_PLATFORM:
        # 沙盒（包括重放）里的报错只在沙盒会话里显示：不报告，也不计入异常聚合，
        # 否则重放仍失败时真实的同一错误会被当成重复。
        if sandbox_module.sandbox is not None:
            exc = event.exception
            sandbox_module.sandbox.note(
                context.channel_id, f"[沙盒] 处理时报错 {type(exc).__name__}: {exc}"
            )
        return
    await send_error_report(
        event.exception,
        event.origin,
        render_service=render_service,
    )


@scheduler.schedule(lambda: timedelta(minutes=5), label="tenko-error-summary")
async def send_error_summaries():
    for record in error_book.due():
        if not await notify(summary_text(record), direct=True):
            logger.warning("异常汇总未全部送达:\n{}", summary_text(record))
        error_book.settle(record)
