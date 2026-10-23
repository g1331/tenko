"""成员权限、全局黑名单、群黑名单和手动授权（#234）。

成员权限 = ``max(实时群身份, 手动授权)``：群主、群管理取消息事件里的实时身份，
只有手动授予的部分（本群群管理、全局 BotAdmin）落库。黑名单与权限等级分开，
由事件入口统一拦截，不参与等级比较。所有键都是 ``(平台, ID)``。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any, Literal

from arclet.entari.config import EntariConfig

from ..context import LEGACY_QQ_PLATFORM, QQ_PLATFORM, MessageContext
from ..context import normalize_platform
from .sandbox import SANDBOX_MASTER_ID, SANDBOX_PLATFORM

BlacklistKind = Literal["user", "group"]
# 手动授权的范围：空串是全局（BotAdmin），否则是群号（该群的群管理）。
GLOBAL_SCOPE = ""


def superuser_ids(platform: str | None) -> tuple[str, ...]:
    """返回 Entari ``basic.superusers`` 中该平台配置的用户 ID（去空白、去空值）。"""

    if platform == SANDBOX_PLATFORM:
        # 沙盒选择 Master 身份时使用的虚拟 ID。
        return (SANDBOX_MASTER_ID,)
    if platform is None or not getattr(EntariConfig, "_inited", False):
        return ()
    configured = getattr(
        getattr(EntariConfig.instance, "basic", None), "superusers", None
    )
    if not isinstance(configured, Mapping):
        return ()
    values = [str(value).strip() for value in configured.get(platform, ())]
    if platform == QQ_PLATFORM:
        # 旧配置写作 superusers = { onebot = [...] }。
        values += (
            str(value).strip() for value in configured.get(LEGACY_QQ_PLATFORM, ())
        )
    return tuple(value for value in values if value)


class Permission(IntEnum):
    """成员权限等级，只用于比较大小；插件一律用名字，不要写数值。"""

    User = 0
    GroupAdmin = 1
    GroupOwner = 2
    BotAdmin = 3
    Master = 4


PermissionLevel = Permission

_ROLE_LEVELS = {
    "member": Permission.User,
    "admin": Permission.GroupAdmin,
    "administrator": Permission.GroupAdmin,
    "owner": Permission.GroupOwner,
}


def role_level(role: str | None) -> Permission:
    """把 ``MessageContext.member_role`` 映射为权限；认不出的角色一律算成员。"""

    return _ROLE_LEVELS.get((role or "member").lower(), Permission.User)


class PermissionStore:
    """黑名单和手动授权的内存快照。

    启动时整表读入；事件入口的同步过滤和权限判断只读内存。写入先落库再改内存，
    落库失败时抛出 ``DatabaseUnavailableError``，内存保持原样。没有配置 repository
    （测试、数据库关闭）时只改内存。快照没加载成功时视为空，命令由
    ``CommandPolicy`` 统一拒绝。
    """

    def __init__(self, repository: Any | None = None) -> None:
        self.configure(repository)

    def configure(self, repository: Any | None = None) -> None:
        self._repository = repository
        self._lists: dict[BlacklistKind, dict[tuple[str, str], Any]] = {
            "user": {},
            "group": {},
        }
        self._grants: set[tuple[str, str, str]] = set()
        self._ready = repository is None

    @property
    def ready(self) -> bool:
        return self._ready

    def mark_unavailable(self) -> None:
        self.configure(self._repository)
        self._ready = False

    async def initialize(self) -> None:
        if self._repository is None:
            self._ready = True
            return
        try:
            snapshot = await self._repository.load_state()
        except Exception:
            self.mark_unavailable()
            raise
        self._lists = {
            "user": {(r.platform, r.target_id): r for r in snapshot.users},
            "group": {(r.platform, r.target_id): r for r in snapshot.groups},
        }
        self._grants = set(snapshot.grants)
        self._ready = True

    def blacklisted(self, kind: BlacklistKind, platform: str, target_id: str) -> bool:
        key = (normalize_platform(platform), str(target_id))
        return key in self._lists[kind]

    def blacklist(self, kind: BlacklistKind) -> tuple[Any, ...]:
        """按加入时间排列的黑名单记录（``BlacklistRecord``）。"""

        return tuple(
            sorted(
                self._lists[kind].values(),
                key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC),
            )
        )

    async def add_blacklist(
        self,
        kind: BlacklistKind,
        platform: str,
        target_id: str,
        *,
        reason: str = "",
        operator_id: str = "",
    ) -> bool:
        """加入黑名单；已在名单里时返回 ``False``，不改原因。"""

        from ..db.repositories import BlacklistRecord

        key = (normalize_platform(platform), str(target_id))
        if key in self._lists[kind]:
            return False
        record = BlacklistRecord(
            *key, reason, operator_id, datetime.now(UTC).replace(microsecond=0)
        )
        if self._repository is not None:
            await self._repository.add_blacklist(kind, record)
        self._lists[kind][key] = record
        return True

    async def remove_blacklist(
        self, kind: BlacklistKind, platform: str, target_id: str
    ) -> bool:
        key = (normalize_platform(platform), str(target_id))
        if key not in self._lists[kind]:
            return False
        if self._repository is not None:
            await self._repository.remove_blacklist(kind, *key)
        del self._lists[kind][key]
        return True

    def granted(self, platform: str, scope: str, user_id: str) -> bool:
        return (normalize_platform(platform), str(scope), str(user_id)) in self._grants

    def grants(self, platform: str, scope: str) -> tuple[str, ...]:
        """该平台某个范围内被授权的用户 ID，按 ID 排序。"""

        platform = normalize_platform(platform)
        return tuple(
            sorted(u for p, s, u in self._grants if p == platform and s == scope)
        )

    async def grant(self, platform: str, scope: str, user_id: str) -> bool:
        key = (normalize_platform(platform), str(scope), str(user_id))
        if key in self._grants:
            return False
        if self._repository is not None:
            await self._repository.add_grant(*key)
        self._grants.add(key)
        return True

    async def revoke(self, platform: str, scope: str, user_id: str) -> bool:
        key = (normalize_platform(platform), str(scope), str(user_id))
        if key not in self._grants:
            return False
        if self._repository is not None:
            await self._repository.remove_grant(*key)
        self._grants.discard(key)
        return True


permission_store = PermissionStore()


class PermissionChecker:
    """按消息上下文计算成员权限。

    ``masters`` 只给宿主升级插件用：它的 Master 来自独立的
    ``upgrade.superuser_ids``，此时用它代替 ``basic.superusers``。
    """

    def __init__(
        self,
        store: PermissionStore | None = None,
        *,
        masters: Iterable[str | int] | None = None,
    ) -> None:
        self.store = store or permission_store
        self.masters = None if masters is None else {str(m) for m in masters}

    def level(self, context: MessageContext) -> Permission:
        user_id = str(context.user_id)
        group = context.chat_type == "group"
        live = role_level(context.member_role) if group else Permission.User
        masters = (
            superuser_ids(context.platform) if self.masters is None else self.masters
        )
        if user_id in masters:
            return Permission.Master
        if self.store.granted(context.platform, GLOBAL_SCOPE, user_id):
            return Permission.BotAdmin
        if group and self.store.granted(context.platform, context.channel_id, user_id):
            return max(live, Permission.GroupAdmin)
        return live

    async def get_user_perm(self, context: MessageContext) -> int:
        """获取上下文发送者的有效成员权限。"""

        return self.level(context)

    async def require_perm(self, context: MessageContext, level: int) -> bool:
        """返回发送者是否达到指定成员权限；不足时返回 False，不抛事件异常。"""

        return self.level(context) >= int(level)


async def get_user_perm(
    context: MessageContext, checker: PermissionChecker | None = None
) -> int:
    """使用指定或默认检查器获取成员权限。"""

    return await (checker or PermissionChecker()).get_user_perm(context)


async def require_perm(
    context: MessageContext,
    level: int,
    checker: PermissionChecker | None = None,
) -> bool:
    """Tenko 的 awaitable 成员权限检查入口。"""

    return await (checker or PermissionChecker()).require_perm(context, level)
