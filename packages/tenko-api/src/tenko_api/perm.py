"""稳定插件 API：权限等级、权限检查和声明式权限装饰器。

装饰器（``require`` / ``require_group`` / ``private_only`` / ``group_only``）
是限定命令权限的默认写法，放在 ``@command.on(...)`` 之上或之下均可；要求会登记
在处理函数上，``/帮助 <编号>`` 据此显示命令所需权限。只有权限取决于运行时数据
（例如按参数区分的权限）时才在函数体里用 :func:`require_perm`。
"""

from tenko.context import MessageContext
from tenko.host import perm as _perm
from tenko.host.guards import group_only, private_only, require_group
from tenko.host.guards import require as _require
from tenko.host.perm import Permission

__all__ = [
    "Permission",
    "get_group_perm",
    "get_user_perm",
    "group_only",
    "private_only",
    "require",
    "require_group",
    "require_perm",
]


async def get_user_perm(ctx: MessageContext) -> int:
    """返回消息发送者在当前群（私聊为全局）的权限数值。"""

    return await _perm.get_user_perm(ctx)


async def get_group_perm(ctx: MessageContext) -> int:
    """返回当前群的群等级数值。"""

    return await _perm.get_group_perm(ctx)


async def require_perm(ctx: MessageContext, level: int) -> bool:
    """发送者权限不低于 ``level``（可用 :class:`Permission`）时返回 ``True``。"""

    return await _perm.require_perm(ctx, level)


def require(level: Permission):
    """要求发送者的成员权限不低于 ``level``，否则回复权限不足并停止处理。

    用法见模块说明；权限数值沿用 :class:`Permission`（含 DB 里的 BotAdmin 和黑名单）。
    """

    return _require(level)
