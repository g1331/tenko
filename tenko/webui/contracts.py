"""WebUI 接口的数据契约：前后端共用的唯一定义。

这里的 TypedDict 和类型别名同时服务两端：

- 后端处理函数返回 ``ApiResponse[契约]``（见 ``auth.py``），mypy 据此检查载荷；
  测试里的契约检查再用它核对真实响应（``tests/tenko/conftest.py``）。
- ``scripts/webui_schema.py`` 把本模块导出为 JSON Schema，
  ``webui`` 的 ``npm run gen:types`` 再生成 ``webui/src/api/generated.ts``。

本模块里定义的每个 TypedDict 和 ``TypeAliasType`` 都会生成同名的 TypeScript 类型；
类和字段下方的文档字符串会成为生成代码里的注释。修改后运行 ``npm run gen:types``
并提交生成结果，CI 会检查两者是否一致。

约定：时间字段都是 ISO 8601 UTC 字符串，群 ID、账号 ID 一律是字符串。TypedDict
从 ``typing_extensions`` 导入：pydantic 在 Python 3.12 以下只接受它。
"""

from __future__ import annotations

from typing import Literal, NotRequired

from typing_extensions import TypeAliasType, TypedDict

# ---------------------------------------------------------------- 通用


class Empty(TypedDict):
    """没有内容的成功响应。"""


# ---------------------------------------------------------------- 登录与会话


class AuthState(TypedDict):
    authenticated: bool
    username: str | None
    password_set: bool
    expires_at: str | None


class LoginBody(TypedDict):
    username: str
    password: str


class LoginResult(TypedDict):
    username: str
    expires_at: str


class SessionInfo(TypedDict):
    id: str
    created_at: str
    last_seen_at: str
    expires_at: str
    ip: str
    user_agent: str
    current: bool


class SessionsData(TypedDict):
    sessions: list[SessionInfo]


class AccountUpdateBody(TypedDict):
    """修改用户名和/或密码；二者都不传时后端只校验当前密码。"""

    current_password: str
    username: NotRequired[str]
    new_password: NotRequired[str]


class AccountUpdateResult(TypedDict):
    username: str


class RevokeOthersResult(TypedDict):
    revoked: int


# ---------------------------------------------------------------- 总览


class HostResources(TypedDict):
    cpu_percent: float
    memory_used: int
    memory_total: int
    memory_percent: float
    disk_used: int
    disk_total: int
    disk_percent: float
    net_sent: int
    net_received: int


class MessageCounts(TypedDict):
    received_count: int
    sent_count: int
    received_rate: int
    """最近 60 秒的收发条数。"""
    sent_rate: int


class OverviewData(TypedDict):
    version: str
    uptime_seconds: float
    rss: int | None
    """Tenko 进程常驻内存（字节）；读取不到时为 null。"""
    resources: HostResources | None
    """主机资源（字节 / 百分比）；未安装 psutil 等读取不到时为 null。"""
    activity: list[tuple[int, int]]
    """近 60 分钟每分钟的 [接收, 发送]，最早的在前，最后一格是当前分钟。"""
    online_accounts: int
    active_groups: int
    inbox: int
    """待处理的好友申请和群邀请数。"""
    plugin_attention: NotRequired[int]
    """需要处理的插件：停用待恢复的和本次启动加载失败的。"""
    messages: MessageCounts


# ---------------------------------------------------------------- 账号与群

# 账号在某群的状态，优先级：停用 > 离线 > 禁言。
GroupAccountState = TypeAliasType(
    "GroupAccountState", Literal["available", "muted", "offline", "disabled"]
)
# 群当前不会响应的原因：群在黑名单、没有可用账号，或定向账号不可用。
SilentReason = TypeAliasType(
    "SilentReason", Literal["blacklisted", "no_account", "target_unavailable"]
)


class AccountSummary(TypedDict):
    id: str
    ref: str
    """写接口里引用该账号的键；OneBot 下等于 id。"""
    platform: str
    name: str | None
    """协议端昵称；尚未取到时为 null。"""
    online: bool
    group_count: int


class GroupAccount(TypedDict):
    ref: str
    id: str
    platform: str
    name: str | None
    state: GroupAccountState
    role: Literal["owner", "admin", "member"] | None
    """只在执行过管理动作后才知道。"""
    muted_until: str | None
    """禁言到期时间；永久禁言或未禁言为 null。"""


class GroupStatus(TypedDict):
    group_id: str
    strategy: str
    """已知策略为 random / deterministic，其余值按“其他”展示。"""
    deterministic_account: str | None
    """定向模式下负责应答的账号（ref）。"""
    silent_reason: SilentReason | None
    accounts: list[GroupAccount]
    """按绑定顺序排列，包含本次运行尚未连接的账号。"""


class AccountsData(TypedDict):
    accounts: list[AccountSummary]
    groups: list[GroupStatus]


# ---------------------------------------------------------------- 管理接口 /manage/*

# 能力覆盖：true 强制允许，false 强制禁用，缺省表示自动探测。
CapabilityOverrides = TypeAliasType("CapabilityOverrides", dict[str, bool])


class ManagedAccount(TypedDict):
    platform: str
    id: str
    alias: str
    enabled: bool
    capabilities: CapabilityOverrides
    online: bool
    available: bool
    group_count: int
    groups: list[str]
    connected: bool
    """协议端当前是否持有连接。"""
    name: str
    """展示名（协议端用户名，缺省为账号 ID）。"""
    manageable: bool
    """仅 QQ（qq-client）账号可管理。"""


class ManagedAccountsData(TypedDict):
    accounts: list[ManagedAccount]
    capabilities: list[str]
    """可覆盖的能力名，如 send_group_message、member_mute……"""


AccountAction = TypeAliasType(
    "AccountAction", Literal["preferences", "enable", "disable", "kick", "forget"]
)


class AccountPreferencesBody(TypedDict):
    """``preferences`` 动作的请求体：别名和能力覆盖。"""

    alias: NotRequired[str]
    capabilities: NotRequired[CapabilityOverrides]


class AccountConfirmBody(TypedDict):
    """``enable`` / ``disable`` / ``kick`` / ``forget`` 动作的请求体。"""

    confirm: Literal[True]


AccountActionBody = TypeAliasType(
    "AccountActionBody", AccountPreferencesBody | AccountConfirmBody
)


class AccountActionResult(TypedDict):
    action: AccountAction
    platform: str
    id: str


class PairingInfo(TypedDict):
    url: str
    path: str
    requires_host: bool
    """监听 0.0.0.0 / :: 时为 true，需要用户自己填写协议端可达的主机。"""
    token_configured: bool
    can_rotate: bool
    access_token: NotRequired[str]
    """仅在 reveal / rotate 的响应中出现。"""


class PairingRevealBody(TypedDict):
    action: Literal["reveal"]


class PairingRotateBody(TypedDict):
    action: Literal["rotate"]
    confirm: Literal[True]


PairingActionBody = TypeAliasType(
    "PairingActionBody", PairingRevealBody | PairingRotateBody
)


class GroupResponseBody(TypedDict):
    strategy: Literal["random", "deterministic"]
    account: str | None
    """仅定向可非空；null 表示默认（第一个绑定的）账号。"""


class GroupResponseResult(TypedDict):
    group_id: str
    strategy: Literal["random", "deterministic"]
    deterministic_account: str | None
