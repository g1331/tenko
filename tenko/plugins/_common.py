from __future__ import annotations

from collections.abc import Mapping

from arclet.entari import MessageChain
from satori import Image

from tenko.host.render import RenderService, try_render


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
