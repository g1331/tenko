"""群聊命令回复默认引用触发消息（issue #224）。

挂在 Entari 的 ``SendRequest``（``before_send``）上：``Session.send`` 发出的回复会
带上 ``referrer["source"]``，Entari 据此在 ``SendRequest.session`` 里给出触发事件；
主动发送（``action_service``、启动通知、公告、定时任务）直接调用协议方法，没有
session，因此不会被引用。插件无需任何改动；要让某条回复不带引用，直接用
``account.protocol.send_message``（不经 session）发送。
"""

from __future__ import annotations

from arclet.entari import MessageChain, SendRequest
from satori import Quote
from satori.element import Message

from ..context import MessageContext, is_message_created


async def quote_group_reply(event: SendRequest) -> MessageChain | None:
    """给群聊里对触发消息的回复补上 ``Quote``，其他发送保持原样。"""

    source = getattr(event.session, "event", None)
    origin = getattr(source, "_origin", source)
    if origin is None or not is_message_created(origin):
        return None
    try:
        context = MessageContext.from_event(origin)
    except (AttributeError, ValueError):
        return None
    if context.chat_type != "group" or event.channel != context.channel_id:
        return None
    # Quote 和合并转发（``<message forward>``）同属 Satori 的 Message 元素：
    # 已经带引用就不重复，转发/多条消息的结构加前缀会破坏发送。
    if any(isinstance(element, Message) for element in event.message):
        return None
    return MessageChain([Quote(context.message_id), *event.message])
