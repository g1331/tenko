from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
import secrets
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, TypeVar, overload

from launart import Launart, Service
from loguru import logger
from satori.server import Server
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    Response,
    StreamingResponse,
)

from ..config import WebUIConfig, TenkoConfig
from ..db.errors import DatabaseError
from ..host.account_management import AccountManagement
from .auth import (
    WEBUI_PATH,
    ApiResponse,
    WebUIAuth,
    error_response as _error_response,
    read_json,
    success_response as _success_response,
)
from .accounts import AccountPanel
from .contracts import (
    CoreConfigData,
    LogData,
    OverviewData,
    PairingInfo,
)
from .blacklist import BlacklistPanel
from .chat import ChatPanel
from .features import FeaturePanel
from .inbox import InboxPanel
from .logs import LogReader
from .middleware import ASSETS_PATH, HashedAssets, WebUIAuthMiddleware
from .plugins import PluginMarket
from .sandbox import SandboxPanel
from .settings import SettingsPanel
from .stats import StatsPanel
from ..host.chat_log import chat_log
from ..host import sandbox as sandbox_module
from ..host.metrics import MessageMetrics, message_metrics
from . import core_config
from .live import MAX_STREAMS, TOPICS, LiveStreams
from ..host.inbox import Inbox, inbox as default_inbox
from ..host.accounts import AccountRegistry, account_registry
from ..host.features import FeatureService, feature_service as default_feature_service
from ..host.plugins.runtime import PluginRuntime
from ..host.status import build_status_data

if TYPE_CHECKING:
    # repository 依赖官方 database 插件已加载，模块顶层不能导入它。
    from ..db.repositories import ManagementRepository

T = TypeVar("T")

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_INDEX = _STATIC_DIR / "index.html"
_STATIC_ASSETS = _STATIC_DIR / "assets"


class WebUIService(Service):
    """复用 Satori Server app 的管理面板；所有数据接口都需要登录会话。"""

    id = "tenko.webui"
    required = {"satori-python.server"}
    stages = {"preparing", "blocking", "cleanup"}

    def __init__(
        self,
        server: Server,
        config: WebUIConfig,
        *,
        accounts: AccountRegistry = account_registry,
        metrics: MessageMetrics = message_metrics,
        feature_service: FeatureService = default_feature_service,
        feature_repository: Any | None = None,
        plugin_runtime: PluginRuntime | None = None,
        management: AccountManagement | None = None,
        connection: Any | None = None,
        tenko_config: TenkoConfig | None = None,
        inbox: Inbox = default_inbox,
        apply_config: Callable[[TenkoConfig], list[str]] | None = None,
    ) -> None:
        super().__init__()
        self.server = server
        self.config = config
        self.accounts = accounts
        self.metrics = metrics
        self.feature_service = feature_service
        self.feature_repository = feature_repository
        self.plugin_runtime = plugin_runtime
        self.management = management
        self.connection = connection
        self.tenko_config = tenko_config
        self.inbox = inbox
        self.auth = WebUIAuth()
        self._write_lock = management.lock if management is not None else asyncio.Lock()
        self.logs = LogReader(self._secrets)
        self._log_sink: int | None = None
        self._retired_tokens: list[str] = []
        native = tenko_config.entari_config if tenko_config else None
        self._config_path = Path(native.path) if native is not None else None
        self._apply_config = apply_config
        # 已保存但要重启才生效的宿主配置项；没有实时应用入口时为 ["*"]。
        self._restart_fields: list[str] = []
        self.live = LiveStreams(
            overview=self._overview_data,
            logs=self.logs,
            marks=self._live_marks,
            session_valid=self.auth.revalidate,
            stopping=self._http_stopping,
        )
        self._config_stamp = self._stamp()
        self._register_routes()

    def _register_routes(self) -> None:
        self.server.app.add_middleware(
            WebUIAuthMiddleware, config=self.config, auth=self.auth
        )
        self.account_panel = AccountPanel(self)
        self.feature_panel = FeaturePanel(self)
        self.inbox_panel = InboxPanel(self)
        self.settings_panel = SettingsPanel(self)
        self.server.asgi_route(
            f"{WEBUI_PATH}/api/overview",
            methods=["GET"],
            name="webui-api-overview",
        )(self.overview)
        self.server.asgi_route(
            f"{WEBUI_PATH}/api/accounts",
            methods=["GET"],
            name="webui-api-accounts",
        )(self.account_panel.accounts_view)
        self.server.asgi_route(
            f"{WEBUI_PATH}/api/features",
            methods=["GET"],
            name="webui-api-features",
        )(self.feature_panel.features_view)
        for index_path in (WEBUI_PATH, f"{WEBUI_PATH}/"):
            self.server.asgi_route(
                index_path,
                methods=["GET"],
                name="webui-index" if index_path == WEBUI_PATH else None,
            )(self.index)
        self.server.app.mount(
            ASSETS_PATH,
            HashedAssets(directory=_STATIC_ASSETS, check_dir=False),
            name="webui-assets",
        )
        routes = (
            ("/api/auth/state", ["GET"], self.auth.state),
            ("/api/auth/login", ["POST"], self.auth.login),
            ("/api/auth/logout", ["POST"], self.auth.logout),
            ("/api/auth/sessions", ["GET"], self.auth.sessions),
            ("/api/auth/sessions/revoke-others", ["POST"], self.auth.revoke_others),
            ("/api/auth/sessions/{id}/revoke", ["POST"], self.auth.revoke),
            ("/api/auth/account", ["POST"], self.auth.account),
            *self.account_panel.routes(),
            ("/api/manage/pairing", ["GET", "POST"], self.pairing),
            *self.feature_panel.routes(),
            *self.settings_panel.routes(),
            ("/api/manage/config", ["GET", "POST"], self.core_config),
            ("/api/manage/logs", ["GET"], self.log_view),
            *self.inbox_panel.routes(),
            ("/api/events", ["GET"], self.live_events),
        )
        self.market = PluginMarket(self)
        self.chat = ChatPanel(self)
        self.sandbox = SandboxPanel(self)
        self.blacklist = BlacklistPanel(self)
        self.stats = StatsPanel(self)
        for path, methods, handler in (
            *routes,
            *self.market.routes(),
            *self.chat.routes(),
            *self.sandbox.routes(),
            *self.blacklist.routes(),
            *self.stats.routes(),
        ):
            self.server.asgi_route(f"{WEBUI_PATH}{path}", methods=methods)(handler)

    @overload
    async def read(
        self, operation: str, reader: Callable[[], Awaitable[T]]
    ) -> ApiResponse[T]: ...

    @overload
    async def read(self, operation: str, reader: Callable[[], T]) -> ApiResponse[T]: ...

    async def read(
        self,
        operation: str,
        reader: Callable[[], Any],
    ) -> ApiResponse[Any]:
        try:
            result = reader()
            if inspect.isawaitable(result):
                result = await result
        except Exception as error:
            logger.exception("WebUI {} read failed: {}", operation, error)
            return _error_response(
                f"{operation}_unavailable",
                "WebUI 数据暂不可用",
                500,
            )
        return _success_response(result)

    def _status_data(self) -> Mapping[str, Any]:
        runtime = self.plugin_runtime or PluginRuntime()
        context = SimpleNamespace(chat_type="private", channel_id="")
        return build_status_data(
            context,
            len(runtime.loaded_plugins),
            registry=self.accounts,
            metrics=self.metrics,
        )

    @staticmethod
    def _version(status_data: Mapping[str, Any]) -> str:
        for line in status_data.get("version_details", ()):
            if str(line).startswith("版本信息："):
                return str(line).removeprefix("版本信息：v")
        return "unknown"

    def _overview_data(self) -> OverviewData:
        status_data = self._status_data()
        online_bots = str(status_data.get("online_bots", "0/0"))
        online_count, separator, _total_count = online_bots.partition("/")
        if not separator:
            online_count = "0"
        try:
            online_accounts = int(online_count)
        except ValueError:
            online_accounts = 0

        process = status_data["process"]
        metrics = status_data["metrics"]
        return {
            "version": self._version(status_data),
            "uptime_seconds": process["uptime_seconds"],
            "rss": process["rss"],
            "resources": status_data["resources"],
            "activity": self.metrics.activity(),
            "online_accounts": online_accounts,
            "active_groups": status_data["active_groups"],
            "inbox": len(self.inbox),
            "plugin_attention": self.market.attention(),
            "messages": {
                "received_count": metrics["received_count"],
                "sent_count": metrics["sent_count"],
                "received_rate": metrics["received_rate"],
                "sent_rate": metrics["sent_rate"],
            },
        }

    async def overview(self, request: Request) -> ApiResponse[OverviewData]:
        del request
        return await self.read("overview", self._overview_data)

    async def group_names(self) -> dict[str, str]:
        """群名称取聊天记录里最近记下的名字，仅用于展示；读取失败不影响调用方。"""

        repository = chat_log.repository
        if not chat_log.enabled or repository is None:
            return {}
        try:
            return await repository.group_names()
        except Exception as error:
            logger.debug("WebUI group names unavailable: {}", type(error).__name__)
            return {}

    def _stamp(self):
        if self._config_path is None or not self._config_path.is_file():
            return None
        import hashlib

        return hashlib.sha256(self._config_path.read_bytes()).digest()

    def _secrets(self) -> tuple[str, ...]:
        values: list[str | None] = [*self._retired_tokens]
        if self.tenko_config is not None:
            values.append(self.tenko_config.onebot.access_token)
        if self.connection is not None:
            values.extend(
                (self.connection.adapter.access_token, self.connection.server.token)
            )
        return tuple(value for value in values if isinstance(value, str) and value)

    async def manage(
        self,
        request: Request,
        operation: Callable[[], Awaitable[T]],
        *,
        lock: bool = True,
    ) -> ApiResponse[T]:
        try:
            if lock:
                async with self._write_lock:
                    value = await operation()
            else:
                value = await operation()
        except ValueError as error:
            return _error_response("invalid_request", str(error), 400)
        except KeyError:
            return _error_response("not_found", "操作目标不存在", 404)
        except PermissionError as error:
            return _error_response("forbidden", str(error), 403)
        except DatabaseError:
            logger.error("WebUI database operation failed: {}", request.url.path)
            return _error_response(
                "database_unavailable", "数据库暂不可用，未确认保存成功", 503
            )
        except RuntimeError as error:
            return _error_response("conflict", str(error), 409)
        except Exception as error:
            # 凭据读写边界禁止记录请求体或异常 locals。
            logger.error(
                "WebUI operation failed: {} ({})",
                request.url.path,
                type(error).__name__,
            )
            return _error_response(
                "operation_failed", "操作失败，请检查宿主日志；未确认保存成功", 503
            )
        return _success_response(value)

    def require_management(self) -> AccountManagement:
        if (
            self.management is None
            or not self.management.ready
            or self.management.repository is None
        ):
            raise RuntimeError("管理数据库尚未就绪")
        return self.management

    def require_repository(self) -> ManagementRepository:
        """管理数据库的 repository；与 ``require_management`` 同样的就绪检查。"""

        repository = self.require_management().repository
        if repository is None:  # require_management 已检查，这里只为收窄类型
            raise RuntimeError("管理数据库尚未就绪")
        return repository

    def _pairing_data(self) -> PairingInfo:
        if self.connection is None or self.tenko_config is None:
            raise RuntimeError("协议连接未配置")
        config = self.tenko_config.onebot
        return {
            "url": config.reverse_ws_url,
            "path": config.reverse_ws_path_value,
            "requires_host": config.listen_host in {"0.0.0.0", "::"},
            "token_configured": bool(self.connection.adapter.access_token),
            "can_rotate": self._config_stamp is not None,
        }

    def update_config(
        self,
        mutate: Callable[[Any], None],
        check: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """只改动 ``mutate`` 涉及的键，注释、顺序和其他节逐字保留。

        ``mutate`` 先作用在 tomlkit 文档上（写文件），成功后再作用在内存里的配置上。
        写入前用 tomllib 和 :class:`TenkoConfig` 校验，临时文件加原子替换；文件在
        面板之外被改过时拒绝写入。
        """

        import tomllib

        import tomlkit

        native = self.tenko_config.entari_config if self.tenko_config else None
        if self._config_path is None or self._config_stamp is None or native is None:
            raise RuntimeError("没有可写回的配置文件")
        if self._config_path.suffix != ".toml":
            raise RuntimeError("只支持写回当前项目的 TOML 配置")
        if self._stamp() != self._config_stamp:
            raise RuntimeError("配置文件已被外部修改，请重启加载后再修改")
        document = tomlkit.parse(self._config_path.read_text(encoding="utf-8"))
        mutate(document)
        rendered = tomlkit.dumps(document)
        parsed = tomllib.loads(rendered)
        if check is not None:
            check(parsed)
        TenkoConfig.from_mapping(parsed)
        descriptor, name = tempfile.mkstemp(
            prefix=".tenko-config-", suffix=".toml", dir=self._config_path.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            if self._stamp() != self._config_stamp:
                raise RuntimeError("配置文件发生并发修改，请重试")
            os.replace(temporary, self._config_path)
        finally:
            temporary.unlink(missing_ok=True)
        self._config_stamp = self._stamp()
        mutate(native.data)
        # 跳过 Entari 对这次写入的自动重载。
        native.save_flag = True

    def raw_config(self) -> dict[str, Any]:
        """配置文件的原始内容（环境变量占位符未展开）。"""

        import tomllib

        if self._config_path is None:
            raise RuntimeError("没有可写回的配置文件")
        return tomllib.loads(self._config_path.read_text(encoding="utf-8"))

    def _rotate_token(self) -> str:
        if self.connection is None:
            raise RuntimeError("协议连接未配置")
        value = self.raw_config().get("onebot", {}).get("access_token", "")
        if isinstance(value, str) and "${" in value:
            raise RuntimeError("OneBot token 由环境变量提供，请在配置来源中轮换")
        token = secrets.token_urlsafe(32)

        def check(parsed: dict[str, Any]) -> None:
            if parsed.get("onebot", {}).get("access_token") != token:
                raise RuntimeError("配置保存未生成预期令牌")

        self.update_config(
            lambda data: data.setdefault("onebot", {}).__setitem__(
                "access_token", token
            ),
            check,
        )
        previous = self.connection.adapter.access_token
        if previous:
            self._retired_tokens.append(previous)
        self.connection.adapter.access_token = token
        return token

    async def pairing(self, request: Request) -> ApiResponse[PairingInfo]:
        async def action() -> PairingInfo:
            connection = self.connection
            if connection is None:  # _pairing_data 会抛同样的错，这里只为收窄类型
                raise RuntimeError("协议连接未配置")
            data = self._pairing_data()
            if request.method == "GET":
                return data
            body = await read_json(request)
            if body == {"action": "reveal"}:
                request.state.webui_action = "reveal"
                data["access_token"] = connection.adapter.access_token or ""
            elif (
                set(body) == {"action", "confirm"}
                and body["action"] == "rotate"
                and body["confirm"] is True
            ):
                request.state.webui_action = "rotate"
                data["access_token"] = self._rotate_token()
                data["token_configured"] = True
            else:
                raise ValueError("请选择显示凭据，或确认轮换令牌")
            return data

        return await self.manage(request, action)

    async def core_config(self, request: Request) -> ApiResponse[CoreConfigData]:
        async def action() -> CoreConfigData:
            writable = self._config_path is not None and self._config_stamp is not None
            raw = self.raw_config() if writable else {}
            if request.method == "POST":
                body = await read_json(request)
                changes = core_config.plan(
                    raw, body.get("values"), body.get("reset", [])
                )
                self.update_config(lambda data: core_config.apply(data, changes))
                request.state.webui_action = "config"
                raw = self.raw_config()
                self._restart_fields = ["*"]
                if self._apply_config is not None:
                    native = (
                        self.tenko_config.entari_config if self.tenko_config else None
                    )
                    if native is None:  # update_config 已检查，这里只为收窄类型
                        raise RuntimeError("没有可写回的配置文件")
                    config = TenkoConfig.from_mapping(native.data, entari_config=native)
                    self._restart_fields = self._apply_config(config)
                    self.tenko_config = config
            return {
                "fields": core_config.read(raw),
                "writable": writable,
                "restart_required": bool(self._restart_fields),
                "restart_fields": self._restart_fields,
                "can_restart": bool(getattr(self.market.updater, "enabled", False)),
            }

        return await self.manage(request, action)

    def _log_directory(self) -> Path | None:
        if self.tenko_config is None or self.tenko_config.basic.log.save is None:
            return None
        from arclet.entari.localdata import local_data

        return local_data._get_base_log_dir()

    async def log_view(self, request: Request) -> ApiResponse[LogData]:
        async def read() -> LogData:
            params = request.query_params
            query = params.get("q", "")
            if len(query) > 200:
                raise ValueError("搜索词不能超过 200 字")
            directory = self._log_directory()
            name = params.get("file")
            if name:
                history = await asyncio.to_thread(
                    self.logs.history, directory, name, query
                )
                return {
                    "lines": history["lines"],
                    "truncated": history["truncated"],
                    "matched": history["matched"],
                    "scan_limit_bytes": history["scan_limit_bytes"],
                    "files": await asyncio.to_thread(self.logs.files, directory),
                    "history_enabled": directory is not None,
                }
            after = int(params.get("after", "0"))
            if after < 0:
                raise ValueError("日志游标不能为负数")
            live = self.logs.live(after, query, params.get("level", ""))
            return {
                "records": live["records"],
                "cursor": live["cursor"],
                "reset": live["reset"],
                "truncated": live["truncated"],
                "files": await asyncio.to_thread(self.logs.files, directory),
                "history_enabled": directory is not None,
            }

        # 高频只读日志不产生审计日志，避免轮询形成递归日志流。
        return await self.manage(request, read)

    def _http_stopping(self) -> bool:
        # uvicorn 先自己接住 Ctrl-C 并等所有连接结束，之后 launart 才收到退出信号，
        # 所以长连接要直接看 uvicorn 的退出标志。
        uvicorn = getattr(getattr(self.server, "asgi_service", None), "server", None)
        return bool(getattr(uvicorn, "should_exit", False))

    def _live_marks(self) -> dict[str, int]:
        """内存里能廉价拿到的状态指纹；变化时推送 changed 让前端重拉。"""

        features = (self.feature_service.default_enabled, self.feature_service.state)
        return {
            "accounts": hash(json.dumps(self.account_panel.data(), sort_keys=True)),
            "features": hash(json.dumps(features, sort_keys=True, default=str)),
            "chat": chat_log.sequence,
            "sandbox": sandbox_module.sandbox.sequence if sandbox_module.sandbox else 0,
        }

    async def live_events(self, request: Request) -> Response:
        params = request.query_params
        topics = frozenset(filter(None, params.get("topics", "").split(",")))
        if not topics <= TOPICS:
            return _error_response("invalid_request", "未知的订阅主题", 400)
        if self.live.count >= MAX_STREAMS:
            return _error_response(
                "too_many_streams", "实时连接过多，请关闭多余的面板标签页", 429
            )
        # 断线重连时浏览器带上最后收到的日志编号，从那里续传。
        raw = request.headers.get("last-event-id") or params.get("after")
        try:
            cursor = int(raw) if raw else self.logs.sequence
        except ValueError:
            return _error_response("invalid_request", "日志游标无效", 400)
        return StreamingResponse(
            self.live.stream(request, topics, max(cursor, 0)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def index(self, request: Request) -> FileResponse:
        del request
        return FileResponse(
            _STATIC_INDEX,
            media_type="text/html",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    async def launch(self, manager: Launart) -> None:
        async with self.stage("preparing"):
            await self.wait_for_required()
            self._log_sink = logger.add(
                self.logs.sink, level="INFO", format="{message}"
            )
        async with self.stage("blocking"):
            await manager.status.wait_for_sigexit()
            self.live.closing.set()
        async with self.stage("cleanup"):
            if self._log_sink is not None:
                logger.remove(self._log_sink)
                self._log_sink = None


__all__ = ["WEBUI_PATH", "WebUIService"]
