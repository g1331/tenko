"""扩展插件给 Master 的通知（``tenko_api.notify``）。

发到哪里、由哪个账号发送沿用宿主的通知设置（:mod:`tenko.host.notify`），插件看不到
Master 是谁。宿主在内容前加一行来源（插件名和 ID），并按插件限速：令牌桶最多连发
:data:`BURST` 条，之后每 :data:`REFILL_SECONDS` 秒恢复一条；刚越线时通知 Master 说明
一次，插件安静下来（令牌回满）之前不再提醒。

带 ``key`` 的通知会记下每条已发消息的 ``(平台, 账号, 消息 ID)``，存在插件数据目录的
:data:`BOOK_NAME`（保留 :data:`KEEP_DAYS` 天、最多 :data:`KEEP_MAX` 条），所以重启后
Master 引用旧通知回复也能对上。:func:`master_reply` 按被引用消息查这本账，只认 Master
本人的回复，且只返回调用方插件自己发出的通知。

沙盒处理链里不发出：通知原文记进发起它的沙盒会话，测试者切到 Master 身份引用它
回复，就能走完整个流程；账本写在沙盒数据副本里，也不占用真实的限速额度。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from arclet.entari.plugin import get_plugins
from loguru import logger
from satori.element import Author, Br, Element, Image, Quote, Text

from ...context import context_from_session
from .. import notify as host_notify
from .. import sandbox
from ..accounts import account_registry
from ..perm import Permission, PermissionChecker
from .runtime import external_plugin_for_module
from .storage import _caller_module, _data_dir

# 每个插件最多连发 BURST 条，之后每 REFILL_SECONDS 秒恢复一条（每小时约 20 条）。
BURST = 5
REFILL_SECONDS = 180.0
MAX_TEXT = 2000
MAX_IMAGES = 4
MAX_KEY = 128
BOOK_NAME = ".master-notices.json"
KEEP_DAYS = 30
KEEP_MAX = 200

NoticeFailure = Literal["rate_limited", "no_account", "no_target", "failed"]
Ref = tuple[str, str, str]


class NoticeError(RuntimeError):
    """无法确定调用方插件：通知接口只能在扩展插件自己的模块里调用。"""


@dataclass(frozen=True, slots=True)
class MasterNotice:
    """一次通知的结果。

    ``delivered`` 为真表示至少一个目标收到了（私聊多个 Master 时部分失败只记日志）。
    没有送达时 ``reason`` 说明原因：

    - ``"rate_limited"``：插件发得太频繁，被宿主拦下，稍后可以再试；
    - ``"no_account"``：没有在线账号可以发送；
    - ``"no_target"``：私聊模式下没有配置 Master；
    - ``"failed"``：发送了，但每个目标都失败了（详情在宿主日志）。
    """

    delivered: bool
    reason: NoticeFailure | None = None
    key: str | None = None


@dataclass(frozen=True, slots=True)
class MasterReply:
    """Master 引用某条通知的回复。"""

    # 发送通知时传入的 key。
    key: str
    # 回复的纯文字，去掉了引用、@ 和首尾空白；只发了图片时为空字符串。
    text: str
    user_id: str


class NoticeLimiter:
    """按插件计数的令牌桶。"""

    def __init__(
        self,
        burst: int = BURST,
        refill: float = REFILL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.burst = burst
        self.refill = refill
        self.clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._warned: set[str] = set()

    def take(self, plugin_id: str) -> bool:
        now = self.clock()
        tokens, updated = self._buckets.get(plugin_id, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - updated) / self.refill)
        if tokens >= self.burst:
            self._warned.discard(plugin_id)
        allowed = tokens >= 1
        self._buckets[plugin_id] = (tokens - 1 if allowed else tokens, now)
        return allowed

    def first_refusal(self, plugin_id: str) -> bool:
        """这次拒绝是不是越线后的第一次（之后到令牌回满前都返回 False）。"""

        if plugin_id in self._warned:
            return False
        self._warned.add(plugin_id)
        return True


limiter = NoticeLimiter()


def _owner(module: str) -> str:
    found = external_plugin_for_module(module)
    if found is None:
        raise NoticeError(
            f"无法确定 {module!r} 所属的扩展插件：通知接口只能在插件自己的模块里调用，"
            "且包必须在 pyproject 的 tenko.plugins 入口中声明"
        )
    return found


def _plugin_name(plugin_id: str) -> str:
    natives = sorted(get_plugins(subplugged=True), key=lambda plugin: len(plugin.id))
    for native in natives:
        if external_plugin_for_module(native.id) == plugin_id:
            name = getattr(getattr(native, "metadata", None), "name", None)
            if name:
                return str(name)[:30]
    return plugin_id


def _elements(content: object) -> list[Element]:
    if isinstance(content, str | Element):
        content = [content]
    try:
        items = list(content)  # type: ignore[call-overload]
    except TypeError:
        raise TypeError("通知内容必须是 str 或消息元素序列") from None
    elements = [Text(item) if isinstance(item, str) else item for item in items]
    for element in elements:
        if not isinstance(element, Text | Image | Br):
            raise TypeError(
                f"通知内容只支持文字、换行和图片，收到 {type(element).__name__}"
            )
    length = sum(len(element.text) for element in elements if isinstance(element, Text))
    images = sum(isinstance(element, Image) for element in elements)
    if not images and not any(
        isinstance(element, Text) and element.text.strip() for element in elements
    ):
        raise ValueError("通知内容不能为空")
    if length > MAX_TEXT:
        raise ValueError(f"通知文字不能超过 {MAX_TEXT} 字")
    if images > MAX_IMAGES:
        raise ValueError(f"一条通知最多 {MAX_IMAGES} 张图片")
    return elements


def _check_key(key: object) -> str | None:
    if key is None:
        return None
    if not isinstance(key, str) or not key or len(key) > MAX_KEY:
        raise ValueError(f"key 必须是 1 到 {MAX_KEY} 个字符的字符串")
    return key


# ------------------------------------------------------------------ 账本


def _book(plugin_id: str):
    return _data_dir(plugin_id) / BOOK_NAME


def _load(plugin_id: str) -> list[dict[str, Any]]:
    """读取未过期的记录；文件缺失或损坏时当作空账本（损坏只记日志）。"""

    path = _book(plugin_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        notices = raw["notices"]
        if not isinstance(notices, list):
            raise TypeError("notices 必须是列表")
    except FileNotFoundError:
        return []
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.warning("通知记录 {} 无法读取，按空记录处理：{}", path, error)
        return []
    oldest = time.time() - KEEP_DAYS * 86400
    return [
        item
        for item in notices
        if isinstance(item, dict)
        and isinstance(item.get("at"), int | float)
        and item["at"] >= oldest
    ]


def _digest(elements: Iterable[Element]) -> str:
    """消息文字（去掉全部空白）的摘要；协议端回传的被引用内容会重排空白和换行。"""

    text = "".join(e.text for e in elements if isinstance(e, Text))
    return hashlib.sha256("".join(text.split()).encode()).hexdigest()[:16]


def _track(plugin_id: str, key: str, refs: Iterable[Ref], digest: str) -> None:
    item = {"key": key, "refs": [list(ref) for ref in refs], "digest": digest}
    try:
        notices = [*_load(plugin_id), {**item, "at": time.time()}]
        path = _book(plugin_id)
        temp = path.with_name(f"{path.name}.tmp")
        text = json.dumps(
            {"version": 1, "notices": notices[-KEEP_MAX:]}, ensure_ascii=False
        )
        temp.write_text(text + "\n", encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        logger.exception(
            "无法记录插件 {} 的通知 {}，将识别不了对它的回复", plugin_id, key
        )


def _lookup(plugin_id: str, ref: Ref, author: str | None, digest: str) -> str | None:
    """按被引用消息找通知。

    先精确匹配 ``(平台, 账号, 消息 ID)``。OneBot 的消息 ID 是各账号本地编号：通知群里
    有多个 Tenko 账号、回复被别的账号接收时对不上，此时被引用消息的作者是发出通知的
    账号（``author``），改用它加上文字摘要匹配。
    """

    platform, _, _ = ref
    target = list(ref)
    fallback = None
    for item in reversed(_load(plugin_id)):
        refs, key = item.get("refs"), item.get("key")
        if not isinstance(refs, list) or not isinstance(key, str):
            continue
        if target in refs:
            return key
        if (
            fallback is None
            and author is not None
            and item.get("digest") == digest
            and any(r[:2] == [platform, author] for r in refs if isinstance(r, list))
        ):
            fallback = key
    return fallback


# ------------------------------------------------------------------ 发送


def notify_master(
    content: str | Iterable[Element | str],
    *,
    key: str | None = None,
) -> Coroutine[Any, Any, MasterNotice]:
    """按宿主的通知设置给 Master 发一条通知，返回可 ``await`` 的 :class:`MasterNotice`。

    ``content`` 是文字，或由文字、``Br``、``Image`` 组成的序列（例如
    ``MessageChain``）；文字合计不超过 2000 字，图片不超过 4 张。传 ``key`` 时记下这条
    通知，之后用 :func:`master_reply` 识别 Master 对它的引用回复。

    内容或 ``key`` 不合法时在调用时抛 ``TypeError`` / ``ValueError``；不在扩展插件的
    模块里调用时抛 :class:`NoticeError`。没送达不抛异常，看返回值的 ``delivered``。
    """

    plugin_id = _owner(_caller_module(1))
    elements = _elements(content)
    return _send(plugin_id, elements, _check_key(key))


async def _send(
    plugin_id: str, elements: list[Element], key: str | None
) -> MasterNotice:
    name = _plugin_name(plugin_id)
    source = f"「{name}」（{plugin_id}）" if name != plugin_id else f"「{plugin_id}」"
    message = [Text(f"🔌 来自插件{source}\n"), *elements]

    channel = sandbox.origin()
    if channel is not None:
        if sandbox.sandbox is None:
            return MasterNotice(False, "no_account", key)
        message_id = sandbox.sandbox.note(
            channel, "[沙盒拦截] 本应发给 Master 的通知：\n", message
        )
        if key is not None:
            ref = (sandbox.SANDBOX_PLATFORM, sandbox.SANDBOX_BOT_ID, message_id)
            _track(plugin_id, key, [ref], _digest(message))
        return MasterNotice(True, None, key)

    if not limiter.take(plugin_id):
        logger.warning("插件 {} 通知 Master 过于频繁，已拦下一条", plugin_id)
        if limiter.first_refusal(plugin_id):
            await host_notify.notify(
                f"⚠️ 插件{source}给 Master 发通知过于频繁，多余的通知已被拦下"
                f"（最多连发 {BURST} 条，之后每 {REFILL_SECONDS / 60:.0f} 分钟恢复一条）。"
                "它安静下来之前不再提醒。"
            )
        return MasterNotice(False, "rate_limited", key)

    delivery = await host_notify.deliver(message)
    if delivery.reason is not None:
        return MasterNotice(False, delivery.reason, key)  # type: ignore[arg-type]
    if not delivery.sent:
        return MasterNotice(False, "failed", key)
    if key is not None:
        platform = str(getattr(delivery.sender, "platform", ""))
        account = str(getattr(delivery.sender, "self_id", ""))
        refs = [
            (platform, account, message_id)
            for _, ids in delivery.sent
            for message_id in ids
        ]
        if not refs:
            logger.warning("平台没有返回通知的消息 ID，无法识别对它的回复：{}", key)
        else:
            _track(plugin_id, key, refs, _digest(message))
    return MasterNotice(True, None, key)


# ------------------------------------------------------------------ 回复


def master_reply(session: Any) -> Coroutine[Any, Any, MasterReply | None]:
    """消息是 Master 引用本插件某条带 ``key`` 的通知的回复时，返回 :class:`MasterReply`。

    其他情况（没有引用、引用的不是本插件的通知、记录已过期、回复者不是 Master）返回
    ``None``。需要 ``await``。
    """

    return _reply(_owner(_caller_module(1)), session)


async def _reply(plugin_id: str, session: Any) -> MasterReply | None:
    quote = getattr(session, "quote", None)
    if not isinstance(quote, Quote) or not quote.id:
        return None
    account = session.account
    platform, self_id = str(account.platform), str(account.self_id)
    # 被引用消息由同平台的另一个自家账号发出时，才需要按文字摘要兜底。
    author = next((str(e.id) for e in quote.children if isinstance(e, Author)), None)
    if author == self_id or not account_registry.is_own((platform, author or "")):
        author = None
    key = _lookup(
        plugin_id, (platform, self_id, str(quote.id)), author, _digest(quote.children)
    )
    if key is None:
        return None
    context = context_from_session(session)
    if PermissionChecker().level(context) < Permission.Master:
        return None
    # 引用和 @（QQ 引用回复时常自动带上）不是 Text，只拼文字。
    text = "".join(
        element.text for element in session.elements if isinstance(element, Text)
    )
    return MasterReply(key, text.strip(), str(context.user_id))


__all__ = [
    "BURST",
    "MasterNotice",
    "MasterReply",
    "NoticeError",
    "NoticeLimiter",
    "REFILL_SECONDS",
    "limiter",
    "master_reply",
    "notify_master",
]
