"""测试用的权限检查器：授权只存在内存里；给了 ``master`` 时用它代替 superusers。"""

from __future__ import annotations

from collections.abc import Iterable

from tenko.context import QQ_PLATFORM
from tenko.host.perm import GLOBAL_SCOPE, PermissionChecker, PermissionStore


def make_checker(
    master: str | None = None,
    bot_admins: Iterable[str] = (),
    *,
    platform: str = QQ_PLATFORM,
) -> PermissionChecker:
    store = PermissionStore()
    for user_id in bot_admins:
        store._grants.add((platform, GLOBAL_SCOPE, str(user_id)))
    return PermissionChecker(store, masters=None if master is None else (master,))
