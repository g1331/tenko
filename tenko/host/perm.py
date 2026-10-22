from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping
from enum import IntEnum
from typing import Any

from arclet.entari.config import EntariConfig

from ..context import MessageContext
from ..db.errors import is_database_unavailable
from .sandbox import SANDBOX_MASTER_ID, SANDBOX_PLATFORM
from loguru import logger


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
    values = (str(value).strip() for value in configured.get(platform, ()))
    return tuple(value for value in values if value)


def master_id_for_account(account: object, permission_checker: object) -> str | None:
    """按现有通知策略解析账号对应的第一个 Master。"""

    registry = getattr(permission_checker, "registry", None)
    configured_master = str(getattr(registry, "master_id", None) or "").strip()
    if configured_master:
        return configured_master
    return next(iter(superuser_ids(getattr(account, "platform", "onebot"))), None)


class Permission(IntEnum):
    """Tenko 侧的成员权限数值，保持旧 `core.control.Permission` 的协议。"""

    GlobalBlack = -1
    GroupBlack = 0
    User = 16
    Member = 16
    GroupAdmin = 32
    Administrator = 32
    GroupOwner = 64
    Owner = 64
    BotAdmin = 128
    Master = 256
    InactiveGroup = 0
    ActiveGroup = 1
    VipGroup = 2
    TestGroup = 3


PermissionLevel = Permission


class GroupPermission(IntEnum):
    """Tenko 侧的群等级数值，保持旧 `core.control.Permission` 的协议。"""

    InactiveGroup = 0
    ActiveGroup = 1
    VipGroup = 2
    TestGroup = 3


GroupLevel = GroupPermission

_ROLE_LEVELS = {
    "member": Permission.User,
    "admin": Permission.GroupAdmin,
    "administrator": Permission.GroupAdmin,
    "owner": Permission.GroupOwner,
}


def _key(value: object) -> str:
    if value is None:
        raise ValueError("权限主体 ID 不能为空")
    return str(value)


def _scalar(result: object) -> object | None:
    if result is None:
        return None
    if isinstance(result, str | bytes | int | float):
        return result
    if isinstance(result, dict):
        if "perm" in result:
            return result["perm"]
        if len(result) == 1:
            return next(iter(result.values()))
    if isinstance(result, tuple | list):
        return result[0] if result else None
    mapping = getattr(result, "_mapping", None)
    if mapping is not None:
        if "perm" in mapping:
            return mapping["perm"]
        if len(mapping) == 1:
            return next(iter(mapping.values()))
    try:
        return result[0]  # SQLAlchemy Row 和类似的行对象。
    except (IndexError, KeyError, TypeError):
        return result


def _level(result: object) -> int | None:
    value = _scalar(result)
    if value is None:
        return None
    return int(value)


class PermissionRegistry:
    """保存宿主侧身份配置和无需数据库的权限覆盖。

    该注册表隔离 Satori `MessageContext` 与旧数据库/配置来源：它只维护
    Tenko 运行时明确提供的 master、BotAdmin、成员和群等级，不复制
    `core.control` 的 Graia 事件注入协议。`PermissionChecker` 会优先读取
    只读数据库记录，再使用这里的值作为没有数据库记录时的运行时来源。
    """

    def __init__(
        self,
        *,
        master_id: str | int | None = None,
        bot_admin_ids: Iterable[str | int] = (),
    ) -> None:
        self.master_id = None if master_id is None else _key(master_id)
        self.bot_admin_ids = {_key(user_id) for user_id in bot_admin_ids}
        self._user_levels: dict[tuple[str, str], int] = {}
        self._group_levels: dict[str, int] = {}

    def set_user_level(
        self, group_id: str | int | None, user_id: str | int, level: int
    ) -> None:
        """设置群内或全局（`group_id=0`）的运行时成员权限。"""

        normalized_group = "0" if group_id is None else _key(group_id)
        self._user_levels[(normalized_group, _key(user_id))] = int(level)

    def set_group_level(self, group_id: str | int, level: int) -> None:
        """设置群的运行时等级覆盖。"""

        self._group_levels[_key(group_id)] = int(level)

    def user_level(self, group_id: str | int | None, user_id: str | int) -> int | None:
        normalized_group = "0" if group_id is None else _key(group_id)
        return self._user_levels.get((normalized_group, _key(user_id)))

    def group_level(self, group_id: str | int) -> int | None:
        return self._group_levels.get(_key(group_id))


class PermissionChecker:
    """基于消息上下文执行成员和群权限判断。

    `database` 需要提供 ``get_member_permission`` 和 ``get_group_permission``
    异步方法；全局 BotAdmin 就是 ``group_id=0`` 的 128 记录。未显式注入
    数据库且未提供注册表时，实际检查才延迟导入 Tenko repository；本类只调用读取方法，不执行写入。
    """

    def __init__(
        self,
        registry: PermissionRegistry | None = None,
        database: Any | None = None,
    ) -> None:
        self.registry = registry or PermissionRegistry()
        self._database_enabled = database is not None or registry is None
        self._database = database
        self._database_failing = False

    def _get_database(self) -> Any:
        if self._database is None and self._database_enabled:
            from ..db import repositories

            self._database = repositories
        if self._database is None:
            raise RuntimeError("权限检查未配置数据库或运行时权限来源")
        return self._database

    def _record_database_failure(self, error: BaseException) -> None:
        """每次故障周期只告警一次；下一次读取成功即视为恢复。"""

        if self._database_failing:
            return
        self._database_failing = True
        logger.warning(
            "权限数据库不可用，将按失败关闭处理（用户视为全局黑名单、群视为"
            f"未启用），数据库恢复后自动生效；原因：{type(error).__name__}: {error}"
        )

    async def _call_database(self, method_name: str, *values: object) -> object:
        method = getattr(self._get_database(), method_name)
        result = method(*values)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _database_member_level(
        self, group_id: str | int, user_id: str | int
    ) -> int | None:
        try:
            result = await self._call_database(
                "get_member_permission", group_id, user_id
            )
        except Exception as error:
            if not is_database_unavailable(error):
                raise
            self._record_database_failure(error)
            # 读不到数据库时无法确认该用户是否在黑名单，回退到角色权限会让
            # 黑名单用户重新获得访问；与健康数据库可能拒绝的结果保持一致，
            # 按黑名单处理。
            return Permission.GlobalBlack
        self._database_failing = False
        return _level(result)

    async def _database_group_level(self, group_id: str | int) -> int | None:
        try:
            result = await self._call_database("get_group_permission", group_id)
        except Exception as error:
            if not is_database_unavailable(error):
                raise
            self._record_database_failure(error)
            # 同上：无法确认群是否被停用，按未启用处理，不放行。
            return GroupPermission.InactiveGroup
        self._database_failing = False
        return _level(result)

    async def get_user_perm(self, context: MessageContext) -> int:
        """获取上下文发送者的有效成员权限数值。"""

        user_id = _key(context.user_id)
        if user_id in superuser_ids(context.platform):
            return Permission.Master
        global_level: int | None = None
        if self._database_enabled:
            global_level = await self._database_member_level(0, user_id)
        if global_level is not None:
            return global_level

        group_id = context.channel_id if context.chat_type == "group" else None
        global_registry_level = self.registry.user_level(None, user_id)
        if global_registry_level is not None:
            return global_registry_level
        explicit_level = self.registry.user_level(group_id, user_id)
        if context.chat_type == "group" and self._database_enabled:
            explicit_level = await self._database_member_level(group_id, user_id)
        if explicit_level is not None:
            return explicit_level

        if self.registry.master_id == user_id:
            return Permission.Master
        if user_id in self.registry.bot_admin_ids:
            return Permission.BotAdmin

        if context.chat_type == "group":
            role = (context.member_role or "member").lower()
            return _ROLE_LEVELS.get(role, Permission.User)
        return Permission.User

    async def get_group_perm(self, context: MessageContext) -> int:
        """获取上下文所属群等级；私聊没有群约束，返回正常群默认值。"""

        if context.chat_type != "group":
            return GroupPermission.ActiveGroup
        group_id = context.channel_id
        if self._database_enabled:
            # 数据库记录优先；读取失败时返回 InactiveGroup（见 _database_group_level）。
            database_level = await self._database_group_level(group_id)
            if database_level is not None:
                return database_level
        registry_level = self.registry.group_level(group_id)
        return GroupPermission.ActiveGroup if registry_level is None else registry_level

    async def require_perm(self, context: MessageContext, level: int) -> bool:
        """返回发送者是否达到指定成员权限；不足时返回 False，不抛事件异常。"""

        return await self.get_user_perm(context) >= int(level)

    async def require_group_perm(self, context: MessageContext, level: int) -> bool:
        """返回上下文群等级是否达到阈值；私聊按旧宿主语义跳过群限制。"""

        if context.chat_type != "group":
            return True
        return await self.get_group_perm(context) >= int(level)


async def get_user_perm(
    context: MessageContext, checker: PermissionChecker | None = None
) -> int:
    """使用指定或默认检查器获取成员权限。"""

    return await (checker or PermissionChecker()).get_user_perm(context)


async def get_group_perm(
    context: MessageContext, checker: PermissionChecker | None = None
) -> int:
    """使用指定或默认检查器获取群等级。"""

    return await (checker or PermissionChecker()).get_group_perm(context)


async def require_perm(
    context: MessageContext,
    level: int,
    checker: PermissionChecker | None = None,
) -> bool:
    """Tenko 的 awaitable 成员权限检查入口。"""

    return await (checker or PermissionChecker()).require_perm(context, level)


async def require_group_perm(
    context: MessageContext,
    level: int,
    checker: PermissionChecker | None = None,
) -> bool:
    """Tenko 的 awaitable 群等级检查入口。"""

    return await (checker or PermissionChecker()).require_group_perm(context, level)
