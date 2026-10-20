from __future__ import annotations

from arclet.entari import Ready, plugin
from arclet.entari.plugin import PluginRole, plugin_config

from tenko import _process_start_monotonic
from tenko.host.actions import action_service
from tenko.host.features import feature_service
from tenko.host.perm import PermissionChecker
from tenko.host.startup import StartupNotifier, startup_history
from tenko.host.updater import get_upgrade_manager
from tenko.plugins._common import NotifyGroupConfig


plugin.metadata(
    "启动通知",
    PluginRole.NORMAL,
    author=["13"],
    version="0.1.0",
    description="在 Tenko 与账号服务就绪后播报启动耗时。",
    classifier=["required"],
)
# 启动通知是宿主级开关，但仍允许 feature_manager 通过 Tenko 功能状态动态控制。
plugin.get_plugin().metadata.feature_scope = "global"


config = plugin_config(NotifyGroupConfig)
permission_checker = PermissionChecker()
# 启动起点、耗时历史和升级目录都是宿主级单例，运行时在加载插件前已经配置好。
notifier = StartupNotifier(
    notify_group=config.notify_group,
    started_at=_process_start_monotonic,
    history=startup_history,
    recovery_notice_path=get_upgrade_manager().layout.recovery_notice_file,
    action_service=action_service,
    feature_service=feature_service,
    permission_checker=permission_checker,
)


async def on_account_online(account: object) -> None:
    await notifier.mark_account_online(account)


@plugin.listen(Ready)
async def on_ready() -> None:
    await notifier.mark_framework_ready()
