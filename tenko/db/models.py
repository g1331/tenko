"""Tenko SQLAlchemy 模型。"""

from __future__ import annotations

try:
    from entari_plugin_database import BaseOrm, Mapped, mapped_column
except (AttributeError, ImportError, LookupError) as error:  # pragma: no cover
    from .errors import DatabaseUnavailableError

    raise DatabaseUnavailableError(
        "官方 entari-plugin-database 尚未加载，无法注册 Tenko 数据模型"
    ) from error

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from .migration import LEGACY_SCHEMA_REVISION


class MemberPerm(BaseOrm):
    """已废弃（#234）：旧成员权限，不再读写；类保留是为了不触发删表。"""

    __tablename__ = "MemberPerm"
    __revision__ = LEGACY_SCHEMA_REVISION

    group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    qq: Mapped[int] = mapped_column(Integer, primary_key=True)
    perm: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        info={"check": [-1, 0, 16, 32, 64, 128, 256]},
        default=16,
    )


class GroupPerm(BaseOrm):
    """已废弃（#234）：旧群等级，不再读写；类保留是为了不触发删表。"""

    __tablename__ = "GroupPerm"
    __revision__ = LEGACY_SCHEMA_REVISION

    group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_name: Mapped[str] = mapped_column(String(length=60), nullable=False)
    perm: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        info={"check": [0, 1, 2, 3]},
        default=1,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=True, default=True)


class GroupSetting(BaseOrm):
    """群级频率限制开关；没有记录的群按开启处理。"""

    __tablename__ = "TenkoGroupSetting"

    platform: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    frequency_limitation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )


class FeatureState(BaseOrm):
    """Tenko 功能开关的全局或群级显式状态；全局行的平台和群 ID 都是空串。"""

    __tablename__ = "TenkoFeatureSwitch"

    plugin_name: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    platform: Mapped[str] = mapped_column(
        String(length=128), primary_key=True, default=""
    )
    group_id: Mapped[str] = mapped_column(
        String(length=128), primary_key=True, default=""
    )
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AccountRoute(BaseOrm):
    """账号与群的有序绑定关系。"""

    __tablename__ = "TenkoGroupRoute"
    __table_args__ = (
        UniqueConstraint(
            "platform", "group_id", "position", name="uq_tenko_group_route_position"
        ),
    )

    platform: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class AccountResponseState(BaseOrm):
    """群级 random/deterministic 响应策略和指定账号。"""

    __tablename__ = "TenkoGroupResponse"

    platform: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    response_type: Mapped[str] = mapped_column(
        String(length=32), nullable=False, default="random"
    )
    deterministic_account: Mapped[str | None] = mapped_column(
        String(length=128), nullable=True
    )


class RateLimitEvent(BaseOrm):
    """限流滚动窗口中的一次加权命令事件。"""

    __tablename__ = "TenkoRateEvent"
    __table_args__ = (
        Index(
            "ix_tenko_rate_event_subject",
            "platform",
            "group_id",
            "user_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(length=128), nullable=False)
    group_id: Mapped[str] = mapped_column(String(length=128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(length=128), nullable=False)
    occurred_at: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)


class RateLimitSubjectState(BaseOrm):
    """用户×群的限流冷却和临时黑名单到期时间。"""

    __tablename__ = "TenkoRateSubject"

    platform: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    cooldown_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    blacklist_until: Mapped[float | None] = mapped_column(Float, nullable=True)


# 以下是只按群号存储的旧表，#234 起由上面带平台主键的新表取代、不再读写。
# 删表会被“只前进”保护拒绝并在每次启动时报错，所以模型原样保留。


class LegacyGroupSetting(BaseOrm):
    """已废弃（#234）。"""

    __tablename__ = "GroupSetting"
    __revision__ = LEGACY_SCHEMA_REVISION

    group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    frequency_limitation: Mapped[bool] = mapped_column(
        Boolean, nullable=True, default=True
    )
    response_type: Mapped[str] = mapped_column(
        String,
        nullable=True,
        info={"check": ["random", "deterministic"]},
        default="random",
    )
    permission_type: Mapped[str] = mapped_column(
        String,
        nullable=True,
        info={"check": ["default", "admin"]},
        default="default",
    )


class LegacyFeatureState(BaseOrm):
    """已废弃（#234）。"""

    __tablename__ = "TenkoFeatureState"

    plugin_name: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        String(length=128), primary_key=True, default=""
    )
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class LegacyAccountRoute(BaseOrm):
    """已废弃（#234）。"""

    __tablename__ = "TenkoAccountRoute"
    __table_args__ = (
        UniqueConstraint("group_id", "position", name="uq_tenko_route_position"),
    )

    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class LegacyAccountResponseState(BaseOrm):
    """已废弃（#234）。"""

    __tablename__ = "TenkoAccountResponseState"

    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    response_type: Mapped[str] = mapped_column(
        String(length=32), nullable=False, default="random"
    )
    deterministic_account: Mapped[str | None] = mapped_column(
        String(length=128), nullable=True
    )


class LegacyRateLimitEvent(BaseOrm):
    """已废弃（#234）。"""

    __tablename__ = "TenkoRateLimitEvent"
    __table_args__ = (
        Index(
            "ix_tenko_rate_event_subject_time",
            "group_id",
            "user_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(length=128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(length=128), nullable=False)
    occurred_at: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)


class LegacyRateLimitSubjectState(BaseOrm):
    """已废弃（#234）。"""

    __tablename__ = "TenkoRateLimitSubjectState"

    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    cooldown_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    blacklist_until: Mapped[float | None] = mapped_column(Float, nullable=True)


class StartupTime(BaseOrm):
    """一次启动完成所记录的耗时样本。"""

    __tablename__ = "TenkoStartupTime"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    duration: Mapped[float] = mapped_column(Float, nullable=False)


class AccountPreference(BaseOrm):
    """平台限定的账户管理偏好；不存协议凭据。"""

    __tablename__ = "TenkoAccountPreference"

    platform: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    alias: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capabilities: Mapped[str] = mapped_column(String, nullable=False, default="{}")


class WebUISetting(BaseOrm):
    """面板和命令写入的运行期设置，与连接配置分开保存。"""

    # 旧表名 TenkoWebUISetting；官方迁移器按类名识别改名，所以类名不能改。
    __tablename__ = "TenkoSetting"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class WebUIAccount(BaseOrm):
    """管理面板唯一账号；``id`` 固定为 1，密码只保存 scrypt 哈希。"""

    __tablename__ = "TenkoWebUIAccount"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WebUISession(BaseOrm):
    """面板登录会话；只保存 cookie 令牌的 sha256，不保存令牌本身。"""

    __tablename__ = "TenkoWebUISession"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class UserBlacklist(BaseOrm):
    """全局黑名单：名单里的人在任何群和私聊都得不到响应。"""

    __tablename__ = "TenkoUserBlacklist"

    platform: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class GroupBlacklist(BaseOrm):
    """群黑名单：名单里的群完全不响应。"""

    __tablename__ = "TenkoGroupBlacklist"

    platform: Mapped[str] = mapped_column(String(128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PermissionGrant(BaseOrm):
    """手动授予的权限：``scope`` 为空串是全局 BotAdmin，否则是该群的群管理。"""

    __tablename__ = "TenkoPermissionGrant"

    platform: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)


class ChatMessage(BaseOrm):
    """面板聊天页的消息记录：某个 bot 账号在群聊或私聊里收到、发出的一条消息。

    每个账号各存一份：同一条群消息的 message_id 在各账号的协议端里并不相同，
    引用回复也必须用发送账号自己的 ID。
    """

    __tablename__ = "TenkoChatMessage"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "account_id",
            "channel_id",
            "message_id",
            name="uq_tenko_chat_message",
        ),
        Index("ix_tenko_chat_conversation", "platform", "account_id", "channel_id"),
        Index("ix_tenko_chat_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(160), nullable=False)
    channel_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outgoing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # 归一化后的消息段 JSON，见 tenko.host.chat_log.segments。
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class CommandStat(BaseOrm):
    """插件调用统计：按整点、插件、处理函数、群和结果汇总，不记参数和用户。"""

    __tablename__ = "TenkoCommandStat"

    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    plugin: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 命令名，或被动监听函数名。
    handler: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 用到的子命令和选项名，空格分隔。
    subcommand: Mapped[str] = mapped_column(String(128), primary_key=True)
    platform: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 私聊为空串。
    group_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # ok / error / denied:<原因>
    outcome: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    render_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # JSON：动作名 -> 次数。
    actions: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


MODEL_CLASSES = (
    MemberPerm,
    GroupPerm,
    GroupSetting,
    FeatureState,
    AccountRoute,
    AccountResponseState,
    RateLimitEvent,
    RateLimitSubjectState,
    StartupTime,
    AccountPreference,
    WebUISetting,
    WebUIAccount,
    WebUISession,
    ChatMessage,
    UserBlacklist,
    GroupBlacklist,
    PermissionGrant,
    CommandStat,
    LegacyGroupSetting,
    LegacyFeatureState,
    LegacyAccountRoute,
    LegacyAccountResponseState,
    LegacyRateLimitEvent,
    LegacyRateLimitSubjectState,
)

__all__ = [
    "ChatMessage",
    "CommandStat",
    "GroupBlacklist",
    "GroupPerm",
    "GroupSetting",
    "PermissionGrant",
    "UserBlacklist",
    "FeatureState",
    "AccountRoute",
    "AccountResponseState",
    "RateLimitEvent",
    "RateLimitSubjectState",
    "StartupTime",
    "MODEL_CLASSES",
    "AccountPreference",
    "WebUISetting",
    "WebUIAccount",
    "WebUISession",
    "MemberPerm",
    "LegacyGroupSetting",
    "LegacyFeatureState",
    "LegacyAccountRoute",
    "LegacyAccountResponseState",
    "LegacyRateLimitEvent",
    "LegacyRateLimitSubjectState",
]
