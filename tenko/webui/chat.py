"""面板聊天：以 bot 账号的身份查看群聊、私聊消息并发送。

消息来自 ``tenko.host.chat_log`` 的记录；发送走 ``ActionService``，与插件发消息
同一条路径，遵守账号停用、能力覆盖和群内禁言。每次发送都由 WebUI 审计中间件
记下目标会话。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from satori.element import At, Element, Image, Quote, Text
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..host.accounts import account_reference
from ..host.actions import ActionServiceError, action_service
from ..host.chat_log import chat_log, preview
from .auth import read_json

if TYPE_CHECKING:
    from .service import WebUIService

PAGE_SIZE = 50
CONVERSATION_LIMIT = 200
IMAGE_LIMIT = 4 * 1024 * 1024
TEXT_LIMIT = 4000
SEGMENT_LIMIT = 30
_IMAGE_URI = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)$")
_ID = re.compile(r"^[0-9]{1,20}$")


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise ValueError(f"{label}无效")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{label}无效")
    return value


def _cursor(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except ValueError as error:
        raise ValueError("消息游标无效") from error
    if number < 0:
        raise ValueError("消息游标无效")
    return number


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _message(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "message_id": record.message_id,
        "outgoing": record.outgoing,
        "user_id": record.user_id,
        "user_name": record.user_name,
        "content": json.loads(record.content),
        "created_at": _iso(record.created_at),
    }


def build_message(content: object) -> list[Element]:
    """把面板提交的消息段转成 Satori 消息；只接受文字、@、图片和回复。"""

    if not isinstance(content, list) or not 0 < len(content) <= SEGMENT_LIMIT:
        raise ValueError("消息内容不能为空")
    elements: list[Element] = []
    text_length = 0
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            raise ValueError("消息段格式无效")
        kind = item.get("type")
        if kind == "text":
            text = item.get("text")
            if not isinstance(text, str):
                raise ValueError("文字消息段格式无效")
            text_length += len(text)
            if text:
                elements.append(Text(text))
        elif kind == "at":
            target = item.get("id")
            if target == "all":
                elements.append(At.all())
            elif isinstance(target, str) and _ID.match(target):
                # 名字只用于聊天记录显示，协议端发送时按 ID @。
                name = item.get("name")
                elements.append(
                    At(target, name[:64] if isinstance(name, str) and name else None)
                )
            else:
                raise ValueError("@ 的对象无效")
        elif kind == "image":
            source = item.get("src")
            matched = _IMAGE_URI.match(source) if isinstance(source, str) else None
            if matched is None:
                raise ValueError("只能发送 PNG、JPEG、GIF 或 WebP 图片")
            try:
                size = len(base64.b64decode(matched[2], validate=True))
            except (binascii.Error, ValueError) as error:
                raise ValueError("图片数据无效") from error
            if size > IMAGE_LIMIT:
                raise ValueError("图片不能超过 4 MiB")
            elements.append(Image(src=source))
        elif kind == "quote":
            target = item.get("id")
            if index != 0 or not isinstance(target, str) or not target:
                raise ValueError("回复只能放在消息开头")
            elements.append(Quote(_identifier(target, "回复的消息")))
        else:
            raise ValueError("不支持的消息段")
    if text_length > TEXT_LIMIT:
        raise ValueError(f"文字不能超过 {TEXT_LIMIT} 字")
    if not any(not isinstance(element, Quote) for element in elements):
        raise ValueError("消息内容不能为空")
    # 传元素而不是 XML 字符串：str_as_message 关闭后，字符串会被当作纯文本转义。
    return elements


class ChatPanel:
    def __init__(self, service: WebUIService) -> None:
        self.service = service
        self.actions = action_service

    def routes(self) -> tuple[tuple[str, list[str], Any], ...]:
        return (
            ("/api/manage/chat/conversations", ["GET"], self.conversations),
            ("/api/manage/chat/messages", ["GET"], self.messages),
            ("/api/manage/chat/send", ["POST"], self.send),
        )

    def _account(self, params: Any) -> tuple[str, str]:
        platform = _identifier(params.get("platform"), "平台")
        account_id = _identifier(params.get("account"), "账号")
        key = (platform, account_id)
        if self.service.accounts.get(key) is None:
            raise KeyError(key)
        return key

    async def conversations(self, request: Request) -> JSONResponse:
        async def read():
            key = self._account(request.query_params)
            accounts = self.service.accounts
            names = await self.service._group_names()
            latest = (
                await chat_log.repository.conversations(*key, limit=CONVERSATION_LIMIT)
                if chat_log.enabled
                else ()
            )
            rows: dict[str, dict[str, Any]] = {}
            for record in latest:
                group = record.chat_type == "group"
                content = json.loads(record.content)
                rows[record.channel_id] = {
                    "channel_id": record.channel_id,
                    "chat_type": record.chat_type,
                    "peer_id": record.channel_id
                    if group
                    else record.channel_id.removeprefix("private:"),
                    "name": (names.get(record.channel_id) if group else None)
                    or record.channel_name,
                    "last": {
                        "preview": preview(content, 60),
                        "user_name": record.user_name,
                        "outgoing": record.outgoing,
                        "created_at": _iso(record.created_at),
                    },
                }
            for group_id in accounts.groups_for_account(key):
                rows.setdefault(
                    group_id,
                    {
                        "channel_id": group_id,
                        "chat_type": "group",
                        "peer_id": group_id,
                        "name": names.get(group_id, ""),
                        "last": None,
                    },
                )
            for row in rows.values():
                row["muted"] = row["chat_type"] == "group" and accounts.is_muted(
                    key, row["channel_id"]
                )
            return {
                "enabled": chat_log.enabled,
                "retention_days": chat_log.retention_days,
                "online": accounts.is_available(key),
                "conversations": list(rows.values()),
            }

        return await self.service._manage(request, read, lock=False)

    async def messages(self, request: Request) -> JSONResponse:
        async def read():
            params = request.query_params
            key = self._account(params)
            channel = _identifier(params.get("channel"), "会话")
            before = _cursor(params.get("before"))
            after = _cursor(params.get("after"))
            if not chat_log.enabled:
                return {"messages": [], "has_more": False}
            records = await chat_log.repository.messages(
                *key, channel, before=before, after=after, limit=PAGE_SIZE + 1
            )
            has_more = len(records) > PAGE_SIZE
            if has_more:
                # 往前翻时多取的一条在最前面，增量读取时在最后面。
                records = records[:PAGE_SIZE] if after is not None else records[1:]
            messages = [_message(row) for row in records]
            await self._fill_quotes(key, channel, messages)
            return {"messages": messages, "has_more": has_more}

        return await self.service._manage(request, read, lock=False)

    async def _fill_quotes(
        self, key: tuple[str, str], channel: str, messages: list[dict[str, Any]]
    ) -> None:
        # bot 发出的回复只带被引用消息的 ID；从同一会话的记录里补上作者和摘要。
        quotes = [
            segment
            for message in messages
            for segment in message["content"]
            if segment.get("type") == "quote"
            and segment.get("id")
            and not segment.get("text")
        ]
        if not quotes:
            return
        found = {
            record.message_id: record
            for record in await chat_log.repository.find(
                *key, channel, (segment["id"] for segment in quotes)
            )
        }
        for segment in quotes:
            record = found.get(segment["id"])
            if record is not None:
                segment["user_id"] = record.user_id
                segment["name"] = record.user_name
                segment["text"] = preview(json.loads(record.content), 60)

    async def send(self, request: Request) -> JSONResponse:
        async def action():
            body = await read_json(request, limit=6 * 1024 * 1024)
            if set(body) - {"platform", "account", "channel", "content"}:
                raise ValueError("未知的发送参数")
            key = self._account(body)
            channel = _identifier(body.get("channel"), "会话")
            message = build_message(body.get("content"))
            request.state.webui_action = f"chat-send {key[1]} -> {channel}"
            accounts = self.service.accounts
            reference = account_reference(key)
            try:
                if channel.startswith("private:"):
                    user_id = channel.removeprefix("private:")
                    if not _ID.match(user_id):
                        raise ValueError("私聊对象无效")
                    await self.actions.send_private_message(reference, user_id, message)
                else:
                    if channel not in accounts.groups_for_account(key):
                        raise ValueError("账号不在这个群里")
                    if accounts.is_muted(key, channel):
                        raise RuntimeError("账号在这个群被禁言，暂时不能发言")
                    await self.actions.send_group_message(
                        reference, channel, message, context=None, system=True
                    )
            except ActionServiceError as error:
                raise RuntimeError(f"发送失败：{str(error)[:200]}") from error
            # 发出的消息由 SendResponse 记录；这里立即写库，前端拉取时就能看到。
            await chat_log.flush()
            return {"sent": True}

        # 发送不改动面板状态，不占用写锁，避免慢请求挡住其他管理操作。
        return await self.service._manage(request, action, lock=False)


__all__ = ["ChatPanel", "build_message"]
