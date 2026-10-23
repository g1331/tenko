"""命令沙盒：面板扮演一个用户和 bot 对话，不经过真实协议。

沙盒是挂在进程内 Satori Server 上的一个虚拟平台 ``sandbox``。面板发出的消息
构造成标准的 ``message-created`` 事件，经 ``Server.post`` → ``App.post`` 进入
Entari，所以 Tenko 的事件过滤链、功能开关、限流和插件生命周期都照常生效；bot
的回复通过 Satori API 回到这个适配器，存进内存供面板拉取。

沙盒账号不登记进 ``AccountRegistry``：不参与群路由、不计入账户和消息统计，也
不会成为公告、升级通知的目标。它使用固定的虚拟 ID，Master 身份通过
``superuser_ids("sandbox")`` 生效，群管理员和群主通过事件里的成员角色生效。

插件逻辑在沙盒里真实执行，所以副作用在出口统一拦截：沙盒事件的处理链带一个
contextvar 标记（:func:`origin`），真实账号的平台写操作在 :class:`GuardedProtocol`
里不发出、改记一条说明；宿主全局状态的写入口调用 :func:`refuse_in_sandbox`。
"""

from __future__ import annotations

import html
import itertools
import re
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from arclet.entari.session import EntariProtocol
from launart import Launart
from launart.status import Phase
from satori import Api, ChannelType, EventType, LoginStatus
from satori.element import Author, Element, Quote, Text
from satori.model import (
    Channel,
    Event,
    Guild,
    Login,
    Member,
    MessageObject,
    Role,
    User,
)
from satori.server import Adapter, Request
from starlette.responses import Response

SANDBOX_PLATFORM = "sandbox"
SANDBOX_BOT_ID = "1000"
SANDBOX_USER_ID = "1001"
SANDBOX_MASTER_ID = "1002"
SANDBOX_GROUP_ID = "1100"
SANDBOX_GROUP_NAME = "沙盒群"
SANDBOX_PRIVATE_CHANNEL = f"private:{SANDBOX_USER_ID}"
HISTORY_LIMIT = 200

# 身份 -> (用户 ID, 显示名, 群角色)。Master 用单独的 ID，权限由 superuser_ids 给出。
IDENTITIES: dict[str, tuple[str, str, str]] = {
    "member": (SANDBOX_USER_ID, "测试成员", "member"),
    "admin": (SANDBOX_USER_ID, "测试管理员", "admin"),
    "owner": (SANDBOX_USER_ID, "测试群主", "owner"),
    "master": (SANDBOX_MASTER_ID, "测试 Master", "member"),
}


def is_sandbox(value: object) -> bool:
    """账号、登录信息、事件或平台名是否属于沙盒。"""

    if isinstance(value, str):
        return value == SANDBOX_PLATFORM
    platform = getattr(value, "platform", None)
    if platform is None:
        platform = getattr(getattr(value, "login", None), "platform", None)
    return str(getattr(platform, "value", platform)) == SANDBOX_PLATFORM


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat()


class SandboxAdapter(Adapter):
    """虚拟平台适配器：收面板消息、发事件，接住 bot 的 API 调用。"""

    def __init__(self, bot_name: str = "Tenko") -> None:
        super().__init__()
        self.login = Login(
            sn=0,
            status=LoginStatus.ONLINE,
            adapter=SANDBOX_PLATFORM,
            platform=SANDBOX_PLATFORM,
            user=User(SANDBOX_BOT_ID, bot_name),
            features=["guild.plain"],
        )
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_LIMIT)
        # 原始消息元素，用于引用回复时把被引用的内容交给插件。
        self._raw: dict[str, list[Element]] = {}
        self._ids = itertools.count(1)
        self.sequence = 0
        self.ready = False

        self.route(Api.MESSAGE_CREATE)(self._message_create)
        self.route(Api.MESSAGE_GET)(self._message_get)
        self.route(Api.MESSAGE_DELETE)(self._message_delete)
        self.route(Api.USER_CHANNEL_CREATE)(self._user_channel_create)
        self.route(Api.USER_GET)(self._user_get)
        self.route(Api.GUILD_GET)(self._guild_get)
        self.route(Api.GUILD_MEMBER_GET)(self._guild_member_get)
        self.route(Api.CHANNEL_GET)(self._channel_get)
        self.route(Api.LOGIN_GET)(self._login_get)

    # Adapter / launart ---------------------------------------------------

    def get_platform(self) -> str:
        return SANDBOX_PLATFORM

    @property
    def id(self) -> str:
        return "tenko.sandbox"

    def ensure(self, platform: str, self_id: str) -> bool:
        return platform == SANDBOX_PLATFORM and self_id == SANDBOX_BOT_ID

    async def get_logins(self) -> list[Login]:
        return [self.login]

    async def handle_internal(self, request: Request, path: str) -> Response:
        return Response(f"沙盒不支持平台内部接口 {path}", status_code=404)

    @property
    def required(self) -> set[str]:
        return {"satori-python.server"}

    @property
    def stages(self) -> set[Phase]:
        return {"preparing", "blocking", "cleanup"}

    async def launch(self, manager: Launart) -> None:
        async with self.stage("preparing"):
            pass
        async with self.stage("blocking"):
            await self.server.post(
                Event(EventType.LOGIN_ADDED, datetime.now(), self.login)
            )
            self.ready = True
            await manager.status.wait_for_sigexit()
        async with self.stage("cleanup"):
            self.ready = False

    # 面板侧 ---------------------------------------------------------------

    def messages(self, channel_id: str, after: int | None = None) -> list[dict]:
        return [
            {key: value for key, value in item.items() if key != "channel_id"}
            for item in self.history
            if item["channel_id"] == channel_id
            and (after is None or item["id"] > after)
        ]

    def clear(self) -> None:
        self.history.clear()
        self._raw.clear()
        self.sequence += 1

    async def send(
        self, chat_type: str, identity: str, elements: list[Element]
    ) -> None:
        """以指定身份发一条消息给 bot；引用会补上被引用的作者和内容。"""

        if not self.ready:
            raise RuntimeError("沙盒还没有就绪，请稍后再试")
        user_id, name, role = IDENTITIES[identity]
        user = User(user_id, name, nick=name)
        elements = [self._expand_quote(element) for element in elements]
        message_id = self._record(
            self._channel_id(chat_type), False, user_id, name, elements
        )
        if chat_type == "group":
            channel = Channel(SANDBOX_GROUP_ID, ChannelType.TEXT, SANDBOX_GROUP_NAME)
            guild = Guild(SANDBOX_GROUP_ID, SANDBOX_GROUP_NAME)
            member = Member(user, name, roles=[Role(role, role)])
        else:
            channel = Channel(SANDBOX_PRIVATE_CHANNEL, ChannelType.DIRECT, name)
            guild = None
            member = None
        event = Event(
            EventType.MESSAGE_CREATED,
            datetime.now(),
            self.login,
            channel=channel,
            guild=guild,
            member=member,
            user=user,
            message=MessageObject.from_elements(message_id, elements),
        )
        await self.server.post(event)

    @staticmethod
    def _channel_id(chat_type: str) -> str:
        return SANDBOX_GROUP_ID if chat_type == "group" else SANDBOX_PRIVATE_CHANNEL

    def _expand_quote(self, element: Element) -> Element:
        if not isinstance(element, Quote) or element.id is None:
            return element
        original = self._find(element.id)
        if original is None:
            return element
        author = Author(original["user_id"], original["user_name"])
        return Quote(element.id, content=[author, *self._raw.get(element.id, [])])

    def _find(self, message_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.history if item["message_id"] == message_id), None
        )

    def _record(
        self,
        channel_id: str,
        outgoing: bool,
        user_id: str,
        user_name: str,
        elements: list[Element],
    ) -> str:
        from .chat_log import preview, segments

        number = next(self._ids)
        message_id = str(number)
        content = segments(elements, inline_images=True)
        for item in content:
            # bot 的回复只带被引用消息的 ID，从沙盒记录里补上作者和摘要。
            if item["type"] == "quote" and not item["text"]:
                original = self._find(item["id"])
                if original is not None:
                    item["user_id"] = original["user_id"]
                    item["name"] = original["user_name"]
                    item["text"] = preview(original["content"], 60)
        if len(self.history) == self.history.maxlen:
            self._raw.pop(self.history[0]["message_id"], None)
        self.history.append(
            {
                "id": number,
                "message_id": message_id,
                "channel_id": channel_id,
                "outgoing": outgoing,
                "user_id": user_id,
                "user_name": user_name,
                "content": content,
                "created_at": _iso(time.time()),
            }
        )
        self._raw[message_id] = [
            element for element in elements if not isinstance(element, Quote)
        ]
        self.sequence += 1
        return message_id

    # Satori API -----------------------------------------------------------

    async def _message_create(self, request: Request) -> list[MessageObject]:
        channel_id = str(request.params["channel_id"])
        message = MessageObject(id="", content=request.params["content"])
        elements = list(message.message)
        message_id = self._record(
            channel_id, True, SANDBOX_BOT_ID, self.login.user.name or "", elements
        )
        return [MessageObject.from_elements(message_id, elements)]

    async def _message_get(self, request: Request) -> MessageObject:
        message_id = str(request.params["message_id"])
        if message_id not in self._raw:
            raise KeyError(f"沙盒里没有消息 {message_id}")
        return MessageObject.from_elements(message_id, self._raw[message_id])

    async def _message_delete(self, request: Request) -> None:
        message_id = str(request.params["message_id"])
        self._raw.pop(message_id, None)
        kept = [item for item in self.history if item["message_id"] != message_id]
        self.history.clear()
        self.history.extend(kept)
        self.sequence += 1

    async def _user_channel_create(self, request: Request) -> Channel:
        user_id = str(request.params["user_id"])
        return Channel(f"private:{user_id}", ChannelType.DIRECT)

    async def _user_get(self, request: Request) -> User:
        user_id = str(request.params["user_id"])
        return User(user_id, self._user_name(user_id))

    async def _guild_get(self, request: Request) -> Guild:
        return Guild(str(request.params["guild_id"]), SANDBOX_GROUP_NAME)

    async def _guild_member_get(self, request: Request) -> Member:
        user_id = str(request.params["user_id"])
        name = self._user_name(user_id)
        return Member(User(user_id, name), name)

    async def _channel_get(self, request: Request) -> Channel:
        channel_id = str(request.params["channel_id"])
        if channel_id.startswith("private:"):
            return Channel(channel_id, ChannelType.DIRECT)
        return Channel(channel_id, ChannelType.TEXT, SANDBOX_GROUP_NAME)

    async def _login_get(self, request: Request) -> Login:
        del request
        return self.login

    def note(self, channel_id: str, text: str) -> None:
        """在沙盒会话里记一条 bot 发出的说明，比如被拦截的真实动作。"""

        self._record(
            channel_id, True, SANDBOX_BOT_ID, self.login.user.name or "", [Text(text)]
        )

    def _user_name(self, user_id: str) -> str:
        if user_id == SANDBOX_BOT_ID:
            return self.login.user.name or ""
        return next(
            (name for uid, name, _ in IDENTITIES.values() if uid == user_id), user_id
        )


sandbox: SandboxAdapter | None = None


def install(adapter: SandboxAdapter | None) -> None:
    global sandbox
    sandbox = adapter


# ---------------------------------------------------------------- 隔离

# 沙盒事件的处理链里是发起它的沙盒会话 ID；create_task 派生的任务会复制上下文。
_origin: ContextVar[str | None] = ContextVar("tenko_sandbox_origin", default=None)


def origin() -> str | None:
    """当前处理链来自沙盒时，返回发起它的沙盒会话 ID。"""

    return _origin.get()


@contextmanager
def sandbox_origin(channel_id: str) -> Iterator[None]:
    token = _origin.set(channel_id)
    try:
        yield
    finally:
        _origin.reset(token)


class SandboxRefused(Exception):
    """沙盒里发起的调用要改动真实状态；异常捕获只在沙盒里回一句说明。"""

    def __init__(self, what: str) -> None:
        super().__init__(f"[沙盒] 这里不会{what}，真实状态没有改动")
        self.channel = _origin.get()


def refuse_in_sandbox(what: str, target: object = None) -> None:
    """宿主全局状态的写入口调用；``target`` 是沙盒平台的群或用户时放行。"""

    if _origin.get() is None or _is_sandbox_target(target):
        return
    raise SandboxRefused(what)


def _is_sandbox_target(target: object) -> bool:
    if target is None:
        return False
    if isinstance(target, tuple):
        return str(target[0]) == SANDBOX_PLATFORM
    from tenko.context import group_key

    try:
        return group_key(target)[0] == SANDBOX_PLATFORM
    except ValueError:
        return False


# 不改变平台状态、发送私聊前又必须调用的动作。
_HARMLESS = {"user.channel.create", "upload.create"}
_TAG = re.compile(r"<[^>]+>")


def _read_only(action: str, method: str) -> bool:
    if action.startswith("internal/"):
        return action.removeprefix("internal/").startswith("get_")  # OneBot 查询动作
    return method == "GET" or action.endswith((".get", ".list")) or action in _HARMLESS


def _describe(action: str, params: dict | None) -> str:
    params = params or {}
    target = params.get("channel_id") or params.get("guild_id") or ""
    if action == "message.create":
        text = html.unescape(_TAG.sub("", str(params.get("content", "")))).strip()
        text = text[:60] + ("…" if len(text) > 60 else "") if text else "[非文本消息]"
        return f"向 {target} 发送：{text}"
    names = {
        "message.delete": "撤回消息",
        "message.update": "编辑消息",
        "guild.member.kick": "踢出成员",
        "guild.member.mute": "禁言成员",
        "guild.member.approve": "处理入群申请",
        "guild.approve": "处理群邀请",
        "friend.approve": "处理好友申请",
    }
    return f"{names.get(action, f'调用 {action}')}{f'（{target}）' if target else ''}"


class GuardedProtocol(EntariProtocol):
    """Entari 默认协议加一道沙盒检查：沙盒处理链里真实账号的写操作不发出。

    所有 API（包括 ``internal`` 扩展动作）都经过 ``call_api``，它和插件在同一个
    任务里执行，所以能读到沙盒标记；服务端的 OneBot 连接隔着本机 HTTP，读不到。
    """

    async def call_api(
        self,
        action: str | Api,
        params: dict | None = None,
        multipart: bool = False,
        method: str = "POST",
    ) -> Any:
        name = action.value if isinstance(action, Api) else action
        channel = _origin.get()
        if channel is None or is_sandbox(self.account) or _read_only(name, method):
            if channel is None and not _read_only(name, method):
                # 延迟导入：stats 经插件运行时间接导入本模块。
                from .stats import record_api

                record_api(name)
            return await super().call_api(action, params, multipart, method)
        if sandbox is not None:
            sandbox.note(
                channel,
                f"[沙盒拦截] 本应由 {self.account.self_id} {_describe(name, params)}",
            )
        if name == "message.create":
            return [{"id": f"sandbox-blocked-{next(_blocked)}", "content": ""}]
        return {}


_blocked = itertools.count(1)


__all__ = [
    "HISTORY_LIMIT",
    "IDENTITIES",
    "SANDBOX_BOT_ID",
    "SANDBOX_GROUP_ID",
    "SANDBOX_GROUP_NAME",
    "SANDBOX_MASTER_ID",
    "SANDBOX_PLATFORM",
    "SANDBOX_PRIVATE_CHANNEL",
    "SANDBOX_USER_ID",
    "GuardedProtocol",
    "SandboxAdapter",
    "SandboxRefused",
    "install",
    "is_sandbox",
    "origin",
    "refuse_in_sandbox",
    "sandbox",
    "sandbox_origin",
]
