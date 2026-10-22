"""通知设置：Master 私聊查看或修改给 Master 的通知发往哪里、由哪个账号发送，立即生效。"""

from __future__ import annotations

from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import command, plugin
from arclet.entari.command import Query
from arclet.entari.plugin import PluginRole

from tenko.db.errors import DatabaseUnavailableError
from tenko.host.guards import private_only, require
from tenko.host.notify import describe_settings, load_settings, notify, save_settings
from tenko.host.perm import Permission

plugin.metadata(
    "通知设置",
    PluginRole.NORMAL,
    author=["13"],
    description="设置启动通知、异常报告、好友申请等通知发往私聊还是通知群，以及由哪个账号发送。",
    classifier=["required", "host"],
)


notify_command = Alconna(
    "通知设置",
    Args["action?", "私聊|群聊|账号|测试"]["value?", str],
    meta=CommandMeta(
        "查看或修改给 Master 的通知（Master 私聊）",
        usage="通知设置 [私聊 | 群聊 [群号] | 账号 <QQ号|自动> | 测试]",
        example="/通知设置\n/通知设置 群聊 123456\n/通知设置 账号 10001\n/通知设置 测试",
        compact=False,
    ),
)


@command.on(notify_command)
@private_only()
@require(Permission.Master)
async def notify_settings(
    action: Query[str] = Query("action"), value: Query[str] = Query("value")
):
    argument = value.result if value.available else None
    try:
        if not action.available:
            return describe_settings(await load_settings())
        if action.result == "测试":
            if await notify("🔔 这是一条测试通知，收到说明通知设置可用。"):
                return "测试通知已发出"
            return "测试通知没有全部送达，请查看日志"
        if action.result == "私聊":
            changes = {"mode": "private"}
        elif action.result == "群聊":
            changes = {"mode": "group"} | ({"group": argument} if argument else {})
        elif argument is None:
            return "请写明账号，例如 /通知设置 账号 10001；/通知设置 账号 自动 表示任选在线账号"
        else:
            changes = {"account": None if argument == "自动" else argument}
        settings = await save_settings(changes)
    except ValueError as error:
        return f"设置失败：{error}"
    except DatabaseUnavailableError:
        return "设置数据库暂不可用，请稍后再试"
    return "已保存，立即生效\n" + describe_settings(settings)
