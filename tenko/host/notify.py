"""给 Master 的通知：发往私聊还是群、由哪个账号发送，都是数据库里的动态设置，修改后立即生效。

启动通知、异常报告、好友申请、入群邀请、插件更新和账号掉线提醒都经由这里发送。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from ..context import QQ_PLATFORM
from .accounts import AccountRegistry, account_key, account_reference, account_registry
from .actions import action_service
from .perm import superuser_ids

SETTING_NAME = "notify"
MODES = {"private": "私聊 Master", "group": "通知群"}


@dataclass(frozen=True, slots=True)
class NotifySettings:
    mode: str = "private"
    group: str | None = None
    # 账号引用（QQ 为裸 ID）；None 表示任选一个在线账号。
    account: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"mode": self.mode, "group": self.group, "account": self.account}


def _identifier(value: object, label: str) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text.isdigit() or len(text) > 32:
        raise ValueError(f"{label}必须是数字 ID")
    return text


def parse_settings(value: Mapping[str, Any]) -> NotifySettings:
    """校验来自面板、命令或数据库的设置；非法时抛 ValueError。"""

    if not isinstance(value, Mapping) or set(value) - {"mode", "group", "account"}:
        raise ValueError("通知设置只能包含 mode、group、account")
    mode = value.get("mode", "private")
    if mode not in MODES:
        raise ValueError("通知方式必须是 private 或 group")
    group = _identifier(value.get("group"), "通知群")
    if mode == "group" and group is None:
        raise ValueError("发到群里时必须指定通知群")
    account = _identifier(value.get("account"), "通知账号")
    return NotifySettings(mode, group, account)


_defaults = NotifySettings()


def configure(notify_group: str | int | None) -> None:
    """配置文件里的 ``notify_group`` 作为面板或命令修改之前的默认值。"""

    global _defaults
    group = _identifier(notify_group, "notify_group")
    _defaults = NotifySettings("group" if group else "private", group)


def _repository() -> Any:
    from .friend_requests import default_repository

    return default_repository()


async def load_settings(repository: Any = None) -> NotifySettings:
    """读取当前设置；数据库不可用或数据损坏时退回配置文件的默认值，通知照常发送。"""

    try:
        stored = await (repository or _repository()).setting(SETTING_NAME)
        return _defaults if stored is None else parse_settings(stored)
    except Exception as error:
        logger.warning("读取通知设置失败，按配置文件默认值发送：{}", error)
        return _defaults


async def save_settings(
    changes: Mapping[str, Any], repository: Any = None
) -> NotifySettings:
    """在当前设置上应用修改并保存，返回新设置。"""

    repository = repository or _repository()
    current = await load_settings(repository)
    settings = parse_settings({**current.to_dict(), **changes})
    if settings.account not in (None, current.account) and not account_registry.is_own(
        settings.account
    ):
        raise ValueError(f"{settings.account} 不是 Tenko 连接过的账号")
    await repository.save_setting(SETTING_NAME, settings.to_dict())
    return settings


def pick_sender(
    settings: NotifySettings,
    preferred: object | None = None,
    registry: AccountRegistry | None = None,
) -> object | None:
    """通知账号在线就用它；否则依次退回事件自己的账号、通知群里的账号、任一在线账号。"""

    registry = registry or account_registry
    configured = registry.get(settings.account) if settings.account else None
    group_accounts = (
        registry.online_accounts_for_group(settings.group)
        if settings.mode == "group" and settings.group
        else ()
    )
    candidates = (configured, preferred, *group_accounts, *registry.accounts.values())
    sender = next(
        (
            item
            for item in candidates
            if item is not None and registry.is_available(item)
        ),
        None,
    )
    if settings.account and sender is not configured:
        logger.warning("通知账号 {} 不在线，改用其他账号发送", settings.account)
    # 注册表之外的账号（例如沙盒）仍可用事件自己的账号发送。
    return sender or preferred


def masters(
    account: object, permission_checker: object | None = None
) -> tuple[str, ...]:
    """接收私聊通知的 Master：运行时指定的 Master 加上该平台的全部 superusers。"""

    given = sorted(getattr(permission_checker, "masters", None) or ())
    platform = getattr(account, "platform", None) or QQ_PLATFORM
    return tuple(dict.fromkeys(x for x in (*given, *superuser_ids(platform)) if x))


async def notify(
    content: str | list,
    *,
    preferred: object | None = None,
    settings: NotifySettings | None = None,
    actions: Any = None,
    permission_checker: object | None = None,
    direct: bool = False,
) -> bool:
    """按通知设置发送，返回是否每个目标都已送达。

    ``direct=True`` 绕过 ActionService 直接调用协议：异常报告要在 ActionService
    自身出错时也能送达。
    """

    settings = settings or await load_settings()
    sender = pick_sender(settings, preferred)
    if sender is None:
        logger.warning("没有在线账号，无法发送通知：{}", _preview(content))
        return False
    actions = actions or action_service
    protocol = getattr(sender, "protocol", None)
    group = settings.mode == "group"
    targets = [settings.group] if group else masters(sender, permission_checker)
    if not targets:
        logger.warning("没有配置 Master，无法私聊通知：{}", _preview(content))
        return False
    delivered = True
    for target in targets:
        try:
            if group and direct:
                result = protocol.send_message(target, content)
            elif group:
                result = actions.send_group_message(
                    sender,
                    target,
                    content,
                    context=None,
                    permission_checker=permission_checker,
                    system=True,
                )
            elif direct:
                result = protocol.send_private_message(target, content)
            else:
                result = actions.send_private_message(sender, target, content)
            if inspect.isawaitable(result):
                await result
        except Exception:
            delivered = False
            logger.exception(
                "通知发送失败：account={} {}={}",
                getattr(sender, "self_id", "-"),
                settings.mode,
                target,
            )
    return delivered


def _preview(content: object) -> str:
    text = content if isinstance(content, str) else "[图片或富文本]"
    return text.replace("\n", " ")[:80]


# 连接断开不到这么久就恢复的（NapCat 重连、网络抖动）不打扰 Master。
OFFLINE_GRACE = 60.0


class AccountAlerts:
    """账号掉线、被下线和恢复在线时通知 Master。"""

    def __init__(
        self,
        registry: AccountRegistry | None = None,
        send: Callable[[str], Awaitable[object]] = notify,
        grace: float = OFFLINE_GRACE,
    ) -> None:
        self.registry = registry or account_registry
        self.send = send
        self.grace = grace
        self._pending: dict[tuple[str, str], asyncio.Task] = {}
        # 已经通知过掉线的账号和掉线时间，恢复时据此补一条恢复通知。
        self._down: dict[tuple[str, str], datetime] = {}
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coroutine: Awaitable[object]) -> asyncio.Task:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def offline(self, account: object) -> None:
        key = account_key(account)
        # 面板停用账号也会断开连接，那是有意为之。
        if (
            key in self._pending
            or key in self._down
            or not self.registry.is_enabled(key)
        ):
            return
        self._pending[key] = self._spawn(self._confirm_offline(key, datetime.now()))

    async def _confirm_offline(self, key: tuple[str, str], since: datetime) -> None:
        await asyncio.sleep(self.grace)
        self._pending.pop(key, None)
        if self.registry.is_online(key) or not self.registry.is_enabled(key):
            return
        self._down[key] = since
        await self.send(
            f"⚠️ 账号 {account_reference(key)} 自 {since:%H:%M:%S} 起断开连接，"
            "至今未恢复\n请检查协议端（如 NapCat）是否在运行、QQ 是否掉线。"
        )

    def kicked(self, account: object, reason: str) -> None:
        """协议端报告 QQ 被下线（例如在其他设备登录、登录态失效），立即通知。"""

        key = account_key(account)
        if task := self._pending.pop(key, None):
            task.cancel()
        self._down.setdefault(key, datetime.now())
        # 连接还在但 QQ 已下线：不再选它回复或发通知，重新登录后协议端重连会恢复。
        if self.registry.get(key) is not None:
            self.registry.set_available(key, False)
        self._spawn(
            self.send(
                f"⚠️ 账号 {account_reference(key)} 已被 QQ 下线\n"
                f"原因：{reason or '协议端未说明'}\n需要重新登录（例如扫码）后才能恢复。"
            )
        )

    def online(self, account: object) -> None:
        key = account_key(account)
        if task := self._pending.pop(key, None):
            task.cancel()
        since = self._down.pop(key, None)
        if since is not None:
            minutes = max((datetime.now() - since).total_seconds() / 60, 0)
            self._spawn(
                self.send(
                    f"✅ 账号 {account_reference(key)} 已恢复在线（离线约 {minutes:.0f} 分钟）"
                )
            )


account_alerts = AccountAlerts()


def bot_offline_reason(event: object) -> str | None:
    """NapCat 在 QQ 被下线时发 ``notice.bot_offline``；适配器不认识，原样作为 INTERNAL 事件送来。"""

    if str(getattr(event, "_type", "") or "") != "notice.bot_offline":
        return None
    data = getattr(event, "_data", None)
    message = data.get("message") if isinstance(data, Mapping) else None
    return "" if message in (None, "BotOfflineEvent") else str(message)


def describe_settings(settings: NotifySettings) -> str:
    registry = account_registry
    target = f"通知群 {settings.group}" if settings.mode == "group" else "私聊 Master"
    if settings.account is None:
        sender = "任选在线账号"
    else:
        state = "在线" if registry.is_available(settings.account) else "不在线"
        sender = f"{settings.account}（{state}）"
    return f"通知发往：{target}\n发送账号：{sender}"


__all__ = [
    "AccountAlerts",
    "NotifySettings",
    "account_alerts",
    "bot_offline_reason",
    "configure",
    "describe_settings",
    "load_settings",
    "masters",
    "notify",
    "parse_settings",
    "pick_sender",
    "save_settings",
]
