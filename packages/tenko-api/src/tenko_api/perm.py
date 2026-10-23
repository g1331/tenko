"""稳定插件 API：权限等级、权限检查和声明式权限装饰器。

装饰器（``require`` / ``private_only`` / ``group_only``）
是限定命令权限的默认写法，放在 ``@command.on(...)`` 之上或之下均可；要求会登记
在处理函数上，``/帮助 <编号>`` 据此显示命令所需权限。只有权限取决于运行时数据
（例如按参数区分的权限）时才在函数体里用 :func:`require_perm`。
"""

from arclet.entari import Session

from tenko.context import context_from_session
from tenko.host import perm as _perm
from tenko.host.guards import group_only, private_only
from tenko.host.guards import require as _require
from tenko.host.perm import Permission

__all__ = [
    "Permission",
    "get_user_perm",
    "group_only",
    "private_only",
    "require",
    "require_perm",
]


async def get_user_perm(session: Session) -> int:
    """返回消息发送者的权限：``max(当前群的实时身份, 手动授权)``，私聊不看群身份。"""

    return await _perm.get_user_perm(context_from_session(session))


async def require_perm(session: Session, level: int) -> bool:
    """发送者权限不低于 ``level``（可用 :class:`Permission`）时返回 ``True``。"""

    return await _perm.require_perm(context_from_session(session), level)


def require(level: Permission):
    """要求发送者的成员权限不低于 ``level``，否则回复权限不足并停止处理。

    用法见模块说明。黑名单用户的消息在进入插件前就被丢弃，插件不必再判断。
    """

    return _require(level)
