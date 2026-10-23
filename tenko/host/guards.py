"""声明式权限与功能装饰器（letoderea ``Propagator``），由 ``tenko_api`` 转发。

装饰器可以放在 ``@command.on(...)`` 之上或之下：Entari 在 ``Subscriber`` 创建时
收集函数上的 ``__propagates__``，已创建的 ``Subscriber`` 则直接挂载。检查在 Alconna
匹配（优先级 70）之后执行，所以只有真正命中命令的消息才会收到拒绝回复。

声明的要求同时登记在处理函数的 ``__tenko_requires__`` 上：无论装饰器顺序如何，
``Subscriber.callable_target`` 都是这个函数，``command_requirements`` 据此按命令名
还原要求，供 ``/帮助`` 展示。不放进 ``CommandMeta.extra``：放在函数下面时装饰器
执行时还没有 Alconna 命令可写。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from arclet.entari import Session
from arclet.entari.command.provider import AlconnaSuppiler
from arclet.entari.plugin import get_plugin, get_plugin_subscribers
from arclet.letoderea import STOP, Propagator, propagate
from loguru import logger

from ..context import MessageContext
from .features import feature_service
from .perm import Permission, PermissionChecker
from .plugins.runtime import external_plugin_for_module
from .stats import mark_denied

REQUIRES_ATTR = "__tenko_requires__"
# 测试可以替换它；内置和扩展插件共用同一个检查器（数据库 + Entari superusers）。
permission_checker = PermissionChecker()

_USER_LABELS = {
    Permission.User: "成员",
    Permission.GroupAdmin: "群管理员",
    Permission.GroupOwner: "群主",
    Permission.BotAdmin: "BOT 管理员",
    Permission.Master: "Master",
}
_CHAT_LABELS = {"group": "群聊", "private": "私聊"}


def user_label(level: int) -> str:
    return _USER_LABELS.get(level, str(level))  # type: ignore[call-overload]


def _context(session: Session | None) -> MessageContext | None:
    origin = getattr(getattr(session, "event", None), "_origin", None)
    if origin is None:
        return None
    try:
        return MessageContext.from_event(origin)
    except ValueError:
        return None


async def _reply(session: Session, text: str) -> None:
    try:
        await session.send(text)
    except Exception:
        logger.exception("Failed to send permission notice")


def _record(target: object, kind: str, value: object) -> None:
    target = getattr(target, "callable_target", target)
    requires: dict[str, object] = getattr(target, REQUIRES_ATTR, None) or {}
    if kind in requires:
        raise ValueError(f"同一个处理函数不能重复声明 {kind} 要求")
    # 新字典：被多层装饰的函数不会和别的函数共享可变状态。
    setattr(target, REQUIRES_ATTR, {**requires, kind: value})


class _Guard(Propagator):
    """一个声明式检查：登记要求，并在 Alconna 匹配之后拦截。"""

    kind: ClassVar[str]
    # Alconna 匹配是 70；先查聊天类型，再查用户权限。
    priority: ClassVar[int]
    # 调用统计里的拒绝原因。
    reason: ClassVar[str]

    def __init__(self, value: Any) -> None:
        self.value = value

    def compose(self):
        yield self.check, True, self.priority

    def __call__(self, func):
        _record(func, self.kind, self.value)
        return propagate(self)(func)

    async def check(self, session: Session | None = None):
        context = _context(session)
        if context is None or session is None:
            return STOP
        if not await self.allowed(session, context):
            mark_denied(self.reason)
            return STOP

    async def allowed(self, session: Session, context: MessageContext) -> bool:
        raise NotImplementedError


class _ChatGate(_Guard):
    kind, priority, reason = "chat", 80, "chat"

    async def allowed(self, session, context):
        if context.chat_type == self.value:
            return True
        await _reply(session, f"该指令只能在{_CHAT_LABELS[self.value]}中使用")
        return False


class _UserGate(_Guard):
    kind, priority, reason = "user", 82, "permission"

    def __init__(self, value: Permission, checker: PermissionChecker | None) -> None:
        super().__init__(value)
        self.checker = checker

    async def allowed(self, session, context):
        current = await (self.checker or permission_checker).get_user_perm(context)
        if current >= self.value:
            return True
        await _reply(
            session,
            f"权限不足（当前权限：{user_label(current)} / "
            f"需要权限：{user_label(self.value)}）",
        )
        return False


def require(
    level: Permission, *, checker: PermissionChecker | None = None
) -> Callable[[Any], Any]:
    """要求发送者的成员权限不低于 ``level``，否则回复权限不足并停止处理。

    ``checker`` 只供宿主内部使用：宿主升级插件的 Master 来自独立的
    ``upgrade.superuser_ids``，不能沿用默认检查器。``tenko_api`` 不暴露它。
    """

    return _UserGate(Permission(level), checker)


def private_only() -> Callable[[Any], Any]:
    """仅允许私聊触发，群聊中回复提示。"""

    return _ChatGate("private")


def group_only() -> Callable[[Any], Any]:
    """仅允许群聊触发，私聊中回复提示。"""

    return _ChatGate("group")


def rate_limit(weight: int) -> Callable[[Any], Any]:
    """单独设置这条命令计入限流窗口的权重；``0`` 表示这条命令不受限流。

    只登记在处理函数上，由 ``CommandPolicy`` 在命令解析后按命令名读取，所以
    只对命令处理函数生效，放在 ``@command.on(...)`` 之上或之下都可以。
    """

    if type(weight) is not int or weight < 0:
        raise ValueError("rate_limit 的 weight 必须是非负整数")

    def decorate(func):
        _record(func, "rate", weight)
        return func

    return decorate


def requirement_text(requires: Mapping[str, object]) -> str | None:
    """把处理函数登记的要求格式化为一行帮助文本；没有要求时返回 ``None``。"""

    parts = []
    if "user" in requires:
        parts.append(user_label(requires["user"]))  # type: ignore[arg-type]
    if "chat" in requires:
        parts.append(f"仅{_CHAT_LABELS[requires['chat']]}")  # type: ignore[index]
    return "，".join(parts) or None


def command_requirements(native_plugin: object) -> dict[str, Mapping[str, object]]:
    """按 Alconna 命令名返回插件各命令处理函数声明的要求。"""

    result: dict[str, Mapping[str, object]] = {}
    for subscriber in get_plugin_subscribers(native_plugin):  # type: ignore[arg-type]
        requires = getattr(subscriber.callable_target, REQUIRES_ATTR, None)
        if not requires:
            continue
        try:
            result[subscriber.get_propagator(AlconnaSuppiler).cmd.command] = requires
        except ValueError:
            continue  # 不是命令处理函数
    return result


def feature_required(*, notify: bool = False) -> Callable[[Any], Any]:
    """非命令监听器的功能开关与维护模式检查（``tenko_api.features.feature_required``）。

    所属插件按被装饰函数的模块解析（规则同 ``data_dir()``），只对群聊生效。
    ``notify=False`` 时静默忽略，避免普通消息触发的监听器刷屏。
    """

    def decorate(func):
        target = getattr(func, "callable_target", func)
        plugin_id = external_plugin_for_module(getattr(target, "__module__", ""))
        if plugin_id is None:
            raise RuntimeError(
                "feature_required 只能用于扩展插件自己的模块，且包必须在 "
                "pyproject 的 tenko.plugins 入口中声明"
            )
        native = get_plugin(optional=True)
        gate = _FeatureGate(plugin_id, native, notify)
        return propagate(gate)(func)

    return decorate


class _FeatureGate(Propagator):
    def __init__(self, plugin_id: str, native: object | None, notify: bool) -> None:
        self.plugin_id = plugin_id
        self.native = native
        self.notify = notify

    def compose(self):
        yield self.check, True, 80

    async def check(self, session: Session | None = None):
        context = _context(session)
        if context is None or session is None:
            return
        label = getattr(getattr(self.native, "metadata", None), "name", self.plugin_id)
        if context.chat_type != "group":
            # 受控插件在私聊里只有 BotAdmin 及以上能用，与命令的规则一致。
            if context.chat_type == "private" and feature_service.is_controlled(
                self.plugin_id
            ):
                if not await permission_checker.require_perm(
                    context, Permission.BotAdmin
                ):
                    mark_denied("controlled")
                    return STOP
            return
        if not feature_service.ready:
            notice, reason = "功能状态暂不可用，请稍后再试", "unavailable"
        elif feature_service.is_maintenance(self.plugin_id):
            notice, reason = f"{label}插件正在维护~", "maintenance"
        elif not feature_service.is_enabled(self.plugin_id, context.group):
            notice, reason = f"{label}插件已关闭", "feature_off"
        else:
            return
        mark_denied(reason)
        if self.notify:
            await _reply(session, notice)
        return STOP


__all__ = [
    "REQUIRES_ATTR",
    "command_requirements",
    "feature_required",
    "group_only",
    "private_only",
    "require",
    "requirement_text",
]
