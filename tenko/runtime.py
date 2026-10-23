from __future__ import annotations

import asyncio
import inspect
import signal
import sys
from collections.abc import Mapping
from pathlib import Path

import arclet.letoderea as le
from arclet.entari import Entari, SendRequest
from arclet.entari.event.command import CommandParse
from arclet.letoderea.utils import set_event_loop
from creart import it
from launart import Launart
from loguru import logger
from satori import EventType, LoginStatus
from satori.client import Account

from .config import TenkoConfig
from .commands import configure_command_prefix
from .connection import OneBotConnection
from .context import LEGACY_QQ_PLATFORM, QQ_PLATFORM
from .db.bootstrap import load_database_plugin
from .db.errors import DatabaseUnavailableError
from .db.runtime import RuntimeStateService
from .events import MessageEventHandler
from .host.chat_log import chat_log
from .host.metrics import configure_message_metrics
from .host import notify
from .host.accounts import account_registry
from .host.account_management import AccountManagement
from .host.actions import action_service
from .host.features import CommandPolicy, configure_feature_service
from .host.perm import permission_store
from .host.plugins.manager import get_plugin_manager
from .host.plugins.registry import configure_registry
from .host.plugins.runtime import PluginRuntime
from .host.quote import quote_group_reply
from .host.ratelimit import configure_rate_limiter
from .host.startup import startup_history
from .host.updater import UpgradeManager, configure_updater
from .host.render import RenderService
from .host.sandbox import (
    GuardedProtocol,
    SandboxAdapter,
    install as install_sandbox,
    is_sandbox,
)
from .webui import WebUIService


class TenkoRuntime:
    """Tenko 第一阶段运行时的服务编排。"""

    def __init__(self, config: TenkoConfig) -> None:
        self.config = config
        configure_command_prefix(())
        self.accounts = account_registry
        self.accounts.configure(None)
        self.actions = action_service
        self.feature_service = configure_feature_service(
            default_enabled=config.features.default_enabled,
        )
        self.rate_limiter = configure_rate_limiter(
            enabled=config.ratelimit.enabled,
            window_seconds=config.ratelimit.window_seconds,
            max_weight=config.ratelimit.max_weight,
            default_weight=config.ratelimit.default_weight,
            cooldown_seconds=config.ratelimit.cooldown_seconds,
            blacklist_seconds=config.ratelimit.blacklist_seconds,
        )
        startup_history.configure(None)
        notify.configure(config.notify_group)
        configure_registry(config.registry.urls)
        stable_root = Path.cwd().resolve()
        self.updater = UpgradeManager.from_config(
            config.upgrade,
            project_root=stable_root,
            database_url=config.database.url,
        )
        configure_updater(
            self.updater,
            superuser_ids=config.upgrade.superuser_ids,
            shutdown_callback=self.request_graceful_shutdown,
        )
        self.account_management = AccountManagement(self.accounts, self.actions)
        self.connection = OneBotConnection(config.onebot)
        self.connection.adapter.admission = self.account_management.can_connect
        # 命令沙盒只给面板用；不开 WebUI 时不注册这个虚拟平台。
        self.sandbox: SandboxAdapter | None = None
        if config.webui.enabled:
            self.sandbox = SandboxAdapter()
            self.connection.server.apply(self.sandbox)
        install_sandbox(self.sandbox)
        self.message_metrics = configure_message_metrics(
            config.exception.message_buffer_size
        )
        self.message_handler = MessageEventHandler(
            account_registry=self.accounts,
            debug_config=config.debug,
            command_prefix=config.command_prefix,
            metrics=self.message_metrics,
            action_service=self.actions,
            chat_log=chat_log,
        )
        self.app: Entari | None = None
        self.manager: Launart | None = None
        self.plugin_runtime: PluginRuntime | None = None
        self._startup_notify_module: object | None = None
        self.database_service = None
        self.webui_service: WebUIService | None = None
        self.runtime_state_service = RuntimeStateService(
            self._initialize_runtime_state,
            self._flush_runtime_state,
        )

    async def _check_plugin_environment(self) -> None:
        """加载插件前核对插件环境：删除已卸载插件的包；不一致时只告警，不自动联网安装。"""

        manager = get_plugin_manager()
        try:
            for problem in await asyncio.to_thread(manager.startup_check):
                logger.warning(problem)
            # 升级前选了兼容版本的停用插件，进入新宿主后在加载前装回。
            if resumed := await asyncio.to_thread(manager.resume_pending):
                logger.info("已恢复停用的插件: {}", "、".join(resumed))
        except Exception as error:  # noqa: BLE001 - uv 缺失等不应阻止启动
            logger.warning("无法核对插件环境: {}", error)

    def _configure_database_repositories(self) -> None:
        """将官方 database service 接入六类运行期状态。"""

        from .db.repositories import (
            account_state_repository,
            chat_message_repository,
            ManagementRepository,
            feature_state_repository,
            group_setting_repository,
            permission_repository,
            rate_limit_repository,
            startup_time_repository,
        )

        self.account_management.repository = ManagementRepository()
        permission_store.configure(permission_repository)
        self.accounts.configure(account_state_repository)
        self.feature_service.configure(
            feature_state_repository,
            default_enabled=self.config.features.default_enabled,
        )
        self.rate_limiter.configure(rate_limit_repository, group_setting_repository)
        startup_history.configure(startup_time_repository)
        log = self.config.chat_log
        chat_log.configure(
            chat_message_repository,
            enabled=log.enabled,
            retention_days=log.retention_days,
            max_messages=log.max_messages,
        )

    def _mark_runtime_state_unavailable(self) -> None:
        """数据库 bootstrap 失败时让运行期状态统一进入保守状态。"""

        self.accounts.mark_unavailable()
        self.feature_service.mark_unavailable()
        permission_store.mark_unavailable()
        self.rate_limiter.mark_unavailable()
        startup_history.mark_unavailable()

    async def _initialize_runtime_state(self) -> None:
        """在数据库服务就绪后加载快照；单类失败不阻止宿主启动。"""

        if self.account_management.repository is not None:
            try:
                renamed = await self.account_management.repository.rename_platform(
                    LEGACY_QQ_PLATFORM, QQ_PLATFORM
                )
                if renamed:
                    logger.info("已将 {} 行数据的平台名迁移为 {}", renamed, QQ_PLATFORM)
            except Exception as error:
                logger.error("Could not migrate legacy platform name: {}", error)

        state_loaders = (
            ("account routes", self.accounts.initialize),
            ("account preferences", self.account_management.initialize),
            ("feature switches", self.feature_service.initialize),
            ("permissions", permission_store.initialize),
            ("rate limit", self.rate_limiter.initialize),
            ("startup duration history", startup_history.load),
        )
        for label, loader in state_loaders:
            try:
                await loader()
            except Exception as error:
                logger.error("Could not load Tenko {} state: {}", label, error)

        if self.account_management.ready:
            self.account_management.ready = False
            try:
                await self.account_management.restore_settings(
                    self.feature_service, self.plugin_runtime
                )
            except Exception as error:
                logger.error(
                    "Could not restore management settings: {}", type(error).__name__
                )
            else:
                self.account_management.ready = True

    async def _flush_runtime_state(self) -> None:
        """在数据库连接销毁前等待同步事件安排的状态写入。"""

        try:
            await self.accounts.flush_persistence()
        except Exception as error:
            logger.error("Could not flush Tenko account route state: {}", error)
        try:
            await self.rate_limiter.persist_state()
        except Exception as error:
            logger.error("Could not flush Tenko rate limit state: {}", error)
        await chat_log.flush()

    @staticmethod
    def _plugin_module(loaded_plugins: object, name: str) -> object | None:
        """从 Entari loader 的结果取模块，兼容旧的 sys.modules 查找。"""

        loaded = (
            loaded_plugins.get(name) if isinstance(loaded_plugins, Mapping) else None
        )
        if loaded is not None:
            module = getattr(loaded, "module", None)
            return loaded if module is None else module
        return sys.modules.get(f"tenko.plugins.{name}")

    def request_graceful_shutdown(self) -> bool:
        """沿 Launart 的 SIGINT 生命周期路径请求当前运行时退出。"""

        manager = self.manager
        if manager is None:
            logger.warning("无法请求 Tenko 优雅退出：Launart manager 尚未就绪")
            return False

        # Launart 0.8.2 的 SIGINT 处理器通过同一组状态转换唤醒 cleanup；
        # 这里不取消当前命令任务，确保回复已经交给 Entari 的发送链路。
        manager.status.exiting = True
        task_group = manager.task_group
        if task_group is None:
            logger.warning("无法请求 Tenko 优雅退出：Launart task group 尚未就绪")
            return False
        task_group.stop = True
        if task_group.blocking_task is not None:
            task_group.blocking_task.cancel()
        return True

    def build_app(self) -> Entari:
        if self.app is not None:
            return self.app

        set_event_loop(asyncio.get_running_loop())
        basic = self.config.basic
        # 不配置 Satori 网络连接：OneBot 事件由进程内 Server 直接交给 app。
        app = Entari(
            log_level=basic.log.level,
            ignore_self_message=basic.ignore_self_message,
            skip_req_missing=basic.skip_req_missing,
            external_dirs=basic.external_dirs,
            rich_error=basic.log.rich_error,
            gen_schema=basic.schema,
        )
        # 账号在登录时按它创建协议对象，所以要在任何账号接入之前替换。
        app.default_api_cls = GuardedProtocol
        native_handler = app.handle_event
        for index, callback in enumerate(app.event_callbacks):
            if callback == native_handler:
                app.event_callbacks[index] = self.message_handler.guard(callback)
                break
        else:  # pragma: no cover - Entari 在 __init__ 中注册此回调
            raise RuntimeError("Entari native event handler is not registered")
        # OneBot 11 的 group_decrease.leave/kick 在当前适配器中都先转换为
        # GUILD_MEMBER_REMOVED；kick_me 转换为 GUILD_REMOVED。先注册退群
        # 事件，再注册消息事件，保留原有消息回调作为最后一个可观察入口。
        app.register_on(EventType.GUILD_MEMBER_REMOVED)(
            self.message_handler.handle_member_removed
        )
        app.register_on(EventType.GUILD_REMOVED)(
            self.message_handler.handle_member_removed
        )
        # OneBot 11 的适配器在退群事件补充信息的 action 失败时，会把原始
        # notice 保留在 EventType.INTERNAL；处理器按已核实的 _type/_data
        # 再识别一次，避免 kick_me 因“已被踢后无法查成员信息”丢失解绑。
        app.register_on(EventType.INTERNAL)(self.message_handler.handle_member_removed)
        app.register_on(EventType.INTERNAL)(self._on_internal)
        app.register_on(EventType.MESSAGE_CREATED)(self.message_handler.handle)
        app.lifecycle(self._on_lifecycle)
        self.app = app
        return app

    async def _on_internal(self, account: Account, event: object) -> None:
        reason = notify.bot_offline_reason(event)
        if reason is not None:
            logger.warning(
                "OneBot account {} was logged out: {}", account.self_id, reason
            )
            notify.account_alerts.kicked(account, reason)

    async def _on_lifecycle(self, account: Account, state: LoginStatus) -> None:
        if is_sandbox(account):
            # 沙盒账号不进账号路由，也不发上线通知。
            return
        if state in (LoginStatus.ONLINE, LoginStatus.CONNECT, LoginStatus.RECONNECT):
            self.accounts.register(account, available=state == LoginStatus.ONLINE)
            if state == LoginStatus.ONLINE:
                try:
                    group_ids = await self.actions.get_group_list(account)
                except Exception as error:
                    logger.warning(
                        "Could not discover groups for OneBot account {}: {}",
                        account.self_id,
                        error,
                    )
                else:
                    for group_id in group_ids:
                        self.accounts.bind_group(group_id, account)
                    logger.info(
                        "Discovered {} groups for OneBot account {}",
                        len(group_ids),
                        account.self_id,
                    )
        elif state in (LoginStatus.DISCONNECT, LoginStatus.OFFLINE):
            if self.accounts.get(account) is not None:
                self.accounts.set_available(account, False)
                notify.account_alerts.offline(account)

        if state == LoginStatus.ONLINE:
            logger.info("OneBot account {} is online", account.self_id)
            notify.account_alerts.online(account)
            startup_notify = self._startup_notify_module
            if startup_notify is None:
                # 保留对测试替身和直接导入插件的兼容；正常的 Entari 插件
                # 由自定义 loader 管理，不一定留在 sys.modules 中。
                startup_notify = sys.modules.get("tenko.plugins.startup_notify")
            on_account_online = getattr(startup_notify, "on_account_online", None)
            if callable(on_account_online):
                try:
                    result = on_account_online(account)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    # 启动通知是就绪后的附加动作，不能影响账号已上线的主流程。
                    logger.exception("Could not dispatch Tenko startup notification")
        elif state in (LoginStatus.CONNECT, LoginStatus.RECONNECT):
            logger.info("OneBot account {} is connecting/reconnecting", account.self_id)
        elif state in (LoginStatus.DISCONNECT, LoginStatus.OFFLINE):
            logger.warning(
                "OneBot account {} disconnected; waiting for reverse WebSocket "
                "reconnection",
                account.self_id,
            )
        else:
            logger.warning(
                "OneBot account {} changed to unknown state {}",
                account.self_id,
                state,
            )

    async def run_async(self) -> None:
        # Entari 的 service provider 通过 creart 的 Launart 实例解析组件；这里
        # 使用与 App.run_async 接收的同一个 manager。
        manager = it(Launart)
        self.manager = manager
        self.connection.install(manager)

        logger.info(
            "Tenko starting; OneBot 11 reverse WebSocket endpoint: {}",
            self.config.onebot.reverse_ws_url,
        )
        app = self.build_app()
        self.connection.attach(app, manager)
        try:
            self.database_service = load_database_plugin(
                self.config.database,
                runtime_state_service=self.runtime_state_service,
            )
            self._configure_database_repositories()
        except DatabaseUnavailableError as error:
            self.database_service = None
            self._mark_runtime_state_unavailable()
            logger.error("Tenko database is unavailable: {}", error)
        manager.add_component(
            RenderService(
                timeout=self.config.render.timeout,
                width=self.config.render.width,
                quality=self.config.render.quality,
                device_scale_factor=self.config.render.device_scale_factor,
            )
        )

        # PluginRuntime.load_all() 只通过 Entari 的 load_plugin API 导入 Tenko
        # 插件，因此不需要注册 Launart 组件。将其放在数据库模型和
        # RenderService 注册之后、run_async 之前。
        self.plugin_runtime = PluginRuntime()
        await self._check_plugin_environment()
        # 宿主配置作为 Entari 插件配置在加载时传入，插件模块导入后即可使用。
        loaded_plugins = await self.plugin_runtime.load_all(
            {
                "webui_account": {
                    "enabled": self.config.webui.enabled,
                    "public_url": self.config.webui.public_url,
                },
                "exception_catcher": {
                    "evidence_dir": self.config.exception.evidence_dir
                },
            },
            external_configs=self.config.plugin_config,
        )
        self._startup_notify_module = self._plugin_module(
            loaded_plugins, "startup_notify"
        )
        # 策略挂在 Entari 已解析出命令的 CommandParse 事件上，返回 False 即阻断；
        # 订阅在根作用域，随 Entari 清理一起释放。
        policy = CommandPolicy(
            self.feature_service,
            self.rate_limiter,
            plugin_runtime=self.plugin_runtime,
            command_prefix=self.config.command_prefix,
            rate_limit_override_permission=self.config.ratelimit.override_permission,
        )
        le.on(CommandParse, policy.enforce, priority=0)
        le.on(SendRequest, quote_group_reply)
        if self.config.webui.enabled:
            feature_repository = None
            if self.database_service is not None:
                from .db.repositories import feature_state_repository

                feature_repository = feature_state_repository

            self.webui_service = WebUIService(
                self.connection.server,
                self.config.webui,
                accounts=self.accounts,
                metrics=self.message_metrics,
                feature_service=self.feature_service,
                feature_repository=feature_repository,
                plugin_runtime=self.plugin_runtime,
                management=self.account_management,
                connection=self.connection,
                tenko_config=self.config,
            )
            manager.add_component(self.webui_service)
        # 注意（pre8 生产事故根因）：RuntimeStateService 与官方
        # SqlalchemyService 都经插件机制注册，由 PluginManagerService 在其
        # launch 阶段统一 resolve_requirements 后装载。两者都不可加入
        # app.required，也不可在此提前 add_component——Launart 顶层解析发生在
        # 插件服务装载之前，任何一者提前进入组件表都会让解析因依赖缺失而
        # 整体崩溃（RequirementResolveFailed）。启动时序由 RuntimeStateService
        # 自身的 required={"database/sqlalchemy"} 与 wait_for_required 保证。
        await app.run_async(
            manager,
            stop_signal=(signal.SIGINT, signal.SIGTERM),
        )


def run(config: TenkoConfig) -> None:
    asyncio.run(TenkoRuntime(config).run_async())
