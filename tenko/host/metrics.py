"""消息统计：收发总数、滑动速率和最近消息的环形缓冲，供 /状态、异常报告和 WebUI 使用。"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from arclet.entari import MessageChain, SendResponse
from arclet.entari.logger import escape_tag, log
import arclet.letoderea as le
from satori.element import Message

from ..context import MessageContext
from .chat_log import chat_log
from .sandbox import is_sandbox


def _compact_text(value: object, limit: int = 160) -> str:
    """将消息内容压缩到可安全放入日志和异常报告的一行。"""

    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _message_timestamp(value: object | None) -> datetime:
    if isinstance(value, datetime):
        # Satori 用 datetime.fromtimestamp 解析时间戳，得到的是本地时间的 naive 值。
        return value.astimezone()
    return datetime.now().astimezone()


@dataclass(frozen=True, slots=True)
class MessageLog:
    """一条供状态查询和异常报告使用的轻量消息记录。"""

    timestamp: datetime
    direction: str
    account_id: str
    platform: str
    chat_type: str
    channel_id: str
    user_id: str | None
    message_id: str | None
    text: str
    event_type: str | None = None

    def summary(self) -> str:
        """返回不包含换行且长度受限的审计摘要。"""

        direction = "收" if self.direction == "received" else "发"
        target = self.channel_id
        if self.chat_type == "private":
            target = f"私聊:{target}"
        elif self.chat_type == "group":
            target = f"群:{target}"
        user = f" user={self.user_id}" if self.user_id else ""
        content = _compact_text(self.text) or "<空消息>"
        return (
            f"{self.timestamp.isoformat(timespec='seconds')} {direction}"
            f" account={self.account_id} {target}{user}"
            f" message={self.message_id or '-'} text={content}"
        )


@dataclass(slots=True)
class MessageMetrics:
    """维护消息总数、滑动速率和有限长度的消息环形缓冲。"""

    buffer_size: int = 10
    rate_window_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _received_count: int = field(init=False, default=0, repr=False)
    _sent_count: int = field(init=False, default=0, repr=False)
    _received_timestamps: deque[float] = field(init=False, repr=False)
    _sent_timestamps: deque[float] = field(init=False, repr=False)
    _recent: deque[MessageLog] = field(init=False, repr=False)
    # 每分钟一格的收发计数 [分钟序号, 接收, 发送]，供面板画近一小时活动。
    _minutes: deque[list[int]] = field(
        init=False, repr=False, default_factory=lambda: deque(maxlen=60)
    )

    def __post_init__(self) -> None:
        if type(self.buffer_size) is not int or self.buffer_size <= 0:
            raise ValueError("message buffer_size 必须是正整数")
        if self.rate_window_seconds <= 0:
            raise ValueError("message rate_window_seconds 必须大于 0")
        # 速率样本也必须是有上限的；单次发送回执通常只有一条，8192 条
        # 足以覆盖短时间突发，同时不会让状态对象随消息数无限增长。
        self._received_timestamps = deque(maxlen=8192)
        self._sent_timestamps = deque(maxlen=8192)
        self._recent = deque(maxlen=self.buffer_size)

    @property
    def received_count(self) -> int:
        return self._received_count

    @property
    def sent_count(self) -> int:
        return self._sent_count

    @property
    def recent_messages(self) -> tuple[MessageLog, ...]:
        """按时间顺序返回当前环形缓冲快照。"""

        return tuple(self._recent)

    @property
    def latest_received(self) -> MessageLog | None:
        for record in reversed(self._recent):
            if record.direction == "received":
                return record
        return None

    def configure_buffer(self, buffer_size: int) -> None:
        if type(buffer_size) is not int or buffer_size <= 0:
            raise ValueError("message buffer_size 必须是正整数")
        self.buffer_size = buffer_size
        self._recent = deque(self._recent, maxlen=buffer_size)

    def _trim_rates(self, now: float, window_seconds: float) -> None:
        cutoff = now - window_seconds
        for samples in (self._received_timestamps, self._sent_timestamps):
            while samples and samples[0] < cutoff:
                samples.popleft()

    def rates(self, window_seconds: float | None = None) -> tuple[int, int]:
        """返回窗口内的收发数量，调用方可格式化为每分钟速率。"""

        window = self.rate_window_seconds if window_seconds is None else window_seconds
        if window <= 0:
            raise ValueError("rate window 必须大于 0")
        now = self.clock()
        self._trim_rates(now, window)
        return len(self._received_timestamps), len(self._sent_timestamps)

    def _bump_minute(self, now: float, received: int, sent: int) -> None:
        minute = int(now // 60)
        if not self._minutes or self._minutes[-1][0] != minute:
            self._minutes.append([minute, 0, 0])
        self._minutes[-1][1] += received
        self._minutes[-1][2] += sent

    def activity(self, minutes: int = 60) -> list[tuple[int, int]]:
        """最近 ``minutes`` 分钟每分钟的（接收, 发送），最早的在前。"""

        current = int(self.clock() // 60)
        counts = {minute: (received, sent) for minute, received, sent in self._minutes}
        return [
            counts.get(current - offset, (0, 0))
            for offset in range(minutes - 1, -1, -1)
        ]

    def record_received(
        self, context: MessageContext, *, timestamp: object | None = None
    ) -> None:
        now = self.clock()
        self._received_count += 1
        self._received_timestamps.append(now)
        self._bump_minute(now, 1, 0)
        text = _compact_text(context.text)
        if not text and context.image_urls:
            text = f"[图片×{len(context.image_urls)}]"
        self._recent.append(
            MessageLog(
                timestamp=_message_timestamp(timestamp),
                direction="received",
                account_id=str(context.account_id),
                platform=str(context.platform),
                chat_type=context.chat_type,
                channel_id=str(context.channel_id),
                user_id=str(context.user_id),
                message_id=str(context.message_id),
                text=text,
                event_type=context.protocol_event_type or context.event_type,
            )
        )

    def record_sent(
        self,
        *,
        account_id: str,
        platform: str,
        chat_type: str,
        channel_id: str,
        text: object,
        count: int = 1,
        user_id: str | None = None,
        message_id: str | None = None,
        event_type: str | None = None,
        timestamp: object | None = None,
    ) -> None:
        if type(count) is not int or count <= 0:
            raise ValueError("sent message count 必须是正整数")
        now = self.clock()
        self._sent_count += count
        for _ in range(count):
            self._sent_timestamps.append(now)
        self._bump_minute(now, 0, count)
        self._recent.append(
            MessageLog(
                timestamp=_message_timestamp(timestamp),
                direction="sent",
                account_id=str(account_id),
                platform=str(platform),
                chat_type=chat_type,
                channel_id=str(channel_id),
                user_id=None if user_id is None else str(user_id),
                message_id=None if message_id is None else str(message_id),
                text=_compact_text(text),
                event_type=event_type,
            )
        )

    def record_sent_for_context(
        self,
        context: MessageContext,
        text: object,
        *,
        count: int = 1,
        message_id: str | None = None,
    ) -> None:
        self.record_sent(
            account_id=context.account_id,
            platform=context.platform,
            chat_type=context.chat_type,
            channel_id=context.channel_id,
            user_id=None,
            message_id=message_id,
            text=text,
            count=count,
            event_type="message_sent",
        )

    def record_send_response(self, response: SendResponse) -> None:
        """消费 Entari ``after_send``，覆盖插件通过 ``Session.send`` 的发送。"""

        account = getattr(response, "account", None)
        account_id = str(getattr(account, "self_id", "-"))
        platform = str(getattr(account, "platform", "unknown"))
        channel_id = str(getattr(response, "channel", "-"))
        chat_type = "private" if channel_id.startswith("private:") else "group"
        session = getattr(response, "session", None)
        event = getattr(session, "event", None)
        origin = getattr(event, "_origin", event)
        try:
            context = MessageContext.from_event(origin)
        except (AttributeError, TypeError, ValueError):
            context = None
        if context is not None:
            platform = context.platform
            chat_type = context.chat_type
            channel_id = context.channel_id

        text = _display(getattr(response, "message", ""))
        result = getattr(response, "result", ())
        try:
            result_items = tuple(result or ())
        except TypeError:
            result_items = ()
        message_id = (
            str(getattr(result_items[-1], "id", ""))
            if result_items and getattr(result_items[-1], "id", None) is not None
            else None
        )
        self.record_sent(
            account_id=account_id,
            platform=platform,
            chat_type=chat_type,
            channel_id=channel_id,
            user_id=None,
            message_id=message_id,
            text=text,
            count=max(len(result_items), 1),
            event_type="entari.after_send",
        )


message_metrics = MessageMetrics()


def configure_message_metrics(buffer_size: int) -> MessageMetrics:
    """应用配置并返回宿主共享的消息统计对象。"""

    message_metrics.configure_buffer(buffer_size)
    return message_metrics


def _display(message: object) -> str:
    """出站消息的日志文本。

    ``<message>`` 只是“单独成条”的包装（复读等插件用它避开自动引用），``display()``
    不展开它，日志里只剩 ``<message/>``；这里展开子元素再显示。
    """

    if isinstance(message, MessageChain):
        return "".join(
            _display(MessageChain(element.children))
            if isinstance(element, Message)
            else MessageChain([element]).display()
            for element in message
        )
    display = getattr(message, "display", None)
    return display() if callable(display) else str(message)


def _send_scene(event: SendResponse) -> str:
    channel = getattr(getattr(event.session, "event", None), "channel", None)
    if channel is not None and channel.id == event.channel and channel.name:
        return f"[{channel.name}({channel.id})]"
    return f"[{event.channel}]"


@le.on(SendResponse)
async def _record_entari_send(event: SendResponse) -> None:
    # Entari 的每条发送（Session.send、ActionService、account.protocol）都在成功后
    # 发布 SendResponse；在这里补齐出站统计和取证日志。
    # 沙盒对话只是面板里的测试，不计入出站统计和聊天记录。
    if not is_sandbox(event.account):
        message_metrics.record_send_response(event)
        chat_log.record_send(event)
    # 与 Entari 记录收到消息的 “[群名(群号)] 昵称(QQ) -> 内容” 同一形式，发送者是 bot。
    account = event.account
    user = getattr(getattr(account, "self_info", None), "user", None)
    name = getattr(user, "name", None) or account.self_id
    # bot 的发言用青色与收到的消息区分；颜色只作用于终端，文件和 WebUI 日志是纯文本。
    text = f"{name}({account.self_id}) -> {_display(event.message)!r}"
    log.message.opt(colors=True).info(
        f"{escape_tag(_send_scene(event))} <c>{escape_tag(text)}</c>"
    )
