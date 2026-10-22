from __future__ import annotations

import asyncio

from arclet.alconna import Alconna, CommandMeta, Option, store_true
from arclet.entari import Session, command, plugin
from arclet.entari.command import Query
from arclet.entari.plugin import PluginRole

from tenko.context import context_from_session
from tenko.host.guards import require
from tenko.host.perm import Permission, PermissionChecker
from tenko.host.render import RenderService
from tenko.host.status import build_status_data, get_version_details
from tenko.plugins._common import render_or_text


plugin.metadata(
    "状态查询",
    PluginRole.NORMAL,
    author=["13"],
    version="0.1.0",
    description="查询 Tenko 当前会话、资源、消息统计和账号路由状态。",
    classifier=["required"],
)


permission_checker = PermissionChecker()


def _plugin_count() -> int:
    """与 /帮助 一致：统计已加载的业务插件，未启用的第三方插件不计入。"""

    from tenko.host.plugins.runtime import PluginRuntime  # 局部导入避免插件加载顺序耦合

    return len(PluginRuntime().loaded_plugins)


status_command = Alconna(
    "状态",
    Option(
        "-t",
        alias=["--text"],
        action=store_true,
        default=False,
        help_text="强制使用文本输出",
    ),
    meta=CommandMeta(
        "查询 Tenko 运行状态",
        usage="状态 [-t]",
        example="/状态 -t",
        compact=True,
    ),
)


@command.on(status_command)
@require(Permission.User)
async def status(
    session: Session,
    text_mode: Query[bool] = Query("t.value", False),
    *,
    render_service: RenderService,
):
    context = context_from_session(session)
    is_master_private = (
        context.chat_type == "private"
        and await permission_checker.require_perm(context, Permission.Master)
    )
    status_data = build_status_data(
        context,
        _plugin_count(),
        detailed=is_master_private,
        # 首次调用会同步执行 git 子进程，放到线程里避免阻塞事件循环。
        version_details=await asyncio.to_thread(get_version_details),
    )
    text = str(status_data["content"])
    if text_mode.result:
        return text
    return await render_or_text(render_service, "status.html", status_data, text)
