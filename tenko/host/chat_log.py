"""面板聊天页的消息记录。

收到的消息在事件入口记录（选路之前，所以同群的每个账号各记一份），发出的消息
从 Entari 的 ``SendResponse`` 记录，覆盖插件回复、宿主通知和面板发送。消息先进
内存缓冲，由后台任务成批写库；数据库不可用时丢弃并告警，不影响消息处理。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from loguru import logger
from satori.element import (
    At,
    Audio,
    Author,
    Br,
    Emoji,
    File,
    Image,
    Link,
    Paragraph,
    Quote,
    Sharp,
    Text,
    Custom,
    Element,
    Message,
    Video,
)

from ..context import QQ_PLATFORM, MessageContext, is_message_created
from .sandbox import is_sandbox

_FLUSH_DELAY = 0.5
_FETCH_TIMEOUT = 5.0
_PRUNE_INTERVAL = 600.0
_BUFFER_LIMIT = 2000
_TEXT_LIMIT = 5000
_OTHER_NAMES = {
    "json": "卡片消息",
    "xml": "卡片消息",
    "ark": "卡片消息",
    "forward": "合并转发",
    "node": "合并转发",
    "poke": "戳一戳",
    "music": "音乐分享",
    "share": "链接分享",
    "markdown": "Markdown 消息",
    "mface": "商城表情",
    "dice": "骰子",
    "rps": "猜拳",
    "contact": "名片",
    "location": "位置",
}


def _other(name: str) -> dict[str, str]:
    return {"type": "other", "name": name}


def segments(
    elements: Iterable[Element | str], *, inline_images: bool = False
) -> list[dict[str, Any]]:
    """把 Satori 消息元素归一化为面板渲染用的消息段。

    只保留文字、@、表情、图片和引用；其他元素以名称占位，未知元素不会原样
    进入数据库或前端。base64 图片（例如渲染出的状态图）不入库，只留占位；
    只存在内存里的沙盒记录用 ``inline_images`` 保留 ``data:image/`` 图片。
    """

    schemes = (
        ("http://", "https://", "data:image/")
        if inline_images
        else ("http://", "https://")
    )

    result: list[dict[str, Any]] = []

    def text(value: str) -> None:
        if not value:
            return
        if result and result[-1]["type"] == "text":
            result[-1]["text"] = (result[-1]["text"] + value)[:_TEXT_LIMIT]
        else:
            result.append({"type": "text", "text": value[:_TEXT_LIMIT]})

    def visit(element: Element | str) -> None:
        if isinstance(element, str):
            text(element)
        elif isinstance(element, Text):
            text(element.text)
        elif isinstance(element, At):
            if element.type in {"all", "here"}:
                result.append({"type": "at", "id": "all", "name": "全体成员"})
            elif element.id is not None:
                result.append(
                    {"type": "at", "id": str(element.id), "name": element.name or ""}
                )
        elif isinstance(element, Emoji):
            result.append(
                {"type": "face", "id": str(element.id), "name": element.name or ""}
            )
        elif isinstance(element, Image):
            src = element.src if element.src.startswith(schemes) else ""
            result.append({"type": "image", "src": src})
        elif isinstance(element, Quote):
            author = next(
                (child for child in element.children if isinstance(child, Author)),
                None,
            )
            body = segments(
                (child for child in element.children if not isinstance(child, Author)),
                inline_images=inline_images,
            )
            result.append(
                {
                    "type": "quote",
                    "id": str(element.id or ""),
                    "user_id": str(author.id) if author else "",
                    "name": (author.name or "") if author else "",
                    "text": preview(body, 60),
                }
            )
        elif isinstance(element, Message):
            result.append(_other("合并转发"))
        elif isinstance(element, Audio):
            result.append(_other("语音"))
        elif isinstance(element, Video):
            result.append(_other("视频"))
        elif isinstance(element, File):
            result.append(_other(f"文件 {element.title}" if element.title else "文件"))
        elif isinstance(element, Br):
            text("\n")
        elif isinstance(element, Paragraph):
            for child in element.children:
                visit(child)
            text("\n")
        elif isinstance(element, Sharp):
            text(f"#{element.name or element.id}")
        elif isinstance(element, Link):
            before = len(result)
            for child in element.children:
                visit(child)
            if len(result) == before:
                text(element.url)
        elif isinstance(element, Author):
            return
        elif isinstance(element, Custom):
            name = element.tag.removeprefix("onebot:")
            result.append(_other(_OTHER_NAMES.get(name, "不支持的消息")))
        elif element.children:
            # 加粗、斜体等样式元素只保留里面的内容。
            for child in element.children:
                visit(child)
        else:
            result.append(_other("不支持的消息"))

    for element in elements:
        visit(element)
    return result


def preview(items: Iterable[dict[str, Any]], limit: int = 80) -> str:
    """消息段的一行摘要，用于会话列表和引用。"""

    parts = []
    for item in items:
        kind = item["type"]
        if kind == "text":
            parts.append(item["text"])
        elif kind == "at":
            parts.append(f"@{item['name'] or item['id']} ")
        elif kind == "face":
            parts.append("[表情]")
        elif kind == "image":
            parts.append("[图片]")
        elif kind == "quote":
            continue
        else:
            parts.append(f"[{item['name']}]")
    line = " ".join("".join(parts).split())
    return line if len(line) <= limit else f"{line[: limit - 1]}…"


def _timestamp(value: object) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    return time.time()


def _name(*candidates: object) -> str:
    for value in candidates:
        if value:
            return str(value)[:128]
    return ""


class ChatLog:
    """缓冲并写入消息记录；``sequence`` 在每次成功写入后递增，供实时推送比对。"""

    def __init__(self) -> None:
        self.repository: Any | None = None
        self.enabled = False
        self.retention_days = 7
        self.max_messages = 50000
        self.sequence = 0
        self._buffer: list[Any] = []
        self._task: asyncio.Task[None] | None = None
        self._fetching: set[asyncio.Task[None]] = set()
        self._last_prune = 0.0
        # 不能写进聊天记录的文字（例如刚生成的面板密码），记录时换成占位符。
        self._hidden: set[str] = set()

    def hide(self, secret: str) -> None:
        """之后记录的出站消息里，把这段文字换成“[已隐藏]”。"""

        if secret:
            self._hidden.add(secret)

    def configure(
        self,
        repository: Any | None,
        *,
        enabled: bool,
        retention_days: int,
        max_messages: int,
    ) -> None:
        self.repository = repository
        self.enabled = enabled and repository is not None
        self.retention_days = retention_days
        self.max_messages = max_messages
        self._buffer = []
        self._fetching = set()
        self._last_prune = 0.0

    def record_event(self, account: Any, event: Any) -> None:
        """记录一条收到的消息事件；不是消息、或缺少会话信息的事件忽略。"""

        if not self.enabled or not is_message_created(event) or is_sandbox(event):
            return
        try:
            context = MessageContext.from_event(event)
        except (AttributeError, TypeError, ValueError):
            return
        if context.chat_type not in {"group", "private"}:
            return
        guild = getattr(event, "guild", None)
        channel = getattr(event, "channel", None)
        member = getattr(event, "member", None)
        user = getattr(event, "user", None)
        outgoing = context.user_id == str(account.self_id)
        user_name = _name(
            getattr(member, "nick", None),
            getattr(user, "nick", None),
            getattr(user, "name", None),
        )
        if context.chat_type == "group":
            channel_name = _name(
                getattr(guild, "name", None), getattr(channel, "name", None)
            )
        else:
            # 私聊的会话名就是对方的昵称。
            channel_name = "" if outgoing else user_name
        self._add(
            platform=context.platform,
            account_id=str(account.self_id),
            chat_type=context.chat_type,
            channel_id=context.channel_id,
            channel_name=channel_name,
            message_id=context.message_id or None,
            outgoing=outgoing,
            user_id=context.user_id,
            user_name=user_name,
            content=segments(event.message.message),
            created_at=_timestamp(getattr(event, "timestamp", None)),
        )

    def record_send(self, response: Any) -> None:
        """记录一次成功的发送（Entari ``SendResponse``）。"""

        if not self.enabled:
            return
        account = getattr(response, "account", None)
        channel_id = str(getattr(response, "channel", "") or "")
        if account is None or not channel_id:
            return
        chat_type = "private" if channel_id.startswith("private:") else "group"
        results = tuple(getattr(response, "result", None) or ())
        message_id = getattr(results[-1], "id", None) if results else None
        origin = getattr(getattr(response, "session", None), "event", None)
        origin_channel = getattr(origin, "channel", None)
        channel_name = ""
        if chat_type == "private" and getattr(origin_channel, "id", None) == channel_id:
            user = getattr(origin, "user", None)
            channel_name = _name(
                getattr(user, "nick", None), getattr(user, "name", None)
            )
        elif chat_type == "group" and getattr(origin_channel, "id", None) == channel_id:
            channel_name = _name(
                getattr(getattr(origin, "guild", None), "name", None),
                getattr(origin_channel, "name", None),
            )
        self_user = getattr(getattr(account, "self_info", None), "user", None)
        content = segments(getattr(response, "message", None) or ())
        for item in content:
            if item["type"] == "text":
                for secret in self._hidden:
                    item["text"] = item["text"].replace(secret, "[已隐藏]")
        fields = dict(
            platform=str(getattr(account, "platform", "") or QQ_PLATFORM),
            account_id=str(account.self_id),
            chat_type=chat_type,
            channel_id=channel_id,
            channel_name=channel_name,
            message_id=None if message_id is None else str(message_id),
            outgoing=True,
            user_id=str(account.self_id),
            user_name=_name(
                getattr(self_user, "nick", None), getattr(self_user, "name", None)
            ),
            created_at=time.time(),
        )
        if message_id is not None and any(
            item["type"] == "image" and not item["src"] for item in content
        ):
            # 插件渲染的图片以 base64 发出，记录里不存图片本身；向协议端取回图片地址。
            task = asyncio.get_running_loop().create_task(
                self._record_fetched(
                    account, channel_id, str(message_id), content, fields
                )
            )
            self._fetching.add(task)
            task.add_done_callback(self._fetching.discard)
            return
        self._add(content=content, **fields)

    async def _record_fetched(
        self,
        account: Any,
        channel_id: str,
        message_id: str,
        content: list[dict[str, Any]],
        fields: dict[str, Any],
    ) -> None:
        try:
            fetched = await asyncio.wait_for(
                account.protocol.message_get(channel_id, message_id), _FETCH_TIMEOUT
            )
            sources = iter(
                item["src"]
                for item in segments(fetched.message)
                if item["type"] == "image" and item["src"]
            )
        except Exception as error:
            logger.debug("Chat log image lookup failed: {}", type(error).__name__)
            sources = iter(())
        # 只补图片地址，文字等仍以发出的内容为准；取不到时保留占位。
        for item in content:
            if item["type"] == "image" and not item["src"]:
                item["src"] = next(sources, "")
        self._add(content=content, **fields)

    def _add(self, *, content: list[dict[str, Any]], **fields: Any) -> None:
        from ..db.repositories import ChatRecord

        if not content:
            return
        if len(self._buffer) >= _BUFFER_LIMIT:
            # 数据库持续写不进去时只保留最新的消息，不让内存无限增长。
            del self._buffer[: len(self._buffer) - _BUFFER_LIMIT + 1]
        self._buffer.append(
            ChatRecord(content=json.dumps(content, ensure_ascii=False), **fields)
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 没有事件循环（同步调用）时等下一次 flush()
        task = self._task
        if task is None or task.done() or task.get_loop() is not loop:
            self._task = loop.create_task(self._flush_soon())

    async def _flush_soon(self) -> None:
        await asyncio.sleep(_FLUSH_DELAY)
        await self.flush()

    async def flush(self) -> None:
        """立即写入缓冲；关闭时由运行时调用。"""

        loop = asyncio.get_running_loop()
        pending = [task for task in self._fetching if task.get_loop() is loop]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # 交换缓冲没有 await，并发的 flush 各写各的一批。
        records, self._buffer = self._buffer, []
        if records and self.repository is not None:
            try:
                await self.repository.add(records)
            except Exception as error:
                logger.warning(
                    "Chat log write failed, {} messages dropped: {}",
                    len(records),
                    type(error).__name__,
                )
            else:
                self.sequence += 1
        if time.monotonic() - self._last_prune >= _PRUNE_INTERVAL:
            await self._prune()

    async def _prune(self) -> None:
        self._last_prune = time.monotonic()
        if self.repository is None:
            return
        cutoff = time.time() - self.retention_days * 86400
        try:
            deleted = await self.repository.prune(cutoff, self.max_messages)
        except Exception as error:
            logger.warning("Chat log cleanup failed: {}", type(error).__name__)
            return
        if deleted:
            logger.info("Chat log cleanup removed {} messages", deleted)


chat_log = ChatLog()

__all__ = ["ChatLog", "chat_log", "preview", "segments"]
