"""WebUI 面板账号管理：Master 在私聊中查询、重置密码和强制下线。"""

from __future__ import annotations

from datetime import UTC, datetime

from arclet.alconna import Alconna, CommandMeta
from arclet.entari import BasicConfModel, Session, command, plugin
from arclet.entari.plugin import PluginRole, plugin_config

from tenko.host.guards import private_only, require
from tenko.host.perm import Permission
from tenko.webui.auth import default_repository, reset_password


plugin.metadata(
    "面板账号",
    PluginRole.NORMAL,
    author=["13"],
    version="0.1.0",
    description="Master 在私聊中查看面板账号、重置密码并让所有登录会话下线。",
    classifier=["required", "host"],
)


class Config(BasicConfModel):
    """由宿主按 ``[webui]`` 传入，用于生成回复中的面板地址。"""

    enabled: bool = True
    public_url: str | None = None


config = plugin_config(Config)


account_command = Alconna(
    "面板账号",
    meta=CommandMeta(
        "查看面板账号、会话数量和访问地址",
        usage="面板账号",
        example="/面板账号",
        compact=True,
    ),
)
reset_command = Alconna(
    "面板重置密码",
    meta=CommandMeta(
        "生成新的面板登录密码，并让所有已登录会话下线",
        usage="面板重置密码",
        example="/面板重置密码",
        compact=True,
    ),
)
logout_command = Alconna(
    "面板下线",
    meta=CommandMeta(
        "让所有面板登录会话下线",
        usage="面板下线",
        example="/面板下线",
        compact=True,
    ),
)


def _panel_url() -> str:
    if not config.enabled:
        return "WebUI 未启用（在配置中设置 [webui] enabled = true 后重启）"
    if config.public_url:
        return f"{config.public_url}/webui"
    return "/webui（请在配置 [webui] 中设置 public_url 以显示完整访问地址）"


@command.on(account_command)
@private_only()
@require(Permission.Master)
async def show_account(session: Session):
    repository = default_repository()
    account = await repository.get_account()
    count = await repository.count_sessions(datetime.now(UTC))
    return (
        f"面板用户名：{account.username if account else 'master（尚未设置密码）'}\n"
        f"密码状态：{'已设置' if account else '未设置，请发送 /面板重置密码'}\n"
        f"在线会话：{count}\n"
        f"面板地址：{_panel_url()}"
    )


@command.on(reset_command)
@private_only()
@require(Permission.Master)
async def reset_account_password(session: Session):
    username, password, revoked = await reset_password()
    return (
        "面板密码已重置，所有登录会话已下线"
        f"（{revoked} 个）。\n"
        f"用户名：{username}\n"
        f"密码：{password}\n"
        f"面板地址：{_panel_url()}\n"
        "请登录后尽快在面板中修改密码，并撤回或删除这条消息。"
    )


@command.on(logout_command)
@private_only()
@require(Permission.Master)
async def revoke_sessions(session: Session):
    count = await default_repository().delete_sessions()
    return f"已让 {count} 个面板会话下线"
