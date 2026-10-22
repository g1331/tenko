from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from launart import Launart
from loguru import logger
from satori import EventType, LoginStatus
from satori.adapters.onebot11.events.base import events as _EVENT_PARSERS
from satori.adapters.onebot11.reverse import (
    OneBot11ReverseAdapter,
    OneBot11ReverseConfig,
    _Connection,
)
from satori.adapters.onebot11.utils import USER_AVATAR_URL, onebot11_event_type
from satori.client import Account, ApiInfo, App
from satori.model import Event, Login, User
from satori.server import Server
from starlette.websockets import WebSocket, WebSocketState

from .config import OneBotConfig


# Server 仍对外提供 Satori HTTP API（账号调用平台接口走这里），路径固定即可。
_SATORI_PATH = "satori"


def _hide_local_api_access(record: logging.LogRecord) -> bool:
    """隐藏本进程账号调用 Satori API 的访问日志：每次发消息都有一行，内容另有消息日志。"""

    args = record.args if isinstance(record.args, tuple) else ()
    return not (
        len(args) >= 3
        and str(args[0]).startswith(("127.0.0.1:", "::1:"))
        and str(args[2]).startswith(f"/{_SATORI_PATH}/")
    )


@dataclass(eq=False)
class _InProcessNetwork:
    """``App.post`` 需要的连接对象：账号凭 ``config`` 调用本机 Satori HTTP API。"""

    config: ApiInfo
    proxy_urls: list[str] = field(default_factory=list)
    accounts: dict[str, Account] = field(default_factory=dict)


class InProcessServer(Server):
    """把适配器事件直接交给同进程的 Entari，不再经回环 WebSocket 转发。

    走 ``App.post`` 而不是 ``Entari.handle_event``：账号增删、生命周期回调和
    ``event_callbacks``（Tenko 的事件过滤链）都依赖它。
    """

    def __init__(self, *, api_host: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.network = _InProcessNetwork(
            ApiInfo(host=api_host, port=self.port, path=self.path, token=self.token)
        )
        self.client: App | None = None
        self.manager: Launart | None = None
        self._tasks: set[asyncio.Task] = set()

    def attach(self, client: App, manager: Launart) -> None:
        self.client = client
        self.manager = manager

    async def post(self, event: Event) -> None:
        if self.client is None or self.manager is None:
            logger.warning("Entari 尚未接入，丢弃事件 {}", event.type)
            return
        self.network.proxy_urls = [
            url for provider in self.providers for url in provider.proxy_urls()
        ]
        # 与 satori WebSocket 客户端相同：每个事件一个任务，适配器的接收循环不被
        # 处理过程阻塞（处理中调用的 API 响应也要经由这个循环送回）。
        task = asyncio.create_task(self._deliver(self.client, self.manager, event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, client: App, manager: Launart, event: Event) -> None:
        # 原 WebSocket 客户端在 blocking 阶段才连上：所有组件准备完毕后再处理事件。
        while manager.status.stage in (None, "preparing"):
            await manager.status.wait_for_update()
        await client.post(event, self.network)  # type: ignore[arg-type]


class _ManagedConnection(_Connection):
    """校验握手身份，阻止断开后尚未完成的登录请求创建幽灵账户。"""

    def __init__(self, adapter, ws: WebSocket, account_id: str) -> None:
        super().__init__(adapter, ws)
        self.account_id = account_id
        self.closed = False

    async def message_receive(self):
        async for connection, data in super().message_receive():
            if not isinstance(data, dict) or (
                not data.get("echo") and str(data.get("self_id")) != self.account_id
            ):
                await self.ws.close(1008, "Event self_id does not match X-Self-ID")
                return
            yield connection, data

    async def message_handle(self) -> None:
        """替换 satori-python-adapter-onebot11 的同名方法。

        上游实现会用内部的 ``_login_add``（0.5.1 起平台名为 ``qq-client``），
        登录完成前收到事件会结束整个接收循环。这里沿用 Tenko 的 ``onebot``
        平台名、只丢弃登录前的事件，并把每条事件的数据作为参数交给任务。
        """

        async for _connection, data in self.message_receive():
            if echo := data.get("echo"):
                if future := self.response_waiters.get(echo):
                    future.set_result(data)
                continue
            event_type = onebot11_event_type(data)
            if event_type in {"meta_event.lifecycle.connect", "meta_event.heartbeat"}:
                if self.account_id not in self.adapter.logins:
                    asyncio.create_task(self._login_add())
            elif event_type in {
                "meta_event.lifecycle.enable",
                "meta_event.lifecycle.disable",
            }:
                logger.warning("反向 WebSocket 不支持 {} 事件，断开连接", event_type)
                return
            elif (login := self.adapter.logins.get(self.account_id)) is None:
                # 原实现在这里结束整个接收循环；登录完成前的事件丢弃即可。
                logger.warning("账号 {} 登录完成前收到事件，已丢弃", self.account_id)
            else:
                asyncio.create_task(self._post_event(login, event_type, data))

    async def _login_add(self) -> None:
        info = await self.call_api("get_login_info")
        login = Login(
            sn=0,
            status=LoginStatus.ONLINE,
            adapter="onebot",
            platform="onebot",
            user=User(
                self.account_id,
                (info or {})["nickname"],
                avatar=USER_AVATAR_URL.format(uin=self.account_id),
            ),
            features=["guild.plain"],
        )
        self.adapter.logins[self.account_id] = login
        await self.adapter.server.post(
            Event(EventType.LOGIN_ADDED, datetime.now(), login)
        )

    async def _post_event(self, login: Login, event_type: str, data: dict) -> None:
        event = None
        if parser := _EVENT_PARSERS.get(event_type):
            try:
                event = await parser(login, self, data)
            except Exception as error:
                logger.exception("failed to handle event {}: {!r}", event_type, error)
        if not event:
            event = Event(EventType.INTERNAL, datetime.now(), login)
        event._type = event_type
        event._data = data
        await self.adapter.server.post(event)

    async def call_api(self, action: str, params: dict | None = None) -> dict:
        result = await super().call_api(action, params)
        if self.closed:
            raise asyncio.CancelledError
        return result


class ManagedOneBotAdapter(OneBot11ReverseAdapter):
    """在已锁定的 OneBot 适配器边界增加准入和主动断开。"""

    admission: Callable[[str, str], bool] | None = None

    async def websocket_server_handler(self, ws: WebSocket) -> None:
        authorization = ws.headers.get("Authorization", "")
        expected = f"Bearer {self.access_token}" if self.access_token else ""
        if not secrets.compare_digest(authorization.encode(), expected.encode()):
            await ws.close(1008, "Authorization Header is invalid")
            return
        account_id = ws.headers.get("X-Self-ID", "")
        if not account_id or len(account_id) > 128 or account_id in self.connections:
            await ws.close(1008, "Invalid or duplicate X-Self-ID")
            return
        if self.admission is not None and not self.admission("onebot", account_id):
            await ws.close(1008, "Account is disabled or state is not ready")
            return
        await ws.accept()
        if account_id in self.connections or (
            self.admission is not None and not self.admission("onebot", account_id)
        ):
            await ws.close(1008, "Account is disabled")
            return
        connection = _ManagedConnection(self, ws, account_id)
        self.connections[account_id] = connection
        tasks = [
            asyncio.create_task(connection.message_handle()),
            asyncio.create_task(connection.close_signal.wait()),
        ]
        try:
            done, _pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            connection.closed = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.connections.pop(account_id, None)
            for future in connection.response_waiters.values():
                future.cancel()
            # 配对中途断线时还没有 Login，不应覆盖原错误或制造幽灵账户。
            login = self.logins.pop(account_id, None)
            if login is not None:
                login.status = LoginStatus.OFFLINE
                await self.server.post(
                    Event(EventType.LOGIN_REMOVED, datetime.now(), login)
                )
            if ws.application_state != WebSocketState.DISCONNECTED:
                await ws.close()

    async def disconnect(self, account_id: str) -> bool:
        connection = self.connections.get(account_id)
        if connection is None:
            return False
        connection.close_signal.set()
        return True


@dataclass(slots=True)
class OneBotConnection:
    """组装 OneBot 反向适配器和进程内 Satori Server。"""

    config: OneBotConfig
    adapter: ManagedOneBotAdapter = field(init=False)
    server: InProcessServer = field(init=False)

    def __post_init__(self) -> None:
        adapter_config = OneBot11ReverseConfig(
            prefix=self.config.reverse_ws_prefix,
            path=self.config.reverse_ws_path,
            endpoint=self.config.reverse_ws_endpoint,
            access_token=self.config.access_token,
            timeout=self.config.api_timeout,
        )
        self.adapter = ManagedOneBotAdapter(adapter_config)
        self.server = InProcessServer(
            api_host=self.config.local_host,
            host=self.config.listen_host,
            port=self.config.listen_port,
            path=_SATORI_PATH,
            # Satori HTTP API 与反向 WS 共用监听端口；令牌每次启动随机生成，
            # 只有本进程的账号知道。
            token=secrets.token_urlsafe(32),
        )
        self.server.apply(self.adapter)

    def install(self, manager: Launart) -> None:
        """把 ASGI 和 Satori Server 加入 Launart。"""

        manager.add_component(self.server.asgi_service)
        manager.add_component(self.server)
        logging.getLogger("uvicorn.access").addFilter(_hide_local_api_access)

    def attach(self, app: App, manager: Launart) -> None:
        """让适配器事件直接进入 ``app``。"""

        self.server.attach(app, manager)
