from __future__ import annotations

import asyncio

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from arclet.alconna import Alconna
from arclet.entari import Session
from arclet.entari.plugin import get_plugin
from loguru import logger

from ..context import MessageContext, group_key, group_reference
from .perm import Permission, PermissionChecker
from .sandbox import refuse_in_sandbox
from .plugins.runtime import external_plugin_for_module

_FEATURE_MANAGER_NAMES = frozenset({"feature_manager", "功能开关"})
_RATE_LIMIT_UNAVAILABLE_NOTICE = "限流状态暂不可用，请稍后再试"

if TYPE_CHECKING:
    from ..db.repositories import FeatureStateRepository


def _key(value: object, label: str) -> str:
    if value is None:
        raise ValueError(f"{label}不能为空")
    normalized = str(value)
    if not normalized:
        raise ValueError(f"{label}不能为空")
    return normalized


class FeatureService:
    """维护插件开关和插件维护状态。

    该服务只拥有 Tenko 新宿主的功能状态，不调用 Entari 插件生命周期 API，
    普通插件使用群级开关；需要宿主级开关的插件可以使用可选的
    ``global_enabled`` 字段。同步事件入口读取内存快照，异步生命周期负责从
    repository 加载和刷新快照。
    """

    def __init__(
        self,
        repository: FeatureStateRepository | None = None,
        *,
        default_enabled: bool = True,
    ) -> None:
        if type(default_enabled) is not bool:
            raise TypeError("default_enabled 必须是布尔值")
        self._repository = repository
        self.default_enabled = default_enabled
        # 插件通过 declare(default_enabled=False) 声明的“新群默认关闭”；这是
        # 插件自身的属性而不是数据库状态，所以 configure() 不会清空它。
        self.plugin_defaults: dict[str, bool] = {}
        # 受控插件（#234）：默认关闭，只有 BotAdmin 及以上能在群里打开、在私聊使用。
        # 同 plugin_defaults，来自插件声明，configure() 不清空。
        self.controlled: set[str] = set()
        # Master 在面板上对受控状态的覆盖（TenkoSetting ``controlled-plugins``）。
        self.controlled_overrides: dict[str, bool] = {}
        self._plugins: dict[str, dict[str, Any]] = {}
        self._ready = repository is None
        self._persist_lock = asyncio.Lock()

    def configure(
        self,
        repository: FeatureStateRepository | None = None,
        *,
        default_enabled: bool | None = None,
    ) -> None:
        """配置 repository 和默认值；实际数据库读取由异步初始化完成。"""

        if default_enabled is not None and type(default_enabled) is not bool:
            raise TypeError("default_enabled 必须是布尔值")
        self._repository = repository
        if default_enabled is not None:
            self.default_enabled = default_enabled
        self._plugins = {}
        self._ready = repository is None
        self._persist_lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        """返回数据库快照是否可安全用于命令判定。"""

        return self._ready

    def mark_unavailable(self) -> None:
        """将数据库加载失败转换为功能关闭的保守状态。"""

        self._plugins = {}
        self._ready = False

    async def initialize(
        self, repository: FeatureStateRepository | None = None
    ) -> None:
        """从 repository 加载状态；没有显式状态的插件使用配置默认值。"""

        if repository is not None:
            self._repository = repository
        if self._repository is None:
            self._plugins = {}
            self._ready = True
            return

        try:
            rows = await self._repository.list_states()
        except Exception:
            self.mark_unavailable()
            raise

        plugins: dict[str, dict[str, Any]] = {}
        for row in rows:
            state = plugins.setdefault(
                row.plugin_name,
                {"maintenance": False, "groups": {}},
            )
            if row.group_id is None:
                state["maintenance"] = row.maintenance
                if row.enabled is not None:
                    state["global_enabled"] = row.enabled
            elif row.enabled is not None:
                state["groups"][group_key((row.platform, row.group_id))] = row.enabled
        self._plugins = plugins
        self._ready = True

    def _state_records(self) -> tuple[Any, ...]:
        from ..db.repositories import FeatureStateRecord

        records: list[FeatureStateRecord] = []
        for plugin_name, state in self._plugins.items():
            if "global_enabled" in state or state["maintenance"]:
                records.append(
                    FeatureStateRecord(
                        plugin_name=plugin_name,
                        group_id=None,
                        enabled=state.get("global_enabled"),
                        maintenance=state["maintenance"],
                    )
                )
            records.extend(
                FeatureStateRecord(
                    plugin_name=plugin_name,
                    group_id=group_id,
                    enabled=enabled,
                    maintenance=False,
                    platform=platform,
                )
                for (platform, group_id), enabled in state["groups"].items()
            )
        return tuple(records)

    async def persist_state(self) -> None:
        """将当前快照原子替换到功能状态表。"""

        if self._repository is None:
            if not self._ready:
                from ..db.errors import DatabaseUnavailableError

                raise DatabaseUnavailableError("功能状态数据库不可用")
            return
        if not self._ready:
            from ..db.errors import DatabaseUnavailableError

            raise DatabaseUnavailableError("功能状态数据库尚未就绪")
        # 写失败时保留内存状态；快照整表替换，下一次持久化会补写。
        async with self._persist_lock:
            await self._repository.replace_states(self._state_records())

    def _plugin_state(self, plugin: object) -> dict[str, Any]:
        plugin_name = _key(plugin, "插件名")
        return self._plugins.setdefault(
            plugin_name,
            {"maintenance": False, "groups": {}},
        )

    def is_enabled(self, plugin: str, group: object = None) -> bool:
        """返回插件在指定群是否可执行；未显式设置时使用默认开启值。

        ``group`` 是 ``(平台, 群 ID)`` 或裸 QQ 群号，见 :func:`tenko.context.group_key`。
        """

        plugin = _key(plugin, "插件名")
        state = self._plugins.get(plugin)
        if not self._ready:
            return False
        default_enabled = (
            self.default_enabled
            and self.plugin_defaults.get(plugin, True)
            and not self.is_controlled(plugin)
        )
        if state is None:
            return default_enabled
        if state["maintenance"]:
            return False
        global_enabled = state.get("global_enabled")
        if global_enabled is not None:
            return global_enabled
        if group is not None:
            enabled = state["groups"].get(group_key(group))
            if enabled is not None:
                return enabled
        return default_enabled

    def is_controlled(self, plugin: str) -> bool:
        plugin = _key(plugin, "插件名")
        return self.controlled_overrides.get(plugin, plugin in self.controlled)

    def set_enabled(self, plugin: str, group: object, enabled: bool) -> bool:
        """更新指定群的插件开关；异步调用方随后应刷新 repository。"""

        refuse_in_sandbox("修改插件开关", group)
        if type(enabled) is not bool:
            raise TypeError("enabled 必须是布尔值")
        state = self._plugin_state(plugin)
        state["groups"][group_key(group)] = enabled
        return enabled

    def enable(self, plugin: str, group: object) -> bool:
        return self.set_enabled(plugin, group, True)

    def disable(self, plugin: str, group: object) -> bool:
        return self.set_enabled(plugin, group, False)

    def set_global_enabled(self, plugin: str, enabled: bool) -> bool:
        """更新宿主级插件开关；异步调用方随后应刷新 repository。"""

        refuse_in_sandbox("修改插件全局开关")
        if type(enabled) is not bool:
            raise TypeError("enabled 必须是布尔值")
        self._plugin_state(plugin)["global_enabled"] = enabled
        return enabled

    def reset_global(self, plugin: str) -> None:
        """清除全局覆盖，恢复群级策略与默认值。"""

        refuse_in_sandbox("修改插件全局开关")
        self._plugin_state(plugin).pop("global_enabled", None)

    def set_maintenance(self, plugin: str, maintenance: bool) -> bool:
        """设置全局维护状态；维护中等价于所有群关闭。"""

        refuse_in_sandbox("修改插件维护状态")
        if type(maintenance) is not bool:
            raise TypeError("maintenance 必须是布尔值")
        self._plugin_state(plugin)["maintenance"] = maintenance
        return maintenance

    def is_maintenance(self, plugin: str) -> bool:
        if not self._ready:
            return True
        state = self._plugins.get(_key(plugin, "插件名"))
        return bool(state and state["maintenance"])

    def reset_group(self, plugin: str, group: object) -> None:
        """删除指定群的显式值，使其恢复默认策略。"""

        refuse_in_sandbox("修改插件开关", group)
        state = self._plugins.get(_key(plugin, "插件名"))
        if state is None:
            return
        state["groups"].pop(group_key(group), None)

    @property
    def state(self) -> Mapping[str, Mapping[str, Any]]:
        """返回用于查询/测试的只读快照；群以 :func:`group_reference` 表示。"""

        snapshot = {
            plugin: {
                "maintenance": value["maintenance"],
                "groups": {
                    group_reference(group): enabled
                    for group, enabled in value["groups"].items()
                },
            }
            for plugin, value in self._plugins.items()
        }
        for plugin, value in self._plugins.items():
            if "global_enabled" in value:
                snapshot[plugin]["global_enabled"] = value["global_enabled"]
        return snapshot


feature_service = FeatureService()


def configure_feature_service(
    repository: FeatureStateRepository | None = None,
    *,
    default_enabled: bool = True,
) -> FeatureService:
    """配置全局功能开关服务，返回供运行时注入的同一实例。"""

    feature_service.configure(repository, default_enabled=default_enabled)
    return feature_service


def declare_feature(
    *,
    scope: str = "group",
    default_enabled: bool = True,
    rate_weight: int = 1,
    controlled: bool = False,
) -> None:
    """为正在加载的扩展插件登记 Tenko 专有元数据（``tenko_api.features.declare``）。

    写入宿主已有的 ``feature_scope`` 元数据属性，以及 ``CommandPolicy`` 使用的
    ``rate_weight``；``default_enabled`` 和 ``controlled`` 记入功能服务。
    内置插件没有这个入口，直接在 ``plugin.get_plugin().metadata`` 上设置同名属性，
    与 ``feature_scope`` 的写法一致。不提供 ``classifier``：扩展插件不能把自己
    标记为受保护的宿主插件。
    """

    if scope not in {"group", "global"}:
        raise ValueError(f"scope 必须是 'group' 或 'global': {scope!r}")
    if type(default_enabled) is not bool:
        raise TypeError("default_enabled 必须是布尔值")
    if type(rate_weight) is not int or rate_weight <= 0:
        raise ValueError("rate_weight 必须是正整数")
    if type(controlled) is not bool:
        raise TypeError("controlled 必须是布尔值")
    native = get_plugin(optional=True)
    found = None if native is None else external_plugin_for_module(native.id)
    if native is None or found is None:
        raise RuntimeError("declare() 只能在扩展插件的模块导入期间调用")
    metadata = native.metadata
    metadata.feature_scope = scope
    metadata.rate_weight = rate_weight
    plugin_id = found
    if default_enabled:
        feature_service.plugin_defaults.pop(plugin_id, None)
    else:
        feature_service.plugin_defaults[plugin_id] = False
    if controlled:
        feature_service.controlled.add(plugin_id)
    else:
        feature_service.controlled.discard(plugin_id)


def _plugin_name(owner: object) -> str:
    return _key(getattr(owner, "name", owner), "插件名")


def _plugin_label(owner: object) -> str:
    return _key(
        getattr(owner, "display_name", getattr(owner, "name", owner)),
        "插件名",
    )


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    """一次命令策略判定；``notice`` 非空时在拒绝的同时回复给用户。"""

    allowed: bool = True
    notice: str | None = None
    # 调用统计里的拒绝原因。
    reason: str = ""


_ALLOW = PolicyVerdict()


class CommandPolicy:
    """Entari 命令的统一策略：未登记插件、受控插件、维护、功能开关和限流。

    策略挂在 Entari 的 ``CommandParse`` 事件上（``enforce``）：此时 Entari 已经
    去掉 @bot、前导空白、命令前缀和昵称，并匹配出具体的 Alconna 命令，因此
    无论消息怎样写，命中的都是同一条命令。私聊只检查受控插件，其余只对群聊生效。
    黑名单在更早的事件入口拦截（``MessageEventHandler.should_skip``）。
    限流只记录调试日志，不回复，避免刷屏者借提示继续刷屏。
    """

    def __init__(
        self,
        feature_service: FeatureService,
        rate_limiter: Any | None = None,
        *,
        plugin_runtime: Any | None = None,
        permission_checker: PermissionChecker | None = None,
        command_prefix: str = "/",
        rate_limit_override_permission: int = Permission.GroupAdmin,
    ) -> None:
        self.feature_service = feature_service
        self.rate_limiter = rate_limiter
        self.plugin_runtime = plugin_runtime
        self.permission_checker = permission_checker or PermissionChecker()
        self.command_prefix = _key(command_prefix, "命令前缀")
        self.rate_limit_override_permission = int(rate_limit_override_permission)

    def _owner(self, command: Alconna) -> object | None:
        runtime = self.plugin_runtime
        if runtime is None:
            return None
        return runtime.command_owner(command.command)

    def _disabled_notice(self, owner: object) -> str:
        label = _plugin_label(owner)
        return (
            f"{label}插件已关闭\n"
            f"请使用‘{self.command_prefix}开启 插件编号’来打开插件\n"
            "插件编号请使用‘帮助’获取"
        )

    @staticmethod
    def _maintenance_notice(owner: object) -> str:
        return f"{_plugin_label(owner)}插件正在维护~"

    async def enforce(self, session: Session, command: Alconna) -> bool | None:
        """``CommandParse`` 处理函数：拒绝时返回 ``False`` 阻断命令并按需回复。"""

        # shortcut: Entari 0.19.3 在 CommandParse 返回 False 时不会完成该消息的
        # 解析缓存 future，同一条消息若还有第二个处理同一命令的订阅者会一直等待；
        # 内置插件没有这种重复注册，上游修复后可删除本说明。
        verdict = await self.check(session, command)
        if not verdict.allowed:
            self._count_denial(session, command, verdict.reason)
        if verdict.notice:
            try:
                await session.send(verdict.notice)
            except Exception:
                logger.exception("Failed to send command policy notice")
        return None if verdict.allowed else False

    def _count_denial(self, session: Session, command: Alconna, reason: str) -> None:
        from .stats import call_stats

        try:
            context = MessageContext.from_event(session.event._origin)
        except (AttributeError, ValueError):
            return
        owner = self._owner(command)
        if owner is not None:
            call_stats.denied(
                _plugin_name(owner), str(command.command), context, reason or "other"
            )

    async def check(self, session: Session, command: Alconna) -> PolicyVerdict:
        origin = getattr(session.event, "_origin", None)
        if origin is None:
            return _ALLOW
        try:
            context = MessageContext.from_event(origin)
        except ValueError:
            return _ALLOW
        if context.chat_type not in {"group", "private"}:
            return _ALLOW
        owner = self._owner(command)
        if owner is None:
            return _ALLOW
        plugin_name = _plugin_name(owner)
        if context.chat_type == "private":
            if self.feature_service.is_controlled(
                plugin_name
            ) and not await self.permission_checker.require_perm(
                context, Permission.BotAdmin
            ):
                return PolicyVerdict(
                    False,
                    f"{_plugin_label(owner)}插件在私聊中仅 BOT 管理员可用",
                    "controlled",
                )
            return _ALLOW
        if getattr(owner, "source", None) == "unmanaged":
            logger.warning(
                "Reject command of unregistered plugin: plugin={} group={} command={}",
                getattr(owner, "name", owner),
                context.channel_id,
                command.command,
            )
            return PolicyVerdict(
                False, "该命令所属插件未在 Tenko 登记，已拒绝执行", "unmanaged"
            )
        if not self.feature_service.ready:
            return PolicyVerdict(False, "功能状态暂不可用，请稍后再试", "unavailable")
        if not self.permission_checker.store.ready:
            # 读不到黑名单时不能放行群命令；与功能状态一样整体拒绝。
            return PolicyVerdict(False, "权限状态暂不可用，请稍后再试", "unavailable")
        if (
            plugin_name not in _FEATURE_MANAGER_NAMES
            and self.feature_service.is_maintenance(plugin_name)
        ):
            logger.debug(
                "Ignore plugin command in maintenance mode: plugin={} group={} "
                "command={}",
                plugin_name,
                context.channel_id,
                command.command,
            )
            return PolicyVerdict(False, self._maintenance_notice(owner), "maintenance")
        if (
            plugin_name not in _FEATURE_MANAGER_NAMES
            and not self.feature_service.is_enabled(plugin_name, context.group)
        ):
            logger.debug(
                "Ignore disabled plugin command: plugin={} group={} command={}",
                plugin_name,
                context.channel_id,
                command.command,
            )
            return PolicyVerdict(False, self._disabled_notice(owner), "feature_off")

        if self.rate_limiter is None:
            return _ALLOW
        if not getattr(self.rate_limiter, "ready", True):
            return PolicyVerdict(False, _RATE_LIMIT_UNAVAILABLE_NOTICE, "unavailable")
        override = await self.permission_checker.require_perm(
            context, self.rate_limit_override_permission
        )
        # 插件声明过 rate_weight 才传权重，未声明沿用限流器的默认权重。
        weight = getattr(owner, "rate_weight", None)
        extra = {} if weight is None else {"weight": weight}
        decision = await self.rate_limiter.check_and_persist(
            context.group,
            context.user_id,
            command=plugin_name,
            override=override,
            **extra,
        )
        if not decision.allowed:
            # 静默拒绝：不回复提示，避免刷屏者通过提示继续刷屏。
            logger.debug(
                "Ignore rate-limited plugin command: plugin={} group={} user={}",
                plugin_name,
                context.channel_id,
                context.user_id,
            )
            return PolicyVerdict(False, reason="rate_limited")
        return _ALLOW


__all__ = [
    "CommandPolicy",
    "PolicyVerdict",
    "FeatureService",
    "configure_feature_service",
    "declare_feature",
    "feature_service",
]
