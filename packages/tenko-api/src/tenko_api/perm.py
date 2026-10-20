"""稳定插件 API：权限等级与权限检查。"""

from tenko.context import MessageContext
from tenko.host import perm as _perm
from tenko.host.perm import Permission

__all__ = ["Permission", "get_group_perm", "get_user_perm", "require_perm"]


async def get_user_perm(ctx: MessageContext) -> int:
    """返回消息发送者在当前群（私聊为全局）的权限数值。"""

    return await _perm.get_user_perm(ctx)


async def get_group_perm(ctx: MessageContext) -> int:
    """返回当前群的群等级数值。"""

    return await _perm.get_group_perm(ctx)


async def require_perm(ctx: MessageContext, level: int) -> bool:
    """发送者权限不低于 ``level``（可用 :class:`Permission`）时返回 ``True``。"""

    return await _perm.require_perm(ctx, level)
