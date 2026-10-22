from __future__ import annotations

import inspect
from collections.abc import Mapping

from arclet.entari import BasicConfModel, MessageChain, Session
from loguru import logger
from satori import Image, Text

from tenko.host.render import RenderService, try_render


class NotifyGroupConfig(BasicConfModel):
    """内置插件共用的 Entari 插件配置：管理通知的群目标，空值表示改发 Master 私聊。"""

    notify_group: str | int | None = None


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
