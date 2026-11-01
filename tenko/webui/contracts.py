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


# ---------------------------------------------------------------- 功能开关与插件启停


class PluginIconRef(TypedDict):
    """glyph 为单色 SVG（按主题着色），否则是目录里的 PNG 图标。"""

    url: str
    glyph: bool


# builtin 内置；external 扩展插件（tenko.plugins 入口，默认关闭）；
# unmanaged 是 Entari 里已注册命令、但未在 Tenko 登记的原生插件。
FeatureSource = TypeAliasType(
    "FeatureSource", Literal["builtin", "external", "unmanaged"]
)


class FeatureInfo(TypedDict):
    plugin: str
    """插件标识，用作 /manage/plugins|features/{plugin} 的路径参数。"""
    name: str
    """展示名。"""
    description: str
    icon: PluginIconRef | None
    """未在插件目录登记的残留开关记录没有图标。"""
    scope: str
    """开关范围：group 按群开关，global 全局开关；其余值按“其他”展示。"""
    source: FeatureSource
    local: bool
    """本地插件目录（.tenko/plugins/local/）里的扩展插件。"""
    protected: bool
    """控制平面插件，不能通过面板停用或改开关。"""
    controlled: bool
    """受控插件：默认关闭，群里需 BOT 管理员开启，私聊仅 BOT 管理员可用。"""
    controlled_declared: bool
    """插件自己声明的受控状态；与 controlled 不同时说明 Master 在面板改过。"""
    loaded: bool
    enabled: bool
    maintenance: bool
    global_enabled: bool | None
    """全局覆盖：未设置为 null。"""
    groups: dict[str, bool]
    """群 ID → 该群当前生效的开关状态。"""


class FeaturesData(TypedDict):
    features: list[FeatureInfo]
    group_names: dict[str, str]
    """群 ID → 群名称；仅包含有可展示名称的群。"""


class PluginToggleBody(TypedDict):
    enabled: bool
    confirm: Literal[True]


class PluginToggleResult(TypedDict):
    plugin: str
    enabled: bool
    restart_required: bool
    """扩展插件已启用但尚未加载，需重启后生效。"""
    restart_reason: str | None
    """首次启用的扩展插件没能直接加载的原因。"""


# POST /manage/features/{plugin} 的四种请求体：维护开关、受控覆盖、全局开关、群级开关。


class FeatureMaintenanceBody(TypedDict):
    maintenance: bool


class FeatureControlledBody(TypedDict):
    controlled: bool | None
    """null 恢复插件声明的受控状态。"""


class FeatureGlobalBody(TypedDict):
    group_id: None
    enabled: bool | None
    """null 清除全局覆盖。"""


class FeatureGroupBody(TypedDict):
    group_id: str
    enabled: bool


FeatureToggleBody = TypeAliasType(
    "FeatureToggleBody",
    FeatureMaintenanceBody
    | FeatureControlledBody
    | FeatureGlobalBody
    | FeatureGroupBody,
)


class FeatureState(TypedDict):
    maintenance: NotRequired[bool]
    global_enabled: NotRequired[bool]
    groups: NotRequired[dict[str, bool]]


class FeatureToggleResult(TypedDict):
    plugin: str
    state: NotRequired[FeatureState]
    controlled: NotRequired[bool]


# ---------------------------------------------------------------- 黑名单 /manage/blacklist

BlacklistKind = TypeAliasType("BlacklistKind", Literal["user", "group"])


class BlacklistEntry(TypedDict):
    platform: str
    id: str
    name: str
    """群名称（取自聊天记录），用户条目为空。"""
    reason: str
    operator_id: str
    """命令操作人的用户 ID；面板操作为 webui。"""
    created_at: str | None


class BlacklistData(TypedDict):
    ready: bool
    """权限状态是否已从数据库加载；未就绪时不能修改。"""
    users: list[BlacklistEntry]
    groups: list[BlacklistEntry]
    platforms: list[str]
    """可选的平台：已知账号的平台和已有条目的平台。"""


class BlacklistBody(TypedDict):
    kind: BlacklistKind
    platform: str
    id: str
    blacklisted: bool
    reason: NotRequired[str]


# ---------------------------------------------------------------- 全局设置 /manage/settings

FriendRequestMode = TypeAliasType("FriendRequestMode", Literal["off", "admins", "all"])


class NotifySettings(TypedDict):
    """给 Master 的通知：发往私聊还是通知群，由哪个账号发送（null 表示任选在线账号）。"""

    mode: Literal["private", "group"]
    group: str | None
    account: str | None


class NotifySettingsChanges(TypedDict, total=False):
    """通知设置的局部修改：只写要改的字段。"""

    mode: Literal["private", "group"]
    group: str | None
    account: str | None


class SettingsData(TypedDict):
    default_enabled: bool
    stored: bool
    friend_request_mode: FriendRequestMode
    notify: NotifySettings


class SettingsBody(TypedDict):
    default_enabled: NotRequired[bool]
    friend_request_mode: NotRequired[FriendRequestMode]
    notify: NotRequired[NotifySettingsChanges]


# ---------------------------------------------------------------- 收件箱 /manage/inbox


class InboxItem(TypedDict):
    """待处理的好友申请和群邀请；时间为 ISO 8601 UTC。"""

    id: str
    kind: Literal["friend", "group"]
    platform: str
    account_id: str
    """收到请求的 bot 账号。"""
    user_id: str | None
    user_name: str
    comment: str
    group_id: str | None
    group_name: str | None
    created_at: str
    expires_at: str | None
    """超时自动拒绝的时间；好友申请没有。"""


class InboxData(TypedDict):
    items: list[InboxItem]


InboxAction = TypeAliasType("InboxAction", Literal["approve", "reject", "dismiss"])
