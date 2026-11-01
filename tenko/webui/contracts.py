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


# ---------------------------------------------------------------- 聊天与命令沙盒


class ChatTextSegment(TypedDict):
    type: Literal["text"]
    text: str


class ChatAtSegment(TypedDict):
    type: Literal["at"]
    id: str
    name: str


class ChatFaceSegment(TypedDict):
    type: Literal["face"]
    id: str
    name: str


class ChatImageSegment(TypedDict):
    type: Literal["image"]
    src: str
    """为空表示图片没有可用地址（例如渲染出的内嵌图片不入库）。"""


class ChatQuoteSegment(TypedDict):
    type: Literal["quote"]
    id: str
    user_id: str
    name: str
    text: str


class ChatOtherSegment(TypedDict):
    type: Literal["other"]
    name: str


# 聊天消息段：后端把 Satori 元素归一化成这几种，未知元素是 other 占位。
ChatSegment = TypeAliasType(
    "ChatSegment",
    ChatTextSegment
    | ChatAtSegment
    | ChatFaceSegment
    | ChatImageSegment
    | ChatQuoteSegment
    | ChatOtherSegment,
)


class ChatMessage(TypedDict):
    id: int
    """记录编号，用作翻页和增量读取的游标。"""
    message_id: str | None
    """平台消息 ID，回复时引用它。"""
    outgoing: bool
    user_id: str
    user_name: str
    content: list[ChatSegment]
    created_at: str


ChatType = TypeAliasType("ChatType", Literal["group", "private"])


class ChatConversationLast(TypedDict):
    preview: str
    user_name: str
    outgoing: bool
    created_at: str


class ChatConversation(TypedDict):
    channel_id: str
    """群号，或 private:<QQ号>。"""
    chat_type: ChatType
    peer_id: str
    """群号或私聊对方的 QQ 号。"""
    name: str
    last: ChatConversationLast | None
    muted: bool


class ChatConversationsData(TypedDict):
    enabled: bool
    """消息记录是否开启；关闭时仍可发送，但看不到历史。"""
    retention_days: int
    online: bool
    conversations: list[ChatConversation]


class ChatMessagesData(TypedDict):
    messages: list[ChatMessage]
    has_more: bool


class ChatSendTextSegment(TypedDict):
    type: Literal["text"]
    text: str


class ChatSendAtSegment(TypedDict):
    type: Literal["at"]
    id: str
    name: NotRequired[str]


class ChatSendImageSegment(TypedDict):
    type: Literal["image"]
    src: str


class ChatSendQuoteSegment(TypedDict):
    type: Literal["quote"]
    id: str


# 发送时可用的消息段；回复只能在最前面。
ChatSendSegment = TypeAliasType(
    "ChatSendSegment",
    ChatSendTextSegment
    | ChatSendAtSegment
    | ChatSendImageSegment
    | ChatSendQuoteSegment,
)


class ChatSendBody(TypedDict):
    platform: str
    account: str
    channel: str
    content: list[ChatSendSegment]


class SentResult(TypedDict):
    sent: bool


SandboxChat = TypeAliasType("SandboxChat", Literal["group", "private"])
SandboxIdentity = TypeAliasType(
    "SandboxIdentity", Literal["member", "admin", "owner", "master"]
)


class SandboxBot(TypedDict):
    id: str
    name: str


class SandboxGroup(TypedDict):
    id: str
    name: str


class SandboxIdentityInfo(TypedDict):
    value: SandboxIdentity
    user_id: str
    name: str


class SandboxData(TypedDict):
    """命令沙盒：面板扮演用户和 bot 对话，记录只在内存里。"""

    ready: bool
    bot: SandboxBot
    group: SandboxGroup
    identities: list[SandboxIdentityInfo]
    messages: list[ChatMessage]


class SandboxSendBody(TypedDict):
    chat: SandboxChat
    identity: SandboxIdentity
    content: list[ChatSendSegment]


class SandboxClearResult(TypedDict):
    cleared: bool


class SandboxResetResult(TypedDict):
    reset: bool


# ---------------------------------------------------------------- 调用统计 /manage/stats

StatsDays = TypeAliasType("StatsDays", Literal[1, 7, 30])


class StatsReason(TypedDict):
    reason: str
    label: str
    count: int


class StatsSummary(TypedDict):
    key: str
    calls: int
    ok: int
    errors: int
    denied: int
    success_rate: float | None
    avg_ms: int | None
    max_ms: int
    avg_render_ms: int | None
    replies: int
    reasons: list[StatsReason]
    actions: dict[str, int]


class StatsGroup(StatsSummary):
    name: str


class StatsPlugin(StatsSummary):
    label: str
    commands: list[StatsSummary]
    groups: list[StatsGroup]


class StatsFailure(TypedDict):
    id: int
    at: str
    chat: str
    """group / private；非消息会话（other）也会出现。"""
    group_id: str
    group_name: str
    user_id: str
    preview: str


class StatsError(TypedDict):
    fingerprint: str
    kind: str
    location: str
    message: str
    traceback: str
    first_seen: str
    last_seen: str
    total: int
    groups: list[str]
    failures: list[StatsFailure]


class StatsData(TypedDict):
    days: StatsDays
    since: str
    total: StatsSummary | None
    trend: list[int]
    trend_unit: Literal["hour", "day"]
    plugins: list[StatsPlugin]
    idle: list[str]
    errors: list[StatsError]


class StatsReplayResult(TypedDict):
    chat: SandboxChat


# ---------------------------------------------------------------- 插件市场与已安装插件

PluginTier = TypeAliasType("PluginTier", Literal["official", "listed"])
PluginTrust = TypeAliasType("PluginTrust", Literal["official", "listed", "unlisted"])


class MarketVersion(TypedDict):
    version: str
    requires_api: str | None
    problem: str | None
    """不能安装的原因；null 表示可安装。"""
    tested_on: str | None
    """目录最近一次实测所用的 Tenko 版本和时间；从未测过为 null。"""
    tested_at: str | None


class MarketPlugin(TypedDict):
    id: str
    name: str
    description: str
    authors: list[str]
    homepage: str | None
    license: str | None
    category: str | None
    """分类的显示名；旧目录没有分类时为 null。"""
    tags: list[str]
    tier: PluginTier
    icon_url: str | None
    source: str | None
    source_type: str
    latest: str | None
    """宿主可安装的最新版本；没有则看 problem。"""
    problem: str | None
    versions: list[MarketVersion]
    installed_version: str | None
    blocked: str | None


class MarketData(TypedDict):
    """目录不可用时只有 available / error / plugins。"""

    available: bool
    error: NotRequired[str]
    url: NotRequired[str | None]
    host_api: NotRequired[str]
    categories: NotRequired[list[str]]
    """目录定义的分类显示名，按展示顺序。"""
    plugins: list[MarketPlugin]


class InstalledPluginRecord(TypedDict):
    """``PluginManager`` 记录的已安装插件。"""

    plugin_id: str
    dist: str
    version: str
    source: str
    trust: PluginTrust
    requirement: str
    enabled: bool
    loaded: bool
    restart_required: bool
    installed_at: str
    updated_at: str


class InstalledPlugin(InstalledPluginRecord):
    name: str | None
    icon_url: str | None
    update: str | None
    """目录里可更新到的版本。"""
    yanked: str | None
    """当前版本被撤回的原因。"""
    blocked: str | None
    """被目录封禁的原因。"""


PluginTaskAction = TypeAliasType(
    "PluginTaskAction",
    Literal["install", "update", "resume", "uninstall", "rollback", "sync"],
)
PluginTaskState = TypeAliasType(
    "PluginTaskState", Literal["running", "succeeded", "failed"]
)


class PluginTaskBody(TypedDict):
    action: PluginTaskAction
    plugin_id: NotRequired[str]
    version: NotRequired[str]
    clear_data: NotRequired[bool]
    confirm: Literal[True]


PluginChange = TypedDict(
    "PluginChange",
    {
        "plugin_id": str,
        "dist": str,
        "from": str | None,
        "to": str | None,
    },
)


class PluginTask(TypedDict):
    id: str
    action: PluginTaskAction
    plugin_id: str | None
    state: PluginTaskState
    message: str
    changes: list[PluginChange]
    restored: bool | None
    """失败时环境是否已恢复；成功或未判断时为 null。"""
    live: dict[str, str | None]
    """更新和恢复：每个插件仍需重启的原因，null 表示已在运行中生效。"""
    restart_required: bool
    """这次变更是否要重启才能生效。"""
    started_at: float
    finished_at: float | None


class SuspendedPlugin(TypedDict):
    """因与宿主不兼容而停用的插件：包已移出环境，记录、数据和配置保留。"""

    plugin_id: str
    name: str | None
    icon_url: str | None
    dist: str
    version: str
    """停用前的版本。"""
    trust: PluginTrust
    enabled: bool
    """停用前是否启用；恢复后沿用。"""
    reason: str
    suspended_at: str | None
    resume: str | None
    """已选定、下次启动自动装回的来源。"""
    error: str | None
    """上次自动恢复失败的原因。"""
    options: list[str]
    """目录里能装在当前宿主上的版本（新到旧）；未收录的插件为空，按原来源恢复。"""


class PluginFailure(TypedDict):
    """本次启动加载失败的插件；失败只跳过该插件。"""

    plugin_id: str
    name: str | None
    builtin: bool
    reason: str


class InstalledData(TypedDict):
    plugins: list[InstalledPlugin]
    suspended: list[SuspendedPlugin]
    failures: list[PluginFailure]
    drift: list[str]
    """环境与插件锁不一致的包。"""
    rollback_available: bool
    restart_required: bool
    can_restart: bool
    task: PluginTask | None


# ---------------------------------------------------------------- 插件详情

# 友好的链接分类，前端据此选图标。
PluginLinkKind = TypeAliasType(
    "PluginLinkKind", Literal["home", "repo", "pypi", "link"]
)


class PluginLink(TypedDict):
    kind: PluginLinkKind
    label: str
    url: str


class PluginCommandField(TypedDict):
    label: str
    value: str


class PluginCommandDoc(TypedDict):
    title: str
    """带前缀的命令名，如 /帮助。"""
    fields: list[PluginCommandField]
    """说明、用法、示例、选项、权限；与群里 /帮助 编号 的内容一致。"""


class PluginDetail(TypedDict):
    """插件详情页的说明资料；状态和开关仍以 FeatureInfo 为准。"""

    plugin: str
    name: str
    source: FeatureSource
    local: bool
    description: str
    version: str | None
    """内置插件为 Tenko 版本，扩展插件为已安装版本。"""
    authors: list[str]
    license: str | None
    tier: str | None
    tags: list[str]
    icon: PluginIconRef
    links: list[PluginLink]
    commands: list[PluginCommandDoc]
    """插件未加载时为空。"""
    installed: InstalledPluginRecord | None
    has_config: bool
    update: str | None
    yanked: str | None
    blocked: str | None


# ---------------------------------------------------------------- 插件配置


class PluginConfigResponse(TypedDict):
    """插件配置表单。"""

    plugin_id: str
    available: bool
    schema: NotRequired[dict[str, object]]
    """pydantic 生成的 JSON Schema；面板只用到其中的 properties。"""
    values: NotRequired[dict[str, object]]
    editable: NotRequired[list[str]]
    read_only_reason: NotRequired[str | None]


class RestartResult(TypedDict):
    restarting: bool


# ---------------------------------------------------------------- 宿主升级 /manage/upgrade


class ReleaseNote(TypedDict):
    tag: str
    name: str
    published_at: str | None
    prerelease: bool
    url: str | None
    """GitHub 上的发布页。"""
    body: str
    """Markdown 原文。"""


class ReleaseNotesData(TypedDict):
    note: ReleaseNote | None


class UpgradeCandidate(TypedDict):
    tag: str
    version: str
    prerelease: bool
    source: str
    notes: str | None


class UpgradeCheck(TypedDict):
    """宿主升级页；时间均为 ISO 8601 UTC。"""

    checked_at: str
    status: str
    current_version: str
    channel: str
    candidate: UpgradeCandidate | None
    changelog: NotRequired[list[ReleaseNote]]
    """当前版本之后到候选版本的各版发布说明（新到旧），取自 GitHub Releases。"""
    changelog_error: NotRequired[str | None]
    """读取发布说明失败的原因；不影响检查结果。"""


UpgradeAction = TypeAliasType("UpgradeAction", Literal["upgrade", "rollback"])
UpgradeJobState = TypeAliasType(
    "UpgradeJobState", Literal["running", "restarting", "succeeded", "failed"]
)


class UpgradeJob(TypedDict):
    action: UpgradeAction
    state: UpgradeJobState
    stage: str
    message: str
    target: str | None
    incompatible: dict[str, str]
    """与目标版本不兼容的插件及原因；"*" 表示插件组合冲突。"""
    options: dict[str, list[str]]
    """不兼容插件在目标版本上可装的目录版本（新到旧）；空数组表示只能停用。"""


UpgradeHistoryEntry = TypedDict(
    "UpgradeHistoryEntry",
    {
        "timestamp": str | None,
        "action": Literal["install", "rollback", "startup", "download", "restart"],
        "result": str,
        "from": str | None,
        "to": str | None,
        "automatic": bool,
        "error": str | None,
    },
)


class UpgradeStatus(TypedDict):
    enabled: bool
    current_version: str
    channel: Literal["stable", "prerelease"]
    policy: Literal["check", "download", "install"]
    check_interval_hours: int
    previous_version: str | None
    release_url: str | None
    """当前版本在 GitHub 上的发布页；升级源不在 GitHub 上时为 null。"""
    last_check: UpgradeCheck | None
    job: UpgradeJob | None
    history: list[UpgradeHistoryEntry]


class UpgradeStartBody(TypedDict):
    action: UpgradeAction
    confirm: Literal[True]
    plugins: NotRequired[dict[str, str]]
    """每个不兼容插件的处理："suspend" 或目标版本上要装的版本号。"""
