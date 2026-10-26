from __future__ import annotations

import re
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from arclet.entari.config import EntariConfig
from loguru import logger
from satori import At, ChannelType, EventType, Quote, Text
from satori.client import Account
from satori.model import Event

from .config import DebugConfig
from .context import MessageContext, is_message_created
from .host.accounts import AccountRegistry, account_key
from .host.sandbox import is_sandbox
from .host.chat_log import ChatLog
from .host.metrics import MessageMetrics, message_metrics
from .host.perm import PermissionStore, permission_store


def _event_group_id(event: Event) -> str | None:
    """从标准 Satori 字段或 OneBot 原始数据中提取群 ID。"""

    guild = getattr(event, "guild", None)
    if (group_id := getattr(guild, "id", None)) is not None:
        return str(group_id)

    raw_data = getattr(event, "_data", None)
    if (
        isinstance(raw_data, Mapping)
        and (group_id := raw_data.get("group_id")) is not None
    ):
        return str(group_id)

    channel = getattr(event, "channel", None)
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return None
    protocol_type = str(getattr(event, "_type", "") or "")
    if ".group." in protocol_type or getattr(channel, "type", None) == ChannelType.TEXT:
        return str(channel_id)
    return None


def _event_user_id(event: Event) -> str | None:
    user = getattr(event, "user", None)
    user_id = getattr(user, "id", None)
    if user_id is not None:
        return str(user_id)
    raw_data = getattr(event, "_data", None)
    if (
        isinstance(raw_data, Mapping)
        and (user_id := raw_data.get("user_id")) is not None
    ):
        return str(user_id)
    return None


def _event_message_id(event: Event) -> str | None:
    message = getattr(event, "message", None)
    message_id = getattr(message, "id", None)
    return None if message_id is None else str(message_id)


def _event_route_id(event: Event) -> str | None:
    """同一条群消息在各账号上相同的标识，多账号据此得出同一个选路结果。

    OneBot 的 ``message_id`` 是协议端本地编号，同一条消息在两个账号上不同；
    NapCat 的 ``real_seq`` 是 QQ 服务端的群消息序号，各账号一致。
    """

    raw_data = getattr(event, "_data", None)
    if isinstance(raw_data, Mapping) and raw_data.get("real_seq") not in (None, ""):
        return f"seq:{raw_data['real_seq']}"
    # shortcut: 没有 real_seq 的协议端仍按本地消息 ID 选路，多账号同群可能重复或漏回；
    # 接入这类协议端时改用服务端序号或（发送者, 时间）指纹。
    return _event_message_id(event)


def _leading_mention(event: Event) -> str | None:
    """消息开头（引用之后）@ 的账号 ID，与 Entari 判断 “@ 我” 的位置一致。"""

    try:
        elements = [
            item
            for item in event.message.message  # type: ignore[union-attr]
            if not isinstance(item, Quote)
        ]
    except (AttributeError, TypeError):
        return None
    if elements and isinstance(elements[0], At) and elements[0].id:
        return str(elements[0].id)
    return None


def _event_type_value(event: Event) -> str:
    event_type = getattr(event, "type", "")
    return str(getattr(event_type, "value", event_type))


_MEMBER_REMOVAL_EVENT_TYPES = {
    EventType.GUILD_MEMBER_REMOVED.value,
    EventType.GUILD_REMOVED.value,
}
_MEMBER_REMOVAL_PROTOCOL_TYPES = {
    "notice.group_decrease.leave",
    "notice.group_decrease.kick",
    "notice.group_decrease.kick_me",
}
_GUILD_INVITE_EVENT_TYPES = {EventType.GUILD_REQUEST.value}
_GUILD_INVITE_PROTOCOL_TYPES = {"request.group.invite"}
# satori 的 onebot11 适配器把加群申请报成 GUILD_MEMBER_ADDED，只能按协议类型认。
_MEMBER_JOIN_REQUEST_PROTOCOL_TYPES = {"request.group.add"}
_RECEIVED_EVENT_CACHE_SIZE = 8192


def _is_member_removed_event(event: Event) -> bool:
    return (
        _event_type_value(event) in _MEMBER_REMOVAL_EVENT_TYPES
        or str(getattr(event, "_type", "") or "") in _MEMBER_REMOVAL_PROTOCOL_TYPES
    )


def _is_guild_invite_request_event(event: Event) -> bool:
    return (
        _event_type_value(event) in _GUILD_INVITE_EVENT_TYPES
        or str(getattr(event, "_type", "") or "") in _GUILD_INVITE_PROTOCOL_TYPES
    )


def _is_member_join_request_event(event: Event) -> bool:
    return (
        _event_type_value(event) == EventType.GUILD_MEMBER_REQUEST.value
        or str(getattr(event, "_type", "") or "") in _MEMBER_JOIN_REQUEST_PROTOCOL_TYPES
    )


@dataclass(slots=True)
class MessageEventHandler:
    """最小消息闭环及事件入口过滤处理器。"""

    account_registry: AccountRegistry | None = None
    debug_config: DebugConfig = DebugConfig()
    command_prefix: str = "/"
    metrics: MessageMetrics | None = None
    action_service: Any | None = None
    chat_log: ChatLog | None = None
    permissions: PermissionStore = permission_store
    # 在有界缓存中保留 origin 引用，确保在重复检查前，object ID 不会被其他
    # 事件复用。
    _received_event_cache: deque[tuple[int, object]] = field(
        init=False,
        default_factory=lambda: deque(maxlen=_RECEIVED_EVENT_CACHE_SIZE),
        repr=False,
        compare=False,
    )
    _received_event_ids: set[int] = field(
        init=False,
        default_factory=set,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.metrics is None:
            self.metrics = message_metrics
        if self.debug_config.enabled and not self.debug_config.masters:
            logger.warning(
                "Debug mode is enabled but no masters are configured; "
                "all events will be ignored"
            )

    def should_skip(self, account: Account, event: Event) -> bool:
        """判断事件是否应在进入消息和插件处理前被跳过。"""

        # 沙盒是面板发起的测试对话：不登记账号、不参与群选路，也不受调试白名单限制。
        if is_sandbox(account):
            return False
        if self.account_registry is not None and not self.account_registry.is_enabled(
            account
        ):
            return True

        # 群成员减少和 bot 邀请都是账号状态来源，不能被调试白名单或账号×群
        # 选路过滤；否则解绑监听器收不到事件，或者邀请恰好由非选中账号收到时
        # 会被丢弃。
        if _is_member_removed_event(event) or _is_guild_invite_request_event(event):
            return False

        # 黑名单（#234）：名单里的人在任何群和私聊、名单里的群对任何人，都不响应，
        # 包括复读这类不经命令的功能。
        platform = account_key(account)[0]
        for kind, target in (
            ("user", _event_user_id(event)),
            ("group", _event_group_id(event)),
        ):
            if target is not None and self.permissions.blacklisted(
                kind, platform, target
            ):
                logger.debug(
                    "Ignore event from blacklisted {}: account={} id={}",
                    kind,
                    account.self_id,
                    target,
                )
                return True

        if self.debug_config.enabled:
            user_id = _event_user_id(event)
            if user_id not in self.debug_config.masters:
                logger.debug(
                    "Ignore event in debug mode: account={} user_id={} "
                    "protocol_type={}",
                    account.self_id,
                    user_id,
                    getattr(event, "_type", "-"),
                )
                return True

        if (
            self.account_registry is None
            or (group_id := _event_group_id(event)) is None
        ):
            return False
        group = (platform, group_id)

        # 与 v3 相同：自家其他 bot 发的群消息不交给插件，免得 bot 之间互相触发命令。
        # 聊天记录在这之前写入，面板里照样能看到。
        if is_message_created(event) and (sender := _event_user_id(event)) is not None:
            if self.account_registry.is_own((account_key(account)[0], sender)):
                return True

        # 消息事件可能是启动时群列表发现之前抵达的第一条消息。事件本身
        # 证明账号当前在线，因此先登记账号并加入当前群路由，再执行统一
        # 选路；这样群列表拉取失败时仍保留消息触发绑定的兜底路径。
        if self.account_registry.get(account) is None:
            self.account_registry.register(account, available=True)
        self.account_registry.bind_group(group, account)

        # QQ 只把加群申请推给群主和管理员，选路选中的号可能不是管理员、根本收不到，
        # 所以收到的号都放行；几个管理员号都收到时由处理申请的插件去重。
        if _is_member_join_request_event(event):
            return self.account_registry.is_muted(account, group)

        if not self.account_registry.is_muted(account, group):
            selected = self.account_registry.select_for_event(
                group,
                source_id=_event_route_id(event),
                mentioned=_leading_mention(event),
            )
            if selected is not None and account_key(selected) == account_key(account):
                return False
            if selected is not None:
                logger.debug(
                    "Ignore event for non-selected account={} selected={} group={} "
                    "protocol_type={}",
                    account.self_id,
                    selected.self_id,
                    group_id,
                    getattr(event, "_type", "-"),
                )
            else:
                logger.debug(
                    "Ignore event because no response account is available: "
                    "account={} group={} response_type={} deterministic_account={} "
                    "protocol_type={}",
                    account.self_id,
                    group_id,
                    self.account_registry.response_type_for_group(group),
                    self.account_registry.deterministic_account_for_group(group),
                    getattr(event, "_type", "-"),
                )
            return True

        if self._is_mute_recovery_event(account, event):
            logger.debug(
                "Allow mute recovery command for muted account={} group={}",
                account.self_id,
                group_id,
            )
            return False

        logger.debug(
            "Ignore event for muted account={} group={} protocol_type={}",
            account.self_id,
            group_id,
            getattr(event, "_type", "-"),
        )
        return True

    def _is_mute_recovery_event(self, account: Account, event: Event) -> bool:
        """判断消息是否是被禁言账号仍需响应的 ``解禁自己`` 命令。

        此时 Entari 还没有解析命令，所以按 Entari 识别命令前的同一套归一化
        （忽略引用、开头的 @bot、前导空白、昵称前缀）还原命令文本。
        """

        try:
            elements = list(event.message.message)  # type: ignore[union-attr]
        except (AttributeError, TypeError):
            return False
        elements = [item for item in elements if not isinstance(item, Quote)]
        if elements and isinstance(elements[0], At):
            if elements[0].id != str(account.self_id):
                return False
            elements.pop(0)
        if not elements or not isinstance(elements[0], Text):
            return False
        text = elements[0].text.lstrip()
        if len(elements) > 1:
            # 命令后跟了图片等非文本元素，不是纯 “解禁自己”。
            return False
        nickname = EntariConfig.instance.basic.nickname if EntariConfig._inited else ""
        if nickname and (
            matched := re.match(rf"^@?{re.escape(nickname)}[，,:\s]+", text)
        ):
            text = text[matched.end() :]
            return text.strip() == "解禁自己"
        return text.strip() == f"{self.command_prefix}解禁自己"

    def guard(
        self,
        callback: Callable[[Account, Event], Awaitable[Any]],
    ) -> Callable[[Account, Event], Awaitable[Any]]:
        """在 Entari 发布事件前执行 Tenko 的整个事件过滤链。

        这是宿主与 Entari 原生 ``event_callbacks`` 之间的边界：插件仍由
        Entari 负责发布和生命周期管理，Tenko 在进入发布链前执行调试白名单
        与账号×群禁言判定，避免只过滤固定回复而让其他插件继续处理同一事件。
        """

        async def dispatch(account: Account, event: Event) -> Any:
            self._record_received_event(event)
            # 在选路之前记录：同群的每个账号都要有自己的一份聊天记录。
            if self.chat_log is not None:
                self.chat_log.record_event(account, event)
            if self.should_skip(account, event):
                return None
            return await callback(account, event)

        return dispatch

    async def handle(self, account: Account, event: Event) -> None:
        self._record_received_event(event)
        if self.should_skip(account, event):
            return

        try:
            context = MessageContext.from_event(event)
        except ValueError:
            logger.exception("Invalid message event received; event was ignored")
            return

        if context.user_id == account.self_id:
            logger.debug(
                "Ignore message sent by the bot itself: {}", context.message_id
            )
            return

        if (
            context.chat_type == "group"
            and self.account_registry is not None
            and self.account_registry.get(account) is not None
        ):
            self.account_registry.bind_group(context.group, account)

        logger.debug(
            "Message received: account={} chat={} channel={} user={} message={} "
            "text={!r} images={} protocol_type={}",
            context.account_id,
            context.chat_type,
            context.channel_id,
            context.user_id,
            context.message_id,
            context.text,
            len(context.image_urls),
            context.protocol_event_type or "-",
        )

        if (
            context.chat_type == "group"
            and self.account_registry is not None
            and self.account_registry.get(account) is not None
            and self.account_registry.is_muted(account, context.group)
        ):
            self.account_registry.set_muted(account, context.group, False)
            logger.info(
                "Cleared stale mute after successful group send: account={} group={}",
                account.self_id,
                context.channel_id,
            )

    async def handle_member_removed(self, account: Account, event: Event) -> None:
        """消费群成员减少事件并立即解除被移除账号的群路由。"""

        if self.account_registry is None or not _is_member_removed_event(event):
            return
        group_id = _event_group_id(event)
        member_id = _event_user_id(event)
        if group_id is None or member_id is None:
            logger.debug(
                "Ignore member-removed event without group/member: account={} "
                "group={} member={} protocol_type={}",
                account.self_id,
                group_id,
                member_id,
                getattr(event, "_type", "-"),
            )
            return

        platform = account_key(account)[0]
        removed_account = self.account_registry.get((platform, member_id))
        if removed_account is None:
            # 普通成员退群不能影响任何账号×群路由。
            return
        # 让 AccountRegistry.unbind_group() 统一处理 deterministic：指定账号
        # 被移除后回退到剩余成员首个账号；群无成员时同时删除群路由状态。
        if not self.account_registry.unbind_group(
            (platform, group_id), (platform, member_id)
        ):
            return

        protocol_type = str(getattr(event, "_type", "") or "")
        kicked = protocol_type.endswith(".kick") or protocol_type.endswith(".kick_me")
        action = "被踢出" if kicked else "退出"
        logger.info(
            "Unbound account={} from group={} after bot {}",
            member_id,
            group_id,
            action,
        )

        if not kicked or self.action_service is None:
            return
        verify = getattr(self.action_service, "verify_group_membership", None)
        if not callable(verify):
            return
        try:
            still_member = await verify(removed_account, group_id)
        except Exception as error:
            # 事件已提供权威的解绑信号；群信息查询只是被踢场景的可选
            # 二次确认，权限不足或连接失败不能阻塞实时路由收缩。
            logger.debug(
                "Could not verify kicked account membership: account={} group={} "
                "error={}",
                member_id,
                group_id,
                error,
            )
        else:
            logger.debug(
                "Verified kicked account membership: account={} group={} "
                "still_member={}",
                member_id,
                group_id,
                still_member,
            )

    def _record_received_event(
        self, event: Event, context: MessageContext | None = None
    ) -> None:
        if self.metrics is None or not is_message_created(event) or is_sandbox(event):
            return
        origin = getattr(event, "_origin", None)
        if origin is None:
            origin = event
        origin_id = id(origin)
        if origin_id in self._received_event_ids:
            return
        if context is None:
            try:
                context = MessageContext.from_event(event)
            except (AttributeError, TypeError, ValueError):
                return
        self.metrics.record_received(
            context, timestamp=getattr(event, "timestamp", None)
        )
        if len(self._received_event_cache) == _RECEIVED_EVENT_CACHE_SIZE:
            expired_id, _ = self._received_event_cache.popleft()
            self._received_event_ids.discard(expired_id)
        self._received_event_cache.append((origin_id, origin))
        self._received_event_ids.add(origin_id)
